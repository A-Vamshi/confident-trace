import { readFileSync } from 'node:fs';
import { ROOT_CONTEXT, trace, TraceFlags } from '@opentelemetry/api';
import type { Attributes, Span } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { expect, it } from 'vitest';
import { createSpanProcessor } from '@/runtime/processor';

interface SpanSpec {
  name: string;
  scope: string;
  parent?: string;
  attributes?: Attributes;
  events?: string[];
}
interface Case {
  name: string;
  spans: SpanSpec[];
  end: string[];
  exported: string[];
}
const vectors = JSON.parse(
  readFileSync(
    new URL('../../../spec/span-export-vectors.json', import.meta.url),
    'utf8',
  ),
) as { remote_parent: { trace_id: string; span_id: string }; cases: Case[] };

async function run(vector: Case, exportAllSpans: boolean): Promise<string[]> {
  const exporter = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [createSpanProcessor({ exporter, exportAllSpans })],
  });
  const remote = trace.setSpanContext(ROOT_CONTEXT, {
    traceId: vectors.remote_parent.trace_id,
    spanId: vectors.remote_parent.span_id,
    traceFlags: TraceFlags.SAMPLED,
    isRemote: true,
  });
  const started = new Map<string, Span>();
  const parentIds = new Map<string, string | undefined>();
  try {
    for (const spec of vector.spans) {
      const scope = spec.scope === '@sdk' ? 'confident-trace' : spec.scope;
      const parent =
        spec.parent === '@remote'
          ? remote
          : spec.parent
            ? trace.setSpan(ROOT_CONTEXT, started.get(spec.parent)!)
            : ROOT_CONTEXT;
      const span = provider
        .getTracer(scope)
        .startSpan(spec.name, { attributes: spec.attributes ?? {} }, parent);
      for (const event of spec.events ?? []) span.addEvent(event);
      started.set(spec.name, span);
      parentIds.set(spec.name, trace.getSpan(parent)?.spanContext().spanId);
    }
    for (const name of vector.end) started.get(name)!.end();
    await provider.forceFlush();
    const exported = exporter.getFinishedSpans();
    for (const span of exported)
      expect(span.parentSpanContext?.spanId).toBe(parentIds.get(span.name));
    return exported.map((span) => span.name);
  } finally {
    await provider.shutdown();
  }
}

it.each(vectors.cases.map((c) => [c.name, c] as const))(
  'exports only selected spans: %s',
  async (_name, vector) => {
    expect(await run(vector, false)).toEqual(vector.exported);
  },
);

it.each(vectors.cases.map((c) => [c.name, c] as const))(
  'exportAllSpans exports every ended span: %s',
  async (_name, vector) => {
    expect(await run(vector, true)).toEqual(vector.end);
  },
);
