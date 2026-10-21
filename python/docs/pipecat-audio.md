# Pipecat call audio

Status: implemented in `integrations/pipecat/recording.py`, with the upload shared
with LiveKit in `integrations/_shared/recording.py`. See
[Pipecat (native OTel)](integrations.md#pipecat-native-otel) for tracing. This page
records the user experience and the decisions behind it.

## What the user writes

Pipecat has no built-in recording. Its recorder is `AudioBufferProcessor`, which
the user adds to the pipeline. We only hook it; we never add one.

```python
import confident_trace as ct
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

ct.init()

audio = AudioBufferProcessor(num_channels=2, auto_start_recording=True)
pipeline = Pipeline([
    transport.input(), stt, user_aggregator, llm, tts,
    transport.output(),
    audio,                     # after output, so it hears both sides
    assistant_aggregator,
])
worker = PipelineWorker(pipeline)
```

That is the whole change. Users who already save audio through
`@audio.event_handler("on_audio_data")` keep their handler; ours runs beside it.

## What the user gets

- The call's trace has a call player, and each `turn` span has a player cut to
  that turn, the same as LiveKit calls.
- The upload happens when the pipeline ends (`EndFrame` or `CancelFrame`), before
  the final span flush. It waits at most three seconds, then the flush waits at
  most five.
- Nothing is uploaded when:
  - the pipeline has no `AudioBufferProcessor`, or recording never started
  - tracing is off for that pipeline (`enable_tracing=False`, or `"pipecat"` left
    out of `instrumentations`)
  - `init(capture_content=False)`
  - the sample rate is one Opus cannot encode (8, 12, 16, 24 and 48 kHz work; a
    warning is logged)
  - the encoded recording is over 18 MB (a warning is logged)
  - the upload fails or times out (a warning is logged; the trace still arrives)

## How it works

1. When an `AudioBufferProcessor` is set up in a traced pipeline, we add our own
   `on_audio_data` handler to it and register it with its `PipelineWorker`
   (`FrameProcessorSetup.pipeline_worker`).
2. When `start_recording()` runs, we note the wall-clock start (`startedAt`), which
   the backend uses to line turns up with the audio. Starting again after a stop
   discards the earlier audio, so the recording always matches `startedAt`.
3. Each `on_audio_data` delivery (one at the end when `buffer_size=0`, or one
   every `buffer_size` bytes) is encoded with `soundfile` into a temporary Ogg/Opus
   file as it arrives, in order, off the event loop.
4. `PipelineWorker._cleanup` is wrapped: the conversation's trace id is read from
   the worker's tracing context before Pipecat ends the conversation span. Once
   the processors have delivered their last audio, we close the file and POST it
   to `/v1/call-recordings?traceUuid=…&startedAt=…`, then flush spans. When a
   pipeline has several processors, the first one with audio is uploaded.

## Decisions

### 1. Audio format

Pipecat hands over raw 16-bit PCM. LiveKit uploads Ogg/Opus.

| Option | 30-minute call, stereo 24 kHz | User sees |
| --- | --- | --- |
| Ogg/Opus via `soundfile` | about 16 MB at libsndfile's default bitrate | Long calls fit. No new dependency: Pipecat already requires `soundfile` (libsndfile), which encodes Opus from version 1.0.29. |
| WAV | about 170 MB | Calls over about 3 minutes skip the upload at the current 18 MB cap. |
| WAV, downsampled to 16 kHz mono | about 58 MB | Calls over about 9 minutes skip the upload; quality drops and the speakers can't be separated. |

Decided: Ogg/Opus only. The backend and the call player already accept
`audio/ogg`, so nothing changes there. `soundfile` 0.13 bundles a libsndfile that
writes Opus.

### 2. Mono or stereo

- **Stereo** (`num_channels=2`, user left, bot right): the player can mute or
  highlight one side, and overlaps and interruptions are easy to hear. With Opus
  the size cost is small.
- **Mono**: matches Pipecat's default, so users who leave the default still get
  audio.

Decided: accept whatever the user configured and document stereo as the better
choice. We never change their processor's channel count.

### 3. Long calls and the size cap

| Option | User sees |
| --- | --- |
| Keep the 18 MB cap, skip with a warning | Simple. Only matters for very long calls with Opus. |
| Raise the cap for Pipecat | Needs a larger backend body limit. |
| Chunked or presigned upload | No practical limit; needs a new backend endpoint and more time at shutdown. |

Decided: keep the 18 MB cap. At libsndfile's default Opus bitrate (about 73 kbps
stereo, 36 kbps mono at 24 kHz) that is about 34 minutes of stereo or 69 minutes
of mono.

### 4. Recording that was never started

`AudioBufferProcessor` records only after `start_recording()` or with
`auto_start_recording=True`. A user who adds the processor and forgets this gets
no audio and no error.

| Option | User sees |
| --- | --- |
| Respect the user's choice | Recording stays fully under user control, for example waiting for consent. |
| Start recording automatically once the pipeline starts | Works out of the box, but overrides a deliberate delay. |

Decided: respect the user's choice; the docs example uses
`auto_start_recording=True`.

### 5. Memory

With `buffer_size=0`, Pipecat itself holds the whole call in memory (about
170 MB for 30 minutes of stereo 24 kHz). Encoding each chunk into a temporary
file as it arrives keeps our own copy small, but only `buffer_size>0` (for
example one second, `buffer_size=sample_rate * 2 * channels`) bounds Pipecat's.

Decided: encode chunks as they arrive, and document a `buffer_size` for long calls.

### 6. Privacy switch

`capture_content=False` skips the upload, as with LiveKit. Pipecat has no
equivalent of `LIVEKIT_TELEMETRY_ALLOW_PII`.

| Option | User sees |
| --- | --- |
| `capture_content=False` only | One switch for all content. |
| A separate audio switch, for example `CONFIDENT_UPLOAD_CALL_AUDIO=false` | Keep text traces but never send audio. |

Decided: `capture_content` only; add a switch if customers ask.

### 7. Backend display

Confident AI now builds a timed transcript from Pipecat's `stt` and `tts` spans,
grouped by turn, and cuts each turn's audio from the call recording using those
timings, the same way it does for LiveKit. Once the SDK uploads the recording,
the call player and the per-turn players need no further backend work, as long
as the format is `audio/ogg` (decision 1).

## Not planned

- Adding an `AudioBufferProcessor` to the user's pipeline for them.
- Per-turn uploads (`on_user_turn_audio_data` / `on_bot_turn_audio_data`). One
  call recording plus turn timestamps covers the same players with one upload.
