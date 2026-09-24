import { ROOT_CONTEXT, trace } from '@opentelemetry/api';
import type { Span } from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { expect, it, vi } from 'vitest';
import { createSpanProcessor } from '@/runtime/processor';
import type { Ancestry } from '@/runtime/ancestry';

function setup() {
  const exporter = new InMemorySpanExporter();
  const witness = new InMemorySpanExporter();
  const processor = createSpanProcessor({ exporter });
  const provider = new NodeTracerProvider({
    spanProcessors: [processor, new SimpleSpanProcessor(witness)],
  });
  const state = (
    processor as unknown as { ancestry: Ancestry<{ active: number }> }
  ).ancestry;
  const start = (name: string, parent?: Span, ai = false, startTime?: number) =>
    provider.getTracer('test').startSpan(
      name,
      {
        attributes: ai ? { 'gen_ai.operation.name': 'chat' } : {},
        ...(startTime !== undefined ? { startTime } : {}),
      },
      parent ? trace.setSpan(ROOT_CONTEXT, parent) : ROOT_CONTEXT,
    );
  return { provider, exporter, witness, processor, state, start };
}

it.each([false, true])(
  'retains early-ending intermediates, late AI=%s',
  async (late) => {
    const s = setup();
    try {
      const a = s.start('A', undefined, true),
        middle = s.start('HTTP', a),
        b = s.start('B', middle, !late);
      middle.end();
      if (late) {
        expect(s.state.pending.size).toBe(1);
        b.setAttribute('gen_ai.operation.name', 'chat');
      }
      b.end();
      a.end();
      await s.provider.forceFlush();
      const rows = s.exporter.getFinishedSpans();
      expect(rows.map((x) => x.name).sort()).toEqual(['A', 'B', 'HTTP']);
      const originals = new Map(
        s.witness.getFinishedSpans().map((x) => [x.spanContext().spanId, x]),
      );
      for (const row of rows) {
        const original = originals.get(row.spanContext().spanId)!;
        expect(row.parentSpanContext).toEqual(original.parentSpanContext);
        expect(row.startTime).toEqual(original.startTime);
        expect(row.endTime).toEqual(original.endTime);
      }
      expect(s.state.nodes.size).toBe(0);
    } finally {
      await s.provider.shutdown();
    }
  },
);
it.each([3, 3000])(
  'streams %i spans over five hours without accumulating payloads',
  async (count) => {
    const s = setup();
    try {
      let now = 1_000_000;
      const root = s.start('agent', undefined, true, now);
      for (let i = 0; i < count; i++) {
        now = 1_000_000 + (i + 1) * ((5 * 3600 * 1000) / count);
        s.start('model', root, true, now).end(now + 1);
        if (i % 100 === 0) {
          await s.provider.forceFlush();
          expect(s.exporter.getFinishedSpans()).toHaveLength(i + 1);
        }
        expect(s.state.nodes.size).toBe(1);
        expect(s.state.pendingBytes).toBe(0);
      }
      root.end(now + 2);
      await s.provider.forceFlush();
      expect(s.exporter.getFinishedSpans()).toHaveLength(count + 1);
      expect(s.state.nodes.size).toBe(0);
    } finally {
      await s.provider.shutdown();
    }
  },
);
it('discards unresolved branches when descendants finish without AI', async () => {
  const s = setup();
  try {
    const a = s.start('root'),
      b = s.start('middle', a),
      c = s.start('child', b);
    a.end();
    b.end();
    expect(s.state.pending.size).toBe(2);
    c.end();
    await s.provider.forceFlush();
    expect(s.exporter.getFinishedSpans()).toHaveLength(0);
    expect(s.state.nodes.size).toBe(0);
    expect(s.state.pendingBytes).toBe(0);
  } finally {
    await s.provider.shutdown();
  }
});
it('bounds pending spans and exports the oldest', async () => {
  const s = setup();
  try {
    const children = [];
    for (let i = 0; i < 1025; i++) {
      const p = s.start('parent');
      children.push(s.start('child', p));
      p.end();
    }
    expect(s.state.pending.size).toBe(1024);
    for (const c of children) c.end();
    await s.provider.forceFlush();
    expect(s.exporter.getFinishedSpans()).toHaveLength(1);
    expect(s.state.nodes.size).toBe(0);
  } finally {
    await s.provider.shutdown();
  }
});
it('bounds bytes including event data and preserves ancestor links', async () => {
  const s = setup();
  try {
    const a = s.start('root'),
      b = s.start('HTTP', a),
      c = s.start('late', b);
    b.addEvent('large', { text: 'x'.repeat(9 * 1024 * 1024) });
    a.end();
    b.end();
    expect(s.state.pendingBytes).toBe(0);
    c.setAttribute('gen_ai.operation.name', 'chat');
    c.end();
    await s.provider.forceFlush();
    const rows = s.exporter.getFinishedSpans();
    expect(rows.map((x) => x.name).sort()).toEqual(['HTTP', 'late', 'root']);
    expect(new Set(rows.map((x) => x.spanContext().spanId)).size).toBe(3);
  } finally {
    await s.provider.shutdown();
  }
});
it.each([false, true])(
  'lifecycle forwards pending data without ending work, shutdown=%s',
  async (shutdown) => {
    const s = setup(),
      seen: string[] = [];
    const original = s.exporter.export.bind(s.exporter);
    vi.spyOn(s.exporter, 'export').mockImplementation((spans, callback) => {
      seen.push(...spans.map((x) => x.name));
      original(spans, callback);
    });
    try {
      const a = s.start('parent'),
        b = s.start('child', a);
      a.end();
      if (shutdown) await s.provider.shutdown();
      else await s.provider.forceFlush();
      expect(b.isRecording()).toBe(true);
      expect(seen).toEqual(['parent']);
      b.end();
      if (!shutdown) await s.provider.forceFlush();
      expect(seen).toEqual(['parent']);
      expect(s.state.nodes.size).toBe(0);
    } finally {
      if (!shutdown) await s.provider.shutdown();
    }
  },
);

it('keeps an unresolved ended parent across a five-hour descendant', async () => {
  const s = setup();
  try {
    const parent = s.start('HTTP', undefined, false, 1000);
    const child = s.start('late', parent, false, 2000);
    parent.end(3000);
    expect(s.state.pending.size).toBe(1);
    child.setAttribute('gen_ai.operation.name', 'chat');
    child.end(5 * 3600 * 1000);
    await s.provider.forceFlush();
    const rows = s.exporter.getFinishedSpans();
    expect(rows.map((row) => row.name)).toEqual(['HTTP', 'late']);
    expect(rows[1]!.parentSpanContext!.spanId).toBe(
      rows[0]!.spanContext().spanId,
    );
    expect(s.state.nodes.size).toBe(0);
  } finally {
    await s.provider.shutdown();
  }
});
