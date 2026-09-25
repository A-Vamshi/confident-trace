/* global Response */
import assert from 'node:assert/strict';
import { init } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { ProxyTracerProvider } from '@opentelemetry/api';
import { GoogleGenAI } from '@google/genai';
import { telemetry } from '@livekit/agents';

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

// Exercise the installed worker's actual concurrent-callback and final-log stages.
const { createRequire } = await import('node:module');
const { dirname, join } = await import('node:path');
const { pathToFileURL } = await import('node:url');
const require = createRequire(import.meta.url);
const lifecycle = await import(
  pathToFileURL(
    join(dirname(require.resolve('@livekit/agents')), 'job_lifecycle.js'),
  )
);
const logger = { error() {}, warn() {}, debug() {} };
await lifecycle.runShutdownCallbacks(
  [
    async () => {
      throw new Error('cleanup failure');
    },
    async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      telemetry.tracer.startSpan({ name: 'late_cleanup' }).end();
    },
  ],
  logger,
);
assert.ok(!sink.getFinishedSpans().some((s) => s.name === 'late_cleanup'));
await lifecycle.flushJobLogs(logger);
assert.ok(sink.getFinishedSpans().some((s) => s.name === 'late_cleanup'));
await rt.shutdown();
console.log('LiveKit automatic integration passed');
