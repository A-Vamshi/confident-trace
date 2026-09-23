"""Install google_genai instrumentation without importing its SDK until selected."""

import inspect
import sys

from opentelemetry import trace

from ..._attributes import Integration
from ..._semconv import genai_v1_37_0 as ai
from .._shared.lifecycle import begin_call, finish_call, same_provider, wrapper
from .._shared.patching import install_targets
from ._constants import INSTRUMENTATION_MODULE, INSTRUMENTOR, SCOPE_NAMES
from .extraction import connection, request, response
from .streaming import Accumulator

TARGETS = [
    ("google.genai.models", c, method)
    for c in ("Models", "AsyncModels")
    for method in ("generate_content", "generate_content_stream")
]


def begin(params, instance=None):
    return begin_call(
        ai.GEN_AI_OPERATION_NAME__GENERATE_CONTENT,
        ai.GEN_AI_PROVIDER_NAME__GCP_GEN_AI,
        params.get("model"),
        params,
        instance,
        connection,
        request,
        integration=Integration.GOOGLE_GENAI,
    )


def finish(op, value):
    return finish_call(op, value, response, Accumulator)


def instrumentor_active(rt):
    """Google's instrumentor owns the call; never construct it (that resets it)."""
    module = sys.modules.get(INSTRUMENTATION_MODULE)
    instance = getattr(getattr(module, INSTRUMENTOR, None), "_instance", None)
    if not getattr(instance, "is_instrumented_by_opentelemetry", False):
        return False
    current = trace.get_current_span()
    scope = getattr(current, "instrumentation_scope", None)
    if scope is None or scope.name not in SCOPE_NAMES:
        return True
    return same_provider(current, rt)


def instrument(runtime):
    return install_targets(
        TARGETS,
        lambda original, method: wrapper(
            begin,
            finish,
            asynchronous=inspect.iscoroutinefunction(inspect.unwrap(original)),
            manager=None,
            stand_down_when=instrumentor_active,
        ),
    )
