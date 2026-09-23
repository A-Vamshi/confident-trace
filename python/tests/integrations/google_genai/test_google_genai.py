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
