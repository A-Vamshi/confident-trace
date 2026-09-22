import type { Span } from '@opentelemetry/api';
import type { ContentPolicy } from '@/content/policy';
export interface ThreadFields {
  id?: string;
  tags?: readonly string[];
  metadata?: Record<string, unknown> | null;
}
export interface CustomerFields {
  id?: string;
  name?: string | null;
}
export interface UserFields {
  id?: string;
  name?: string | null;
}
export interface LlmFields {
  model?: string;
  provider?: string;
  inputTokenCount?: number;
  outputTokenCount?: number;
  /** USD per token, not per million tokens. */
  costPerInputToken?: number;
  costPerOutputToken?: number;
}
export const llmAttributes: Record<string, string> = {
  model: 'gen_ai.request.model',
  provider: 'gen_ai.provider.name',
  inputTokenCount: 'gen_ai.usage.input_tokens',
  outputTokenCount: 'gen_ai.usage.output_tokens',
  costPerInputToken: 'confident.llm.cost_per_input_token',
  costPerOutputToken: 'confident.llm.cost_per_output_token',
};
// Structured trace entities emit one dotted OTEL attribute per field.
export const entityConfig = {
  thread: {
    shorthand: 'threadId',
    keys: ['id', 'tags', 'metadata'],
  },
  customer: {
    shorthand: 'customerId',
    keys: ['id', 'name'],
  },
  user: {
    shorthand: 'userId',
    keys: ['id', 'name'],
  },
} as const;
export type EntityName = keyof typeof entityConfig;
export function validateFields(fields: object): void {
  const values = fields as Record<string, unknown>;
  for (const [entity, config] of Object.entries(entityConfig)) {
    const value = values[entity] as Record<string, unknown> | undefined;
    if (value === undefined) continue;
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      Object.keys(value).some(
        (k) => !(config.keys as readonly string[]).includes(k),
      )
    )
      throw new TypeError(`${entity} accepts ${config.keys.join(', ')}`);
    if (value.id !== undefined && typeof value.id !== 'string')
      throw new TypeError(`${entity}.id must be a string`);
    if (
      'name' in value &&
      value.name !== undefined &&
      value.name !== null &&
      typeof value.name !== 'string'
    )
      throw new TypeError(`${entity}.name must be a string`);
    if (
      value.id !== undefined &&
      values[config.shorthand] !== undefined &&
      value.id !== values[config.shorthand]
    )
      throw new TypeError(`Conflicting ${entity} IDs`);
  }
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || !llmAttributes[key]) continue;
    if (key === 'model' || key === 'provider') {
      if (typeof value !== 'string')
        throw new TypeError(key + ' must be a string');
    } else if (
      typeof value !== 'number' ||
      !Number.isFinite(value) ||
      value < 0 ||
      (key.endsWith('TokenCount') && !Number.isInteger(value))
    ) {
      throw new TypeError(
        key + ' must be nonnegative and finite; token counts must be integers',
      );
    }
  }
}
export function applyLlmFields(span: Span, fields: LlmFields): void {
  if (!span.isRecording()) return;
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && llmAttributes[key])
      span.setAttribute(llmAttributes[key]!, value);
  }
}
export function applyEntityFields(
  span: Span,
  fields: Record<string, unknown>,
  policy: ContentPolicy,
): void {
  if (!span.isRecording()) return;
  for (const [entity, config] of Object.entries(entityConfig) as [
    EntityName,
    (typeof entityConfig)[EntityName],
  ][]) {
    const value = fields[entity] as Record<string, unknown> | undefined | null;
    const shorthand = fields[config.shorthand];
    const id = typeof value?.id === 'string' ? value.id : shorthand;
    if (value === undefined && typeof id !== 'string') continue;
    if (typeof id === 'string') {
      span.setAttribute(`confident.trace.${entity}_id`, id.slice(0, 4096));
      span.setAttribute(`confident.trace.${entity}.id`, id.slice(0, 4096));
      if (entity === 'thread')
        span.setAttribute('gen_ai.conversation.id', id.slice(0, 4096));
    }
    if (value) {
      const tags = value.tags;
      if (
        entity === 'thread' &&
        Array.isArray(tags) &&
        tags.every((v) => typeof v === 'string')
      ) {
        span.setAttribute('confident.trace.thread.tags', tags.slice(0, 128));
      }
      if (entity === 'thread' && 'metadata' in value) {
        const encoded = policy.encode(value.metadata);
        if (encoded !== undefined)
          span.setAttribute('confident.trace.thread.metadata', encoded);
      }
      if (entity !== 'thread' && typeof value.name === 'string')
        span.setAttribute(
          `confident.trace.${entity}.name`,
          value.name.slice(0, 4096),
        );
    }
  }
}
