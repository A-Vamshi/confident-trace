"""Native LiveKit Agents telemetry interoperability."""

from importlib.metadata import version

from opentelemetry import trace

from ..._attributes import Integration
from .._shared.lifecycle import register_native_inference
from ._constants import INFERENCE_SPANS, SCOPE_NAME


def instrument(runtime):
    version("livekit-agents")
    from livekit.agents import telemetry

    # There is no public getter for LiveKit's provider. Preserve configured
    # settings; an unset one would get a private LiveKit Cloud provider.
    configured = getattr(telemetry.tracer, "_tracer_provider", None)
    if isinstance(configured, (trace.ProxyTracerProvider, trace.NoOpTracerProvider)):
        telemetry.set_tracer_provider(runtime.provider)
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.LIVEKIT
    return register_native_inference(SCOPE_NAME, span_names=INFERENCE_SPANS)
