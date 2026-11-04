import { context, propagation, trace } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createSpanProcessor } from '@/runtime/processor';
import { withinBudget } from '@/runtime/lifecycle';
import { state } from '@/runtime/state';

beforeEach(() => {
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  for (const key of Object.keys(process.env))
    if (key.startsWith('OTEL_') || key === 'CONFIDENT_API_KEY')
      vi.stubEnv(key, undefined);
});
afterEach(async () => {
  const { shutdown } = await import('@/runtime/init');
  await shutdown(1000);
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
it('exports standard spans, preserves async parentage and merges resources', async () => {
  vi.stubEnv('OTEL_RESOURCE_ATTRIBUTES', 'service.name=environment,custom=env');
  const exporter = new InMemorySpanExporter();
  const { init, flush, shutdown } = await import('@/runtime/init');
  const runtime = init({
    exporter,
    resourceAttributes: { 'service.name': 'explicit' },
  });
  expect(init()).toBe(runtime);
  await runtime.getTracer().startActiveSpan('parent', async (parent) => {
    await Promise.resolve();
    const child = trace.getTracer('third-party').startSpan('child', {
      attributes: { 'gen_ai.operation.name': 'chat' },
    });
    child.end();
    parent.end();
  });
  expect(await flush()).toBe(true);
  const [child, parent] = exporter.getFinishedSpans();
  expect(child?.parentSpanContext?.spanId).toBe(parent?.spanContext().spanId);
  expect(parent?.resource.attributes).toMatchObject({
    'service.name': 'explicit',
    custom: 'env',
  });
  expect(parent?.instrumentationScope.schemaUrl).toBe(
    'https://opentelemetry.io/schemas/1.37.0',
  );
  expect(await shutdown()).toBe(true);
  expect(runtime.active).toBe(false);
  expect(init()).toBe(runtime);
  expect(runtime.getTracer().startSpan('after shutdown').isRecording()).toBe(
    false,
  );
});
it('runs extra span processors and shuts them down with the runtime', async () => {
  const started: string[] = [];
  const ended: string[] = [];
  let closed = false;
  const { init, shutdown } = await import('@/runtime/init');
  const runtime = init({
    exporter: new InMemorySpanExporter(),
    spanProcessors: [
      {
        onStart: (span) => started.push(span.name),
        onEnd: (span) => ended.push(span.name),
        forceFlush: async () => {},
        shutdown: async () => {
          closed = true;
        },
      },
    ],
  });
  runtime.getTracer().startSpan('work').end();
  trace.getTracer('third-party').startSpan('plain').end();
  expect([started, ended]).toEqual([
    ['work', 'plain'],
    ['work', 'plain'],
  ]);
  expect(await shutdown()).toBe(true);
  expect(closed).toBe(true);
});
it('preserves isolation between concurrent asynchronous roots', async () => {
  const exporter = new InMemorySpanExporter();
  const { init } = await import('@/runtime/init');
  const runtime = init({ exporter });
  await Promise.all(
    ['a', 'b'].map((name) =>
      runtime.getTracer().startActiveSpan(name, async (parent) => {
        await new Promise((resolve) => setTimeout(resolve, 1));
        runtime.getTracer().startSpan(`${name}-child`).end();
        parent.end();
      }),
    ),
  );
  await runtime.flush();
  const spans = exporter.getFinishedSpans();
  for (const name of ['a', 'b'])
    expect(
      spans.find((span) => span.name === `${name}-child`)?.parentSpanContext
        ?.spanId,
    ).toBe(spans.find((span) => span.name === name)?.spanContext().spanId);
});
it('factory works with an application-owned provider and package shutdown leaves it alone', async () => {
  const exporter = new InMemorySpanExporter();
  const other = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [
      createSpanProcessor({ exporter }),
      createSpanProcessor({ exporter: other }),
    ],
  });
  provider.register();
  const { shutdown } = await import('@/runtime/init');
  await shutdown();
  provider
    .getTracer('application')
    .startSpan('still alive', {
      attributes: { 'gen_ai.operation.name': 'chat' },
    })
    .end();
  await provider.forceFlush();
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  expect(other.getFinishedSpans()).toHaveLength(1);
  await provider.shutdown();
});
it('registration conflict leaves the existing provider and cleans up the unused exporter', async () => {
  const provider = new NodeTracerProvider();
  provider.register();
  const originalGlobal = trace.getTracerProvider();
  const exporter = new InMemorySpanExporter();
  const close = vi.spyOn(exporter, 'shutdown');
  const { init } = await import('@/runtime/init');
  expect(init({ exporter }).active).toBe(false);
  await vi.waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(trace.getTracerProvider()).toBe(originalGlobal);
  await provider.shutdown();
});
it('disabled and failed initialization do not consume injected exporters', async () => {
  const exporter = new InMemorySpanExporter();
  const close = vi.spyOn(exporter, 'shutdown');
  const { init } = await import('@/runtime/init');
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  expect(init({ exporter }).active).toBe(false);
  await createSpanProcessor({ exporter }).shutdown();
  vi.stubEnv('OTEL_SDK_DISABLED', undefined);
  expect(init({ exporter, maxContentBytes: 1 }).active).toBe(false);
  expect(close).not.toHaveBeenCalled();
});
it('a processor with an injected exporter forgets the previous OTLP/HTTP endpoint', async () => {
  await createSpanProcessor({
    endpoint: 'http://127.0.0.1:4318/v1/traces',
    apiKey: 'key',
  }).shutdown();
  expect(state.otlpHttpExport?.endpoint).toBe(
    'http://127.0.0.1:4318/v1/traces',
  );
  await createSpanProcessor({
    exporter: new InMemorySpanExporter(),
  }).shutdown();
  expect(state.otlpHttpExport).toBeUndefined();
});
it('bounds waiting and consumes synchronous/asynchronous failures', async () => {
  expect(await withinBudget(() => new Promise(() => {}), 5)).toBe(false);
  expect(
    await withinBudget(() => {
      throw new Error('failure');
    }, 100),
  ).toBe(false);
  expect(
    await withinBudget(() => Promise.reject(new Error('failure')), 100),
  ).toBe(false);
  expect(await withinBudget(() => Promise.resolve(), -1)).toBe(false);
});
it('shutdown closes the gate immediately, is idempotent, and cleanup can finish after timeout', async () => {
  let finish!: () => void;
  const exporter = new InMemorySpanExporter();
  const close = vi.spyOn(exporter, 'shutdown').mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const { init } = await import('@/runtime/init');
  const runtime = init({ exporter });
  expect(await runtime.shutdown(5)).toBe(false);
  expect(runtime.active).toBe(false);
  finish();
  expect(await runtime.shutdown()).toBe(true);
  expect(close).toHaveBeenCalledOnce();
});
it('flushes pending spans when the event loop empties, until shutdown', async () => {
  const exporter = new InMemorySpanExporter();
  const before = process.listeners('beforeExit');
  const { init } = await import('@/runtime/init');
  const runtime = init({ exporter });
  const added = process
    .listeners('beforeExit')
    .filter((listener) => !before.includes(listener));
  expect(added).toHaveLength(1);
  runtime.getTracer().startSpan('pending').end();
  expect(exporter.getFinishedSpans()).toHaveLength(0);
  added[0]!(0);
  await vi.waitFor(() => expect(exporter.getFinishedSpans()).toHaveLength(1));
  expect(await runtime.shutdown()).toBe(true);
  expect(process.listeners('beforeExit')).toEqual(before);
});
it("doesn't export an SDK's own span for a provider call it records", async () => {
  const exporter = new InMemorySpanExporter();
  const { init, flush } = await import('@/runtime/init');
  const { instrument } = await import('@/integrations/instrument');
  init({ exporter, instrumentations: [] });
  // Anthropic SDK 1.x traces each API call itself, which repeats our span.
  const sdkSpan = (scope: string) =>
    trace
      .getTracer(scope)
      .startSpan('anthropic.messages.create', {
        attributes: { 'gen_ai.operation.name': 'chat' },
      })
      .end();
  const messages = {
    create: async () => {
      sdkSpan('com.anthropic.sdk.typescript');
      sdkSpan('com.anthropic.sdk.typescript.bedrock');
      return { id: 'msg-1', type: 'message', content: [] };
    },
  };
  instrument([[messages, 'create']], 'anthropic');
  await messages.create();
  sdkSpan('com.anthropic.sdk.typescript'); // A call we don't record still exports.
  expect(await flush()).toBe(true);
  expect(
    exporter.getFinishedSpans().map((s) => s.instrumentationScope.name),
  ).toEqual(['confident-trace', 'com.anthropic.sdk.typescript']);
});
