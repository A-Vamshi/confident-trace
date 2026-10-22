"""Upload the call audio LiveKit records itself (`record=True`) when a job ends."""

import asyncio
import os
import weakref

from opentelemetry import trace

from ..._core.runtime import log
from .._shared.recording import (
    MAX_RECORDING_BYTES,
    can_upload,
    upload_call_recording,
)

# LiveKit's own PII opt-out (livekit.agents.telemetry.utils.allow_pii_from_env).
ALLOW_PII_ENV_VAR = "LIVEKIT_TELEMETRY_ALLOW_PII"
FALSY = ("0", "false", "no", "off")

_jobs_with_recording_upload = weakref.WeakSet()


def register_recording_upload(runtime):
    from livekit.agents import get_job_context

    ctx = get_job_context(required=False)
    if ctx is None or ctx in _jobs_with_recording_upload:
        return
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return
    trace_uuid = f"{span_context.trace_id:032x}"
    _jobs_with_recording_upload.add(ctx)

    # Shutdown callbacks run after the recorder closes and before
    # _on_cleanup deletes the session directory that holds the file.
    async def upload_recording():
        await _upload(runtime, ctx, trace_uuid)

    ctx.add_shutdown_callback(upload_recording)


def _pii_withheld():
    raw = os.environ.get(ALLOW_PII_ENV_VAR)
    return raw is not None and raw.strip().lower() in FALSY


def _read(path):
    if os.path.getsize(path) > MAX_RECORDING_BYTES:
        return None
    with open(path, "rb") as recording:
        return recording.read()


async def _upload(runtime, ctx, trace_uuid):
    if not can_upload(runtime) or _pii_withheld():
        return
    try:
        report = ctx.make_session_report()
        path = report.audio_recording_path
        started_at = report.audio_recording_started_at
        if path is None or started_at is None:
            return
        body = await asyncio.to_thread(_read, path)
        if not body:
            log.warning("LiveKit call recording is empty or too large to upload")
            return
        await upload_call_recording(runtime, "LiveKit", trace_uuid, started_at, body)
    except Exception:
        log.debug("LiveKit call recording upload failed")
