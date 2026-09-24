/* global Response */
import assert from 'node:assert/strict';
import { init } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { ProxyTracerProvider } from '@opentelemetry/api';
import { GoogleGenAI } from '@google/genai';
import { JobContext, telemetry } from '@livekit/agents';

// Built entry points bundle separately; the provider wrapper must see the
// processor init() created to recognize LiveKit's model call.
const sink = new InMemorySpanExporter();
const rt = init({ exporter: sink });
assert.equal(rt.getInstrumentationStatus().integrations.livekit, 'enabled');
assert.ok(!(telemetry.tracer.getProvider() instanceof ProxyTracerProvider));

const google = new GoogleGenAI({ apiKey: 'test' });
globalThis.fetch = async () =>
  Response.json({
    candidates: [
      {
        content: { role: 'model', parts: [{ text: 'Hello' }] },
        finishReason: 'STOP',
      },
    ],
    usageMetadata: { promptTokenCount: 2, candidatesTokenCount: 1 },
  });
await telemetry.tracer.startActiveSpan(
  () =>
    telemetry.tracer.startActiveSpan(
      () =>
        google.models.generateContent({ model: 'gemini-test', contents: 'Hi' }),
      { name: 'llm_request_run' },
    ),
  { name: 'llm_request', attributes: { 'gen_ai.operation.name': 'chat' } },
);
await google.models.generateContent({ model: 'gemini-test', contents: 'Hi' });
await rt.flush();
const spans = sink.getFinishedSpans();
const livekit = spans.filter(
  (s) => s.instrumentationScope.name === 'livekit-agents',
);
assert.deepEqual(livekit.map((s) => s.name).sort(), [
  'llm_request',
  'llm_request_run',
]);
for (const span of livekit)
  assert.equal(span.attributes['confident.span.integration'], 'LiveKit');
// Only the direct call outside LiveKit keeps a Confident provider span.
assert.equal(
  spans.filter((s) => s.instrumentationScope.name === 'confident-trace').length,
  1,
);

const job = Object.assign(Object.create(JobContext.prototype), {
  connected: true,
  shutdownCallbacks: [],
});
await job.connect();
assert.equal(job.shutdownCallbacks.length, 1);
await rt.shutdown();
console.log('LiveKit automatic integration passed');
