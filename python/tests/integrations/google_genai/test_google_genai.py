import json

import httpx
import pytest
from conftest import enable, spans


def test_google_genai(telemetry):
    from google import genai
    from google.genai import types

    exporter = enable(telemetry, "google_genai")
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        "modelVersion": "gemini-test",
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(client_args={"transport": transport}),
    ) as client:
        result = client.models.generate_content(model="gemini-test", contents="hi")
    assert result.text == "hello"
    assert spans(exporter)[0].attributes["gen_ai.usage.input_tokens"] == 2


@pytest.mark.asyncio
async def test_google_async_stream(telemetry):
    from google import genai
    from google.genai import types

    exporter = enable(telemetry, "google_genai")
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            text="data: " + json.dumps(body) + "\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )
    async with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(async_client_args={"transport": transport}),
    ).aio as client:
        stream = await client.models.generate_content_stream(
            model="gemini-test", contents="hi"
        )
        assert [chunk.text async for chunk in stream] == ["hello"]
    assert len(spans(exporter)) == 1


class _Singleton:
    def __new__(cls, *args, **kwargs):
        raise AssertionError("the instrumentor must never be constructed")


def _google_instrumentation(monkeypatch, instance):
    import sys
    import types as pytypes

    module = pytypes.ModuleType("opentelemetry.instrumentation.google_genai")
    instrumentor = type("GoogleGenAiSdkInstrumentor", (_Singleton,), {})
    instrumentor._instance = instance
    module.GoogleGenAiSdkInstrumentor = instrumentor
    monkeypatch.setitem(sys.modules, module.__name__, module)


def _official_wrapper(monkeypatch, provider):
    import functools

    from google.genai import models

    tracer = provider.get_tracer("opentelemetry.util.genai.handler")
    original = models.Models.generate_content

    @functools.wraps(original)
    def generate_content(self, *args, **kwargs):
        with tracer.start_as_current_span(
            "generate_content gemini-test",
            attributes={"gen_ai.operation.name": "generate_content"},
        ):
            return original(self, *args, **kwargs)

    monkeypatch.setattr(models.Models, "generate_content", generate_content)


def _generate():
    from google import genai
    from google.genai import types

    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(client_args={"transport": transport}),
    ) as client:
        return client.models.generate_content(model="gemini-test", contents="hi")


@pytest.mark.parametrize("order", ["official-first", "confident-first"])
def test_steps_back_when_google_instrumentation_is_active(
    telemetry, monkeypatch, order
):
    import types as pytypes

    provider, _ = telemetry
    _google_instrumentation(
        monkeypatch, pytypes.SimpleNamespace(is_instrumented_by_opentelemetry=True)
    )
    if order == "official-first":
        _official_wrapper(monkeypatch, provider)
        exporter = enable(telemetry, "google_genai")
    else:
        exporter = enable(telemetry, "google_genai")
        _official_wrapper(monkeypatch, provider)

    assert _generate().text == "hello"
    (llm,) = spans(exporter)
    assert llm.instrumentation_scope.name == "opentelemetry.util.genai.handler"
    assert not any(key.startswith("confident.") for key in llm.attributes)


@pytest.mark.parametrize(
    "instance",
    [None, "inactive", "raising"],
)
def test_keeps_our_span_when_google_instrumentation_is_not_active(
    telemetry, monkeypatch, instance
):
    import types as pytypes

    class Raising:
        @property
        def is_instrumented_by_opentelemetry(self):
            raise RuntimeError("unavailable")

    _google_instrumentation(
        monkeypatch,
        {
            None: None,
            "inactive": pytypes.SimpleNamespace(is_instrumented_by_opentelemetry=False),
            "raising": Raising(),
        }[instance],
    )
    exporter = enable(telemetry, "google_genai")
    assert _generate().text == "hello"
    (llm,) = spans(exporter)
    assert llm.instrumentation_scope.name == "confident_trace"


@pytest.mark.parametrize("order", ["official-first", "confident-first"])
def test_keeps_our_span_when_enclosing_google_span_uses_another_provider(
    telemetry, monkeypatch, order
):
    import types as pytypes

    from opentelemetry.sdk.trace import TracerProvider

    _google_instrumentation(
        monkeypatch, pytypes.SimpleNamespace(is_instrumented_by_opentelemetry=True)
    )
    other = TracerProvider(shutdown_on_exit=False)
    if order == "official-first":
        _official_wrapper(monkeypatch, other)
        exporter = enable(telemetry, "google_genai")
    else:
        exporter = enable(telemetry, "google_genai")
        _official_wrapper(monkeypatch, other)

    assert _generate().text == "hello"
    (llm,) = spans(exporter)
    assert llm.instrumentation_scope.name == "confident_trace"


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["official-first", "confident-first"])
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("mode", ["sync", "async", "stream", "async-stream"])
async def test_native_ownership_all_call_modes(
    telemetry, monkeypatch, order, shared, mode
):
    import functools
    import types as pytypes

    from google import genai
    from google.genai import models, types
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider, _ = telemetry
    native_provider = provider if shared else TracerProvider(shutdown_on_exit=False)
    native_exporter = InMemorySpanExporter()
    from confident_trace._core.runtime import OwnedProcessor

    native_provider.add_span_processor(
        OwnedProcessor(SimpleSpanProcessor(native_exporter))
    )
    _google_instrumentation(
        monkeypatch, pytypes.SimpleNamespace(is_instrumented_by_opentelemetry=True)
    )
    asynchronous = mode in ("async", "async-stream")
    streaming = mode in ("stream", "async-stream")
    cls = models.AsyncModels if asynchronous else models.Models
    method = "generate_content_stream" if streaming else "generate_content"

    def install():
        original = getattr(cls, method)
        tracer = native_provider.get_tracer("opentelemetry.util.genai.handler")

        def span():
            return tracer.start_as_current_span(
                "native", attributes={"gen_ai.operation.name": "generate_content"}
            )

        if asynchronous:

            @functools.wraps(original)
            async def wrapped(self, *args, **kwargs):
                if streaming:

                    async def chunks():
                        with span():
                            stream = await original(self, *args, **kwargs)
                            async for chunk in stream:
                                yield chunk

                    return chunks()
                with span():
                    return await original(self, *args, **kwargs)
        else:

            @functools.wraps(original)
            def wrapped(self, *args, **kwargs):
                if streaming:

                    def chunks():
                        with span():
                            yield from original(self, *args, **kwargs)

                    return chunks()
                with span():
                    return original(self, *args, **kwargs)

        monkeypatch.setattr(cls, method, wrapped)

    if order == "official-first":
        install()
        exporter = enable(telemetry, "google_genai")
    else:
        exporter = enable(telemetry, "google_genai")
        install()
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }

    def respond(request):
        if streaming:
            return httpx.Response(
                200,
                text="data: " + json.dumps(body) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(respond)
    try:
        with genai.Client(
            api_key="test",
            http_options=types.HttpOptions(
                client_args={"transport": transport},
                async_client_args={"transport": transport},
            ),
        ) as client:
            api = client.aio.models if asynchronous else client.models
            result = getattr(api, method)(model="gemini-test", contents="hi")
            if asynchronous:
                result = await result
            if streaming:
                texts = (
                    [chunk.text async for chunk in result]
                    if asynchronous
                    else [chunk.text for chunk in result]
                )
                assert texts == ["hello"]
            else:
                assert result.text == "hello"
            await client.aio.aclose()
        (row,) = spans(exporter)
        assert row.instrumentation_scope.name == (
            "opentelemetry.util.genai.handler" if shared else "confident_trace"
        )
        assert row.attributes["gen_ai.operation.name"] == "generate_content"
        if not shared:
            assert row.attributes["gen_ai.usage.input_tokens"] == 2
            assert len(native_exporter.get_finished_spans()) == 1
    finally:
        if not shared:
            native_provider.shutdown()


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("order", ["official-first", "confident-first"])
def test_google_native_error_ownership(telemetry, monkeypatch, shared, order):
    import types as pytypes

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import StatusCode

    provider, _ = telemetry
    other = provider if shared else TracerProvider(shutdown_on_exit=False)
    _google_instrumentation(
        monkeypatch, pytypes.SimpleNamespace(is_instrumented_by_opentelemetry=True)
    )
    if order == "official-first":
        _official_wrapper(monkeypatch, other)
        exporter = enable(telemetry, "google_genai")
    else:
        exporter = enable(telemetry, "google_genai")
        _official_wrapper(monkeypatch, other)

    def failure(*args, **kwargs):
        raise RuntimeError("transport failed")

    monkeypatch.setattr(httpx.Client, "send", failure)
    try:
        with pytest.raises(RuntimeError):
            _generate()
        (row,) = spans(exporter)
        assert row.status.status_code == StatusCode.ERROR
        assert row.instrumentation_scope.name == (
            "opentelemetry.util.genai.handler" if shared else "confident_trace"
        )
    finally:
        if not shared:
            other.shutdown()
