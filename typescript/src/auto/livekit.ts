import { ProxyTracerProvider } from '@opentelemetry/api';
import { enabled, onActivate } from '@/auto/control';
import { observed, replace } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';
import { SCOPE } from '@/integrations/livekit';
import { state } from '@/runtime/state';
import { INTEGRATIONS } from '@/semconv/generated';

const flushing = new WeakSet<object>();
export function attachLiveKit(exports: Foreign): void {
  // Every job calls ctx.connect(); its process exits right after shutdown
  // callbacks, before batched spans would otherwise export.
  if (exports.JobContext)
    replace(
      exports.JobContext.prototype,
      'connect',
      (original) =>
        function (this: Foreign, ...args: Foreign[]) {
          if (enabled('livekit') && !flushing.has(this)) {
            flushing.add(this);
            this.addShutdownCallback(async () => {
              await state.runtime?.flush();
            });
          }
          return original.apply(this, args);
        },
    );
  const telemetry = exports.telemetry ?? exports;
  if (typeof telemetry.setTracerProvider !== 'function' || !telemetry.tracer)
    return;
  observed('livekit');
  onActivate('livekit', () => {
    state.integrationScopes.set(SCOPE, INTEGRATIONS.livekit);
    // LiveKit Cloud replaces an unset provider with a private one. Preserve a
    // configured provider; ours keeps Cloud export via registerSpanProcessor.
    const owned = state.ownedProvider;
    if (
      !owned ||
      !(telemetry.tracer.getProvider() instanceof ProxyTracerProvider)
    )
      return;
    telemetry.setTracerProvider(owned.tracerProvider, {
      registerSpanProcessor: owned.registerSpanProcessor,
    });
  });
}
