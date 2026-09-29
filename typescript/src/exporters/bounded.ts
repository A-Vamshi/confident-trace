import { ExportResultCode } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { AttributeValue } from '@opentelemetry/api';

export const MAX_EXPORT_BYTES = 24 * 1024 * 1024;
const SPAN_OVERHEAD = 1024;

function valueSize(value: AttributeValue | undefined): number {
  if (typeof value === 'string') return value.length;
  if (Array.isArray(value))
    return (
      value.reduce<number>(
        (total, item) => total + valueSize(item as AttributeValue),
        0,
      ) +
      2 * value.length
    );
  return 8;
}

export function spanSize(span: ReadableSpan): number {
  let total = SPAN_OVERHEAD + span.name.length;
  for (const [key, value] of Object.entries(span.attributes))
    total += key.length + valueSize(value);
  for (const event of span.events) {
    total += SPAN_OVERHEAD + event.name.length;
    for (const [key, value] of Object.entries(event.attributes ?? {}))
      total += key.length + valueSize(value);
  }
  return total;
}

export function batches(
  spans: ReadableSpan[],
  maxBytes: number,
): ReadableSpan[][] {
  const result: ReadableSpan[][] = [];
  let current: ReadableSpan[] = [];
  let used = 0;
  for (const span of spans) {
    const size = spanSize(span);
    if (current.length && used + size > maxBytes) {
      result.push(current);
      current = [];
      used = 0;
    }
    current.push(span);
    used += size;
  }
  if (current.length) result.push(current);
  return result;
}

export class BoundedSpanExporter implements SpanExporter {
  constructor(
    private readonly exporter: SpanExporter,
    private readonly maxBytes: number = MAX_EXPORT_BYTES,
  ) {}

  export(
    spans: ReadableSpan[],
    resultCallback: (result: ExportResult) => void,
  ): void {
    const chunks = batches(spans, this.maxBytes);
    if (!chunks.length) {
      resultCallback({ code: ExportResultCode.SUCCESS });
      return;
    }
    let pending = chunks.length;
    let failure: ExportResult | undefined;
    for (const chunk of chunks)
      this.exporter.export(chunk, (result) => {
        if (result.code !== ExportResultCode.SUCCESS) failure ??= result;
        if (--pending === 0)
          resultCallback(failure ?? { code: ExportResultCode.SUCCESS });
      });
  }

  forceFlush(): Promise<void> {
    return this.exporter.forceFlush?.() ?? Promise.resolve();
  }

  shutdown(): Promise<void> {
    return this.exporter.shutdown();
  }
}
