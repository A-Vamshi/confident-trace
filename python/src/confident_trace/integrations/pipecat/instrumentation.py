"""Native Pipecat telemetry interoperability."""

from importlib.metadata import version

from opentelemetry import trace

from ..._attributes import Integration
from .._shared.flush import bounded_flush
from .._shared.lifecycle import register_native_inference
from .._shared.patching import install_targets
from ._constants import (
    CONTENT_ATTRIBUTES,
    INFERENCE_SCOPE,
    INFERENCE_SPANS,
    SCOPE_NAMES,
)


def instrument(runtime):
    version("pipecat-ai")

    # Pipecat creates every span on the global provider. A provider passed to
    # init() is installed there only if nothing else has been.
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        trace.set_tracer_provider(runtime.provider)

    # Tracing is opt-in per pipeline; selecting this integration turns it on.
    # An explicit enable_tracing=False is respected.
    def init_wrapper(original, method):
        def init(wrapped, instance, args, kwargs):
            if runtime.active:
                kwargs.setdefault("enable_tracing", True)
            return wrapped(*args, **kwargs)

        return init

    # _cleanup ends the conversation span. A bot process is often stopped as
    # soon as its pipeline finishes, before the batch processor's next export.
    def cleanup_wrapper(original, method):
        async def cleanup(wrapped, instance, args, kwargs):
            try:
                return await wrapped(*args, **kwargs)
            finally:
                if getattr(instance, "_enable_tracing", False):
                    await bounded_flush(runtime, "Pipecat")

        return cleanup

    undo = install_targets(
        [("pipecat.pipeline.worker", "PipelineWorker", "__init__")], init_wrapper
    )
    if not undo:
        raise RuntimeError("Pipecat pipeline hook could not be installed")
    undo += install_targets(
        [("pipecat.pipeline.worker", "PipelineWorker", "_cleanup")], cleanup_wrapper
    )
    for scope in SCOPE_NAMES:
        runtime.processor.integration_scopes[scope] = Integration.PIPECAT
        runtime.processor.content_scopes[scope] = CONTENT_ATTRIBUTES
    return undo + register_native_inference(INFERENCE_SCOPE, span_names=INFERENCE_SPANS)
