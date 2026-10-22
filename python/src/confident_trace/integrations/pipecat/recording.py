"""Upload the call audio a pipeline's AudioBufferProcessor records when the pipeline ends."""

import asyncio
import os
import tempfile
import time
import weakref

from opentelemetry import trace

from ..._core.runtime import log
from .._shared.recording import (
    MAX_RECORDING_BYTES,
    can_upload,
    upload_call_recording,
)

# The sample rates libsndfile's Opus encoder accepts.
OPUS_SAMPLE_RATES = frozenset({8000, 12000, 16000, 24000, 48000})

_recorders_by_worker = weakref.WeakKeyDictionary()
_recorder_by_processor = weakref.WeakKeyDictionary()


class CallRecorder:
    def __init__(self):
        self.started_at = None
        self._path = None
        self._file = None
        self._unusable = False
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            await asyncio.to_thread(self._discard)
            self.started_at = time.time()
            self._unusable = False

    async def write(self, audio, sample_rate, num_channels):
        async with self._lock:
            if self.started_at is None or self._unusable or not audio:
                return
            if sample_rate not in OPUS_SAMPLE_RATES:
                self._unusable = True
                log.warning(
                    "Pipecat call recording at %s Hz cannot be encoded as Opus",
                    sample_rate,
                )
                return
            try:
                await asyncio.to_thread(self._write, audio, sample_rate, num_channels)
            except Exception:
                self._unusable = True
                log.debug("Pipecat call recording could not be encoded")

    async def finish(self):
        async with self._lock:
            try:
                return await asyncio.to_thread(self._finish)
            finally:
                await asyncio.to_thread(self._discard)

    def _write(self, audio, sample_rate, num_channels):
        import numpy
        import soundfile

        if self._file is None:
            descriptor, self._path = tempfile.mkstemp(suffix=".ogg")
            os.close(descriptor)
            self._file = soundfile.SoundFile(
                self._path,
                mode="w",
                samplerate=sample_rate,
                channels=num_channels,
                format="OGG",
                subtype="OPUS",
            )
        frames = numpy.frombuffer(audio, dtype=numpy.int16).reshape(-1, num_channels)
        self._file.write(frames)

    def _finish(self):
        if self._file is None or self._unusable:
            return None
        self._file.close()
        self._file = None
        if os.path.getsize(self._path) > MAX_RECORDING_BYTES:
            log.warning("Pipecat call recording is too large to upload")
            return None
        with open(self._path, "rb") as recording:
            return recording.read()

    def _discard(self):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
        if self._path is not None:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._path = None


def attach_recorder(runtime, processor, setup):
    worker = getattr(setup, "pipeline_worker", None)
    if worker is None or not getattr(setup, "enable_tracing", False):
        return
    if processor in _recorder_by_processor or not can_upload(runtime):
        return
    recorder = CallRecorder()

    async def on_audio_data(_processor, audio, sample_rate, num_channels):
        await recorder.write(audio, sample_rate, num_channels)

    processor.add_event_handler("on_audio_data", on_audio_data)
    _recorder_by_processor[processor] = recorder
    _recorders_by_worker.setdefault(worker, []).append(recorder)


async def recording_started(processor):
    recorder = _recorder_by_processor.get(processor)
    if recorder is not None and not getattr(processor, "_recording", False):
        await recorder.start()


def conversation_trace_uuid(worker):
    tracing_context = getattr(worker, "_tracing_context", None)
    if tracing_context is None:
        return None
    span_context = trace.get_current_span(
        tracing_context.get_conversation_context()
    ).get_span_context()
    return f"{span_context.trace_id:032x}" if span_context.is_valid else None


async def upload_recording(runtime, worker, trace_uuid):
    recorders = _recorders_by_worker.pop(worker, [])
    try:
        for recorder in recorders:
            body = await recorder.finish()
            if not body or trace_uuid is None or not can_upload(runtime):
                continue
            await upload_call_recording(
                runtime, "Pipecat", trace_uuid, recorder.started_at, body
            )
            return
    except Exception:
        log.debug("Pipecat call recording upload failed")
    finally:
        for recorder in recorders:
            await recorder.finish()
