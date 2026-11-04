"""Install anthropic instrumentation without importing its SDK until selected."""

import inspect

from ..._attributes import Integration
from ..._core.scopes import register_sdk_call_scope
from ..._semconv import genai_v1_37_0 as ai
from .._shared.lifecycle import begin_call, finish_call, wrapper
from .._shared.patching import install_targets
from ._constants import SCOPE_NAME
from .extraction import connection, request, response
from .streaming import Accumulator, AsyncStream, Manager, Stream

TARGETS = [
    ("anthropic.resources.messages", c, method)
    for c in ("Messages", "AsyncMessages")
    for method in ("create", "stream")
]


def begin(params, instance=None):
    return begin_call(
        ai.GEN_AI_OPERATION_NAME__CHAT,
        ai.GEN_AI_PROVIDER_NAME__ANTHROPIC,
        params.get("model"),
        params,
        instance,
        connection,
        request,
        integration=Integration.ANTHROPIC,
    )


def finish(op, value):
    return finish_call(op, value, response, Accumulator, Stream, AsyncStream)


def instrument(runtime):
    return install_targets(
        TARGETS,
        lambda original, method: wrapper(
            begin,
            finish,
            asynchronous=inspect.iscoroutinefunction(inspect.unwrap(original)),
            manager=Manager if method == "stream" else None,
        ),
    ) + register_sdk_call_scope(SCOPE_NAME)
