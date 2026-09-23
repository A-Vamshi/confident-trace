import asyncio

import httpx
import pytest
from conftest import spans
from opentelemetry import trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

import confident_trace as ct


def application(provider):
    tracer = provider.get_tracer("agent-framework")
    client = provider.get_tracer("opentelemetry.instrumentation.httpx")

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        if scope["path"] == "/api/v1/chat":
            with tracer.start_as_current_span("invocation"):
                with tracer.start_as_current_span(
                    "generate_content gemini-test",
                    attributes={"gen_ai.operation.name": "generate_content"},
                ):
                    with client.start_as_current_span("GET"):
                        pass
            for chunk in (b"a", b"b", b"c"):
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
                await asyncio.sleep(0)
        elif scope["path"] == "/api/v1/profile":
            ct.update_trace(user_id="user-1")
        await send({"type": "http.response.body", "body": b""})

    return OpenTelemetryMiddleware(app, tracer_provider=provider)


def witness(provider):
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


async def call(provider, *requests):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(provider)),
        base_url="http://test",
    ) as client:
        return await asyncio.gather(
            *(client.request(method, path) for method, path in requests)
        )


def server(captured, path):
    return next(
        s
        for s in captured
        if s.kind == SpanKind.SERVER
        and path
        in (s.attributes.get("url.path") or s.attributes.get("http.target") or s.name)
    )


@pytest.mark.asyncio
async def test_streaming_asgi_request_exports_only_ai_lineage(telemetry):
    provider, exporter = telemetry
    everything = witness(provider)

    await call(provider, ("POST", "/api/v1/chat"), ("GET", "/api/v1/threads"))

    exported = spans(exporter)
    all_spans = everything.get_finished_spans()
    chat = server(all_spans, "/api/v1/chat")
    assert {s.name for s in exported} == {
        "generate_content gemini-test",
        "invocation",
        chat.name,
    }
    assert {s.context.trace_id for s in exported} == {chat.context.trace_id}
    # The application's own pipeline still receives every span.
    assert any("http send" in s.name for s in all_spans)
    assert len(all_spans) > len(exported)
    # Exported spans keep their real parents.
    by_id = {s.context.span_id: s for s in exported}
    for span in exported:
        if span.parent is not None:
            assert span.parent.span_id in by_id


@pytest.mark.asyncio
async def test_concurrent_requests_keep_only_ai_lineage(telemetry):
    provider, exporter = telemetry
    everything = witness(provider)

    await call(
        provider,
        *[("POST", "/api/v1/chat"), ("GET", "/api/v1/threads")] * 3,
    )

    exported = spans(exporter)
    traces = {s.context.trace_id for s in exported}
    chats = [
        s
        for s in everything.get_finished_spans()
        if s.kind == SpanKind.SERVER and s.context.trace_id in traces
    ]
    assert len(chats) == 3
    assert sorted(s.name for s in exported) == sorted(
        ["generate_content gemini-test", "invocation", chats[0].name] * 3
    )


@pytest.mark.asyncio
async def test_trace_fields_on_a_request_span_are_kept(telemetry):
    provider, exporter = telemetry

    await call(provider, ("GET", "/api/v1/profile"))

    (exported,) = spans(exporter)
    assert exported.kind == SpanKind.SERVER
    assert exported.attributes["confident.trace.user_id"] == "user-1"


@pytest.mark.asyncio
async def test_export_all_spans_restores_pass_through(telemetry):
    provider, _ = telemetry
    everything = witness(provider)
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=(),
        export_all_spans=True,
    )

    await call(provider, ("POST", "/api/v1/chat"), ("GET", "/api/v1/threads"))

    assert len(spans(exporter)) == len(everything.get_finished_spans())


def routing():
    from confident_trace._core import runtime

    return runtime.current().processor.delegate


def test_bookkeeping_is_released_and_dropped_spans_stay_clean(telemetry):
    provider, exporter = telemetry
    tracer = provider.get_tracer("web-framework")
    router = routing()

    with tracer.start_as_current_span("request"):
        with tracer.start_as_current_span("http send"):
            pass
    assert router.default.dirty is False

    with tracer.start_as_current_span("request"):
        with tracer.start_as_current_span(
            "chat", attributes={"gen_ai.operation.name": "chat"}
        ):
            pass

    assert [s.name for s in spans(exporter)] == ["chat", "request"]
    assert router.spans == {} and router.parents == {} and router.retained == set()
    assert router.default.active == 0


def test_export_all_spans_keeps_no_ancestry_state(telemetry):
    provider, _ = telemetry
    ct.shutdown()
    ct.init(
        tracer_provider=provider,
        exporter=InMemorySpanExporter(),
        instrumentations=(),
        export_all_spans=True,
    )
    tracer = provider.get_tracer("web-framework")
    router = routing()
    with tracer.start_as_current_span("request"):
        assert router.parents == {}


def test_suppressed_parent_is_never_retained(telemetry):
    provider, exporter = telemetry
    tracer = provider.get_tracer("web-framework")
    with ct.suppress_tracing():
        with tracer.start_as_current_span("request"):
            with tracer.start_as_current_span(
                "chat", attributes={"gen_ai.operation.name": "chat"}
            ):
                pass
    assert spans(exporter) == ()


def test_descendant_ending_after_its_ancestor_exports_alone(telemetry):
    provider, exporter = telemetry
    tracer = provider.get_tracer("web-framework")
    request = tracer.start_span("request")
    child = tracer.start_span(
        "background chat",
        context=trace.set_span_in_context(request),
        attributes={"gen_ai.operation.name": "chat"},
    )
    request.end()
    child.end()
    assert [s.name for s in spans(exporter)] == ["background chat"]


def test_shutdown_clears_ancestry_state(telemetry):
    provider, _ = telemetry
    tracer = provider.get_tracer("web-framework")
    router = routing()
    span = tracer.start_span("request")
    assert router.parents
    ct.shutdown()
    assert router.parents == {} and router.retained == set()
    span.end()
