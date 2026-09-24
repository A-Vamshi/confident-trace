import {
  context,
  trace,
  createContextKey,
  ROOT_CONTEXT,
} from '@opentelemetry/api';
import type { Context, Span } from '@opentelemetry/api';
import { suppressTracing, isTracingSuppressed } from '@opentelemetry/core';
import { requestSuppressionKey, state, traceContextKey } from '@/runtime/state';

export const projectKey = createContextKey('confident-trace.project-route');
export interface RouteScope {
  readonly identity: object;
}
export interface ProjectRouter {
  acquire(apiKey: string, parent: Context): RouteScope;
  release(route: RouteScope): void;
  owns(span: Span): boolean;
}
export function setProjectRouter(value: ProjectRouter): void {
  state.router = value;
}
/** Whether a span was started on the provider that exports to Confident. */
export function isRoutedSpan(span: Span): boolean {
  return state.router?.owns(span) ?? false;
}
export function tracingSuppressed(parent = context.active()): boolean {
  return (
    Boolean(parent.getValue(requestSuppressionKey)) ||
    isTracingSuppressed(parent)
  );
}
/** New trace, same request policy and private destination. */
export function detachedContext(): Context {
  let parent = ROOT_CONTEXT.setValue(
    projectKey,
    context.active().getValue(projectKey),
  );
  if (tracingSuppressed())
    parent = suppressTracing(parent).setValue(requestSuppressionKey, true);
  return parent.setValue(
    traceContextKey,
    context.active().getValue(traceContextKey),
  );
}
/** Suppress supported instrumentation; unrelated exporters remain application-owned. */
export function withTracingSuppressed<T>(callback: () => T): T {
  return context.with(
    suppressTracing(context.active()).setValue(requestSuppressionKey, true),
    callback,
  );
}
/** Select the destination before starting traced work, including automatic spans. */
export function projectContext<T>(
  options: { apiKey: string },
  callback: () => T,
): T {
  if (typeof options.apiKey !== 'string' || !options.apiKey.trim())
    throw new TypeError('apiKey must be a nonempty string');
  if (process.env.OTEL_SDK_DISABLED?.toLowerCase() === 'true')
    return callback();
  if (!state.router)
    throw new Error('Initialize Confident Trace before selecting a project');
  const owner = state.router;
  const route = owner.acquire(options.apiKey, context.active());
  let released = false;
  const release = () => {
    if (!released) {
      released = true;
      owner.release(route);
    }
  };
  try {
    const value = context.with(
      context.active().setValue(projectKey, route),
      callback,
    );
    if (
      value != null &&
      typeof (value as { then?: unknown }).then === 'function'
    )
      return Promise.resolve(value).finally(release) as T;
    release();
    return value;
  } catch (error) {
    release();
    throw error;
  }
}
export function activeRecordingSpan(parent: Context): boolean {
  return trace.getSpan(parent)?.isRecording() ?? false;
}
