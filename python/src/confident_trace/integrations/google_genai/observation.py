"""Delay only our fallback span until external ownership can be observed.

An external wrapper may be inside or outside ours. The shared provider's public
on_start callback proves whether it receives that native span. Until then no
Confident span is inserted into the application's parent context.
"""

from contextlib import contextmanager
from time import time_ns

from opentelemetry import context, trace

from ..._core import runtime
from ..._core.observation import OBSERVER
from .._shared.lifecycle import _SUPPRESS
from ._constants import SCOPE_NAMES


class DeferredOperation:
    def __init__(self, factory):
        self.factory = factory
        self.processor = runtime.current().processor
        self.started = time_ns()
        self.parent = context.get_current()
        self.ctx = context.set_value(
            _SUPPRESS, True, context.set_value(OBSERVER, self.observe, self.parent)
        )
        self.operation = None
        self.native = False
        self.ended = False

    def observe(self, span, processor):
        scope = span.instrumentation_scope
        if (
            processor is self.processor
            and scope is not None
            and scope.name in SCOPE_NAMES
        ):
            self.native = True

    def resolve(self):
        if self.operation is None and not self.native and not self.ended:
            token = context.attach(self.parent)
            try:
                self.operation = self.factory(self.started)
            finally:
                context.detach(token)
        return self.operation

    @property
    def span(self):
        op = self.resolve()
        return op.span if op else trace.INVALID_SPAN

    @property
    def is_entry(self):
        op = self.resolve()
        return op.is_entry if op else False

    @contextmanager
    def active(self):
        if self.operation is not None:
            with self.operation.active():
                yield
        else:
            token = context.attach(self.ctx)
            try:
                yield
            finally:
                context.detach(token)

    def end(self, error=None):
        if self.ended:
            return
        op = self.resolve()
        self.ended = True
        if op:
            op.end(error)
        self.factory = None
        self.ctx = self.parent = None
