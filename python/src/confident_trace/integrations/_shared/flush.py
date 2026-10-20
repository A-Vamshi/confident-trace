"""Bounded flush for frameworks that end a call before the process may exit."""

import asyncio
import threading

from ..._core.runtime import log


async def bounded_flush(runtime, label, timeout_seconds=5):
    if not (runtime.active and runtime.processor):
        return
    result = []

    def flush():
        try:
            result.append(runtime.processor.force_flush(timeout_seconds * 1000))
        except Exception:
            result.append(False)

    # A broken exporter must not keep the worker alive through asyncio's
    # executor shutdown; only the bounded join uses it.
    try:
        worker = threading.Thread(target=flush, daemon=True)
        worker.start()
        await asyncio.to_thread(worker.join, timeout_seconds)
        if not result or not result[0]:
            log.debug("%s final span flush failed or timed out", label)
    except Exception:
        log.debug("%s final span flush failed or timed out", label)
