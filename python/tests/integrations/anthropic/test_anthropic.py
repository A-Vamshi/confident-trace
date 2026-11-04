import json

import pytest
from conftest import enable, spans


def test_anthropic(telemetry):
    from anthropic import Anthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    body = {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }
    with Anthropic(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        result = client.messages.create(model="claude-test", messages=[], max_tokens=5)
    assert result.content[0].text == "hello"
    assert spans(exporter)[0].attributes["gen_ai.provider.name"] == "anthropic"


def test_sdk_call_span_is_not_exported_twice(telemetry):
    # Since 2.0 the SDK traces each API call itself, which repeats our span.
    from anthropic import Anthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    body = {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }

    def sdk_span(scope):
        telemetry[0].get_tracer(scope).start_span(
            "anthropic.messages.create", attributes={"gen_ai.operation.name": "chat"}
        ).end()

    def handle(request):
        sdk_span("com.anthropic.sdk.python")
        sdk_span("com.anthropic.sdk.python.bedrock")
        return httpx.Response(200, json=body)

    with Anthropic(
        api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handle))
    ) as client:
        client.messages.create(model="claude-test", messages=[], max_tokens=5)
    sdk_span("com.anthropic.sdk.python")  # A call we don't record still exports.
    assert [s.instrumentation_scope.name for s in spans(exporter)] == [
        "confident_trace",
        "com.anthropic.sdk.python",
    ]


@pytest.mark.asyncio
async def test_anthropic_raw_response(telemetry):
    # with_raw_response returns the HTTP response, which the caller parses.
    from anthropic import Anthropic, AsyncAnthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    body = {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }
    with Anthropic(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        raw = client.messages.with_raw_response.create(
            model="claude-test", messages=[], max_tokens=5
        )
        assert raw.parse().content[0].text == "hello"
    async with AsyncAnthropic(
        api_key="test",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        raw = await client.messages.with_raw_response.create(
            model="claude-test", messages=[], max_tokens=5
        )
        assert (await raw.parse()).content[0].text == "hello"
    captured = spans(exporter)
    assert len(captured) == 2
    for span in captured:
        assert span.attributes["gen_ai.usage.output_tokens"] == 1
        output = json.loads(span.attributes["gen_ai.output.messages"])
        assert output[0]["parts"][0]["content"] == "hello"


def anthropic_wire():
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [],
                    "usage": {"input_tokens": 2, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hello"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        (f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in events)
    )


def test_anthropic_stream_helper(telemetry):
    from anthropic import Anthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    with Anthropic(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text=anthropic_wire(),
                    headers={"content-type": "text/event-stream"},
                )
            )
        ),
    ) as client:
        with client.messages.stream(
            model="claude-test", messages=[], max_tokens=5
        ) as stream:
            assert list(stream.text_stream) == ["hello"]
            assert stream.get_final_message().content[0].text == "hello"
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["gen_ai.usage.output_tokens"] == 1
    assert "hello" in captured[0].attributes["gen_ai.output.messages"]


@pytest.mark.asyncio
async def test_anthropic_async_stream_helper(telemetry):
    from anthropic import AsyncAnthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    async with AsyncAnthropic(
        api_key="test",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text=anthropic_wire(),
                    headers={"content-type": "text/event-stream"},
                )
            )
        ),
    ) as client:
        async with client.messages.stream(
            model="claude-test", messages=[], max_tokens=5
        ) as stream:
            assert [text async for text in stream.text_stream] == ["hello"]
    assert len(spans(exporter)) == 1


@pytest.mark.asyncio
async def test_anthropic_async_create_stream(telemetry):
    from anthropic import AsyncAnthropic, _base_client

    transport_api = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    async with AsyncAnthropic(
        api_key="test",
        http_client=transport_api.AsyncClient(
            transport=transport_api.MockTransport(
                lambda r: transport_api.Response(
                    200,
                    text=anthropic_wire(),
                    headers={"content-type": "text/event-stream"},
                )
            )
        ),
    ) as client:
        stream = await client.messages.create(
            model="claude-test", messages=[], max_tokens=5, stream=True
        )
        events = [event async for event in stream]
        assert events[-1].type == "message_stop"
        await stream.close()
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["gen_ai.usage.output_tokens"] == 1
    output = json.loads(captured[0].attributes["gen_ai.output.messages"])
    assert output[0]["parts"][0]["content"] == "hello"
    assert output[0]["finish_reason"] == "stop"
