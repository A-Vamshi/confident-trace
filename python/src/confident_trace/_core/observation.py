"""Observe spans delivered to our processor using public OTel callbacks."""

from opentelemetry import context

from .._attributes import NATIVE_OBSERVER_CONTEXT_KEY

OBSERVER = context.create_key(NATIVE_OBSERVER_CONTEXT_KEY)


def observe_start(span, parent_context, processor):
    observer = context.get_value(OBSERVER, parent_context)
    if observer is not None:
        observer(span, processor)
