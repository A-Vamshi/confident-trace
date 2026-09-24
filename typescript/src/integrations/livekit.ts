import { context, trace } from '@opentelemetry/api';
import type { Span } from '@opentelemetry/api';
import { isRoutedSpan } from '@/runtime/scopes';
import { state } from '@/runtime/state';

export const SCOPE = 'livekit-agents';
// LiveKit calls the model SDK inside this retry-attempt span; its parent,
// llm_request, carries the GenAI operation.
const INFERENCE_SPANS = new Set(['llm_request_run']);
type ScopedSpan = { name?: string; instrumentationScope?: { name: string } };

/** LiveKit already records this model call on the provider we export. */
export function liveKitOwnsCall(): boolean {
  const current = trace.getSpan(context.active()) as
    (Span & ScopedSpan) | undefined;
  return Boolean(
    current &&
    current.instrumentationScope?.name === SCOPE &&
    state.integrationScopes.has(SCOPE) &&
    INFERENCE_SPANS.has(current.name ?? '') &&
    isRoutedSpan(current),
  );
}
