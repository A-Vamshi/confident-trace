"""One process per scenario: LiveKit's module-level tracer never escapes it."""

import json

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

USAGE = {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132}


def sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def chunk(delta, finish=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def fake_openai(request):
    messages = json.loads(request.content)["messages"]
    usage = {**chunk({}), "choices": [], "usage": USAGE}
    if not any(m.get("role") == "tool" for m in messages):
        call = {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup_weather", "arguments": '{"city": "Paris"}'},
        }
        return sse(
            [
                chunk({"role": "assistant", "tool_calls": [call]}),
                chunk({}, "tool_calls"),
                usage,
            ]
        )
    return sse(
        [
            chunk({"role": "assistant", "content": "It is sunny."}),
            chunk({}, "stop"),
            usage,
        ]
    )


async def converse():
    import openai as openai_sdk
    from livekit.agents import Agent, AgentSession, function_tool
    from livekit.plugins import openai

    class Assistant(Agent):
        def __init__(self):
            super().__init__(instructions="You are a helpful voice assistant.")

        @function_tool
        async def lookup_weather(self, city: str) -> str:
            """Look up the weather for a city."""
            return f"Sunny in {city}"

    client = openai_sdk.AsyncOpenAI(
        api_key="offline",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
    )
    llm = openai.LLM(model="gpt-4.1-mini", client=client)
    async with AgentSession(llm=llm) as session:
        await session.start(Assistant())
        await session.run(user_input="What's the weather in Paris?")


def by_scope(captured, name):
    return [s for s in captured if s.instrumentation_scope.name == name]


@pytest.mark.asyncio
async def test_session_spans_are_labelled_and_llm_calls_appear_once():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit", "openai"))
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    livekit = by_scope(captured, "livekit-agents")
    names = {s.name for s in livekit}
    # Voice-only lifecycle spans carry no GenAI data but are still exported.
    assert {"agent_session", "on_enter", "llm_request_run"} <= names
    assert all(s.attributes["confident.span.integration"] == "LiveKit" for s in livekit)
    assert [s.name for s in livekit].count("llm_request") == 2
    assert by_scope(captured, "confident_trace") == []
    ids = {s.context.span_id for s in captured}
    assert all(s.parent is None or s.parent.span_id in ids for s in captured)


@pytest.mark.asyncio
async def test_unconfigured_livekit_tracer_uses_our_provider():
    from livekit.agents import telemetry

    provider = TracerProvider(shutdown_on_exit=False)
    ct.init(
        tracer_provider=provider,
        exporter=InMemorySpanExporter(),
        instrumentations=("livekit",),
    )
    assert telemetry.tracer._tracer_provider is provider


@pytest.mark.asyncio
async def test_configured_livekit_tracer_is_preserved():
    from livekit.agents import telemetry

    other = TracerProvider(shutdown_on_exit=False)
    elsewhere = InMemorySpanExporter()
    other.add_span_processor(SimpleSpanProcessor(elsewhere))
    telemetry.set_tracer_provider(other)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit", "openai"))
    assert telemetry.tracer._tracer_provider is other
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    # A LiveKit span on another provider does not stand our provider span down.
    assert by_scope(captured, "livekit-agents") == []
    assert len(by_scope(captured, "confident_trace")) == 2
    assert by_scope(elsewhere.get_finished_spans(), "livekit-agents")


@pytest.mark.asyncio
async def test_unselected_livekit_keeps_provider_spans():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("openai",))
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    assert len(by_scope(captured, "confident_trace")) == 2
    livekit = by_scope(captured, "livekit-agents")
    assert livekit
    assert all("confident.span.integration" not in s.attributes for s in livekit)
