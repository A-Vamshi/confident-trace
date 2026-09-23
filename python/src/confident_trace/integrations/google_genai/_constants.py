"""Identifiers owned by OpenTelemetry's Google GenAI instrumentation."""

from typing import Final

INSTRUMENTATION_MODULE: Final = "opentelemetry.instrumentation.google_genai"
INSTRUMENTOR: Final = "GoogleGenAiSdkInstrumentor"
# Older releases trace under the module name, newer ones via util-genai's handler.
SCOPE_NAMES: Final = frozenset(
    {INSTRUMENTATION_MODULE, "opentelemetry.util.genai.handler"}
)
