"""Bounded call recording upload for voice frameworks that end a call before the process may exit."""

import asyncio
import ssl
import threading
import urllib.request
from urllib.parse import urlencode

from ..._core.runtime import log

# About what the upload bound carries at 50 Mbps, roughly 25 minutes of call.
MAX_RECORDING_BYTES = 18 * 1024 * 1024
# Leaves the final span flush its own share of the framework's shutdown budget.
UPLOAD_TIMEOUT_SECONDS = 3
SPAN_BATCH_PATH = "/v1/traces"
CALL_RECORDING_PATH = "/v1/call-recordings"
CONTENT_TYPE = "audio/ogg"


def recording_endpoint(runtime):
    if runtime.otlp_http_export is None:
        return None
    endpoint, headers = runtime.otlp_http_export
    if not endpoint.endswith(SPAN_BATCH_PATH):
        return None
    return endpoint[: -len(SPAN_BATCH_PATH)] + CALL_RECORDING_PATH, headers


def can_upload(runtime):
    return bool(
        runtime.active and runtime.policy.enabled and recording_endpoint(runtime)
    )


def _ssl_context(skip_verify):
    if not skip_verify:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _post(url, headers, body, result, skip_verify=False):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=UPLOAD_TIMEOUT_SECONDS, context=_ssl_context(skip_verify)
        ) as response:
            result.append(200 <= response.status < 300)
    except Exception:
        result.append(False)


async def upload_call_recording(runtime, label, trace_uuid, started_at, body):
    destination = recording_endpoint(runtime)
    if destination is None:
        return
    endpoint, headers = destination
    query = urlencode({"traceUuid": trace_uuid, "startedAt": round(started_at * 1000)})
    result = []
    # A daemon thread so a hung upload cannot hold the worker past its bound.
    worker = threading.Thread(
        target=_post,
        args=(
            f"{endpoint}?{query}",
            {**headers, "content-type": CONTENT_TYPE},
            body,
            result,
            runtime.tls_skip_verify,
        ),
        daemon=True,
    )
    worker.start()
    await asyncio.to_thread(worker.join, UPLOAD_TIMEOUT_SECONDS)
    if not result or not result[0]:
        log.warning("%s call recording upload failed or timed out", label)
