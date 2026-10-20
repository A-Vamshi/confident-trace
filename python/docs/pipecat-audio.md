# Pipecat call audio (proposed)

Status: design only, not implemented. Tracing ships first; see
[Pipecat (native OTel)](integrations.md#pipecat-native-otel). This page describes the
experience we want for call audio, from the user's side, and the decisions still
open. Each decision lists what a user would see under each option.

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
  the final span flush, inside the same five-second shutdown budget.
- Nothing is uploaded when:
  - the pipeline has no `AudioBufferProcessor`, or recording never started
  - tracing is off for that pipeline (`enable_tracing=False`, or `"pipecat"` left
    out of `instrumentations`)
  - `init(capture_content=False)`
  - the upload fails or times out (a warning is logged; the trace still arrives)

## How it works

1. On `StartFrame`, the processor receives the pipeline's tracing context. We keep
   the conversation's trace id from it.
2. On `start_recording`, we note the wall-clock start (`startedAt`), which the
   backend uses to line turns up with the audio.
3. Each `on_audio_data` delivery (one at the end when `buffer_size=0`, or one
   every `buffer_size` bytes) is encoded into a temporary file as it arrives.
4. On `stop_recording`, we close the file and POST it to
   `/v1/call-recordings?traceUuid=…&startedAt=…`, reusing LiveKit's upload path.

## Open decisions

### 1. Audio format

Pipecat hands over raw 16-bit PCM. LiveKit uploads Ogg/Opus.

| Option | 30-minute call, stereo 24 kHz | User sees |
| --- | --- | --- |
| Ogg/Opus via `soundfile` | about 5–10 MB | Long calls fit. No new dependency: Pipecat already requires `soundfile` (libsndfile), which encodes Opus from version 1.0.29. |
| WAV | about 170 MB | Calls over about 3 minutes skip the upload at the current 18 MB cap. |
| WAV, downsampled to 16 kHz mono | about 58 MB | Calls over about 9 minutes skip the upload; quality drops and the speakers can't be separated. |

Recommendation: Ogg/Opus, falling back to a capped WAV only if the installed
libsndfile cannot write Opus. This needs the backend to accept `audio/ogg` from
Pipecat (it already does from LiveKit).

### 2. Mono or stereo

- **Stereo** (`num_channels=2`, user left, bot right): the player can mute or
  highlight one side, and overlaps and interruptions are easy to hear. With Opus
  the size cost is small.
- **Mono**: matches Pipecat's default, so users who leave the default still get
  audio.

Recommendation: accept whatever the user configured and document stereo as the
better choice. We should not change their processor's channel count.

### 3. Long calls and the size cap

| Option | User sees |
| --- | --- |
| Keep the 18 MB cap, skip with a warning | Simple. Only matters for very long calls with Opus. |
| Raise the cap for Pipecat | Needs a larger backend body limit. |
| Chunked or presigned upload | No practical limit; needs a new backend endpoint and more time at shutdown. |

Recommendation: keep the cap for v1 if we go with Opus; revisit for WAV.

### 4. Recording that was never started

`AudioBufferProcessor` records only after `start_recording()` or with
`auto_start_recording=True`. A user who adds the processor and forgets this gets
no audio and no error.

| Option | User sees |
| --- | --- |
| Respect the user's choice and log a debug message | Recording stays fully under user control, for example waiting for consent. |
| Start recording automatically once the pipeline starts | Works out of the box, but overrides a deliberate delay. |

Recommendation: respect the user's choice; the docs example uses
`auto_start_recording=True`.

### 5. Memory

With `buffer_size=0`, Pipecat itself holds the whole call in memory (about
170 MB for 30 minutes of stereo 24 kHz). Encoding each chunk into a temporary
file as it arrives keeps our own copy small, but only `buffer_size>0` (for
example one second, `buffer_size=sample_rate * 2 * channels`) bounds Pipecat's.

Recommendation: encode chunks as they arrive, and document a `buffer_size` for long calls.

### 6. Privacy switch

`capture_content=False` skips the upload, as with LiveKit. Pipecat has no
equivalent of `LIVEKIT_TELEMETRY_ALLOW_PII`.

| Option | User sees |
| --- | --- |
| `capture_content=False` only | One switch for all content. |
| A separate audio switch, for example `CONFIDENT_UPLOAD_CALL_AUDIO=false` | Keep text traces but never send audio. |

Recommendation: `capture_content` only for v1; add a switch if customers ask.

### 7. Backend display

The players line turns up using LiveKit's turn spans. Pipecat has one `turn` span
per exchange (user and bot together), and its text is in the plain
`transcript`, `text`, `input` and `output` attributes. The backend needs a
Pipecat mapping for the per-turn players and the transcript. Until then, the
call player works, but turn players and transcript text may not show.

## Not planned

- Adding an `AudioBufferProcessor` to the user's pipeline for them.
- Per-turn uploads (`on_user_turn_audio_data` / `on_bot_turn_audio_data`). One
  call recording plus turn timestamps covers the same players with one upload.
