"""Media in custom content fields: markers in the field, bytes in one attribute."""

import gc
import json
from base64 import b64decode

import pytest
from conftest import spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace import _attributes as attrs
from confident_trace._core import spans as spans_module
from confident_trace._core.media import MARKER

WAV = b"RIFF$\x00\x00\x00WAVEfmt " + bytes(64)
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000"
    "000a49444154789c6360000002000100ffff03000006000557bfabd40000000049454e"
    "44ae426082"
)


def reinit(telemetry, **options):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0], exporter=exporter, instrumentations=(), **options
    )
    return exporter


def exported(exporter):
    (span,) = spans(exporter)
    return dict(span.attributes)


def attachments(attributes):
    return json.loads(attributes[attrs.SPAN_ATTACHMENTS])


def marker_ids(value):
    return MARKER.findall(value)


def test_a_media_value_becomes_a_marker_with_its_bytes_attached(telemetry):
    _, exporter = telemetry
    audio = ct.Media.from_bytes(WAV, "audio/wav")
    with ct.span("call"):
        ct.update_span(output=audio)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == str(audio)
    (media_id,) = marker_ids(row[attrs.SPAN_OUTPUT])
    attachment = attachments(row)[media_id]
    assert attachment["mimeType"] == "audio/wav"
    assert b64decode(attachment["dataBase64"]) == WAV
    assert attachment["dataBase64"] not in row[attrs.SPAN_OUTPUT]


def test_media_formatted_into_a_string_is_attached(telemetry):
    _, exporter = telemetry
    image = ct.Media.from_bytes(PNG, "image/png")
    with ct.span("describe"):
        ct.update_trace(input=f"What is in this picture? {image}")
    row = exported(exporter)
    value = json.loads(row["confident.trace.input"])
    assert value == f"What is in this picture? [CONFIDENT:IMAGE:{image._id}]"
    assert b64decode(attachments(row)[image._id]["dataBase64"]) == PNG


def test_a_temporary_formatted_media_is_still_found(telemetry):
    _, exporter = telemetry
    text = f"Listen: {ct.Media.from_bytes(WAV, 'audio/wav')}"
    gc.collect()
    with ct.span("call"):
        ct.update_span(input=text)
    row = exported(exporter)
    (media_id,) = marker_ids(row[attrs.SPAN_INPUT])
    assert b64decode(attachments(row)[media_id]["dataBase64"]) == WAV


def test_media_nested_in_structured_values_is_attached(telemetry):
    _, exporter = telemetry
    audio = ct.Media.from_bytes(WAV, "audio/wav")
    pdf = ct.Media.from_bytes(PNG, "application/pdf")
    with ct.span("call"):
        ct.update_span(metadata={"turns": [{"audio": audio}], "doc": pdf})
    row = exported(exporter)
    metadata = json.loads(row["confident.span.metadata"])
    assert metadata["turns"][0]["audio"] == f"[CONFIDENT:AUDIO:{audio._id}]"
    assert metadata["doc"] == f"[CONFIDENT:PDF:{pdf._id}]"
    assert set(attachments(row)) == {audio._id, pdf._id}


def test_trace_and_span_fields_on_one_span_share_one_attribute(telemetry):
    _, exporter = telemetry
    question = ct.Media.from_bytes(WAV, "audio/wav")
    answer = ct.Media.from_bytes(WAV, "audio/ogg")
    with ct.span("call"):
        ct.update_trace(input=question)
        ct.update_span(output=answer)
    assert set(attachments(exported(exporter))) == {question._id, answer._id}


def test_rewriting_a_field_drops_only_what_it_named(telemetry):
    _, exporter = telemetry
    kept = ct.Media.from_bytes(WAV, "audio/wav")
    replaced = ct.Media.from_bytes(PNG, "image/png")
    with ct.span("call"):
        ct.update_span(input=kept, output=replaced)
        ct.update_span(output="no media now")
    assert set(attachments(exported(exporter))) == {kept._id}


def test_clearing_the_last_attachment_leaves_an_empty_map(telemetry):
    _, exporter = telemetry
    with ct.span("call"):
        ct.update_span(output=ct.Media.from_bytes(WAV, "audio/wav"))
        ct.update_span(output="done")
    assert attachments(exported(exporter)) == {}


def test_media_named_twice_on_a_span_spends_its_budget_once(telemetry):
    exporter = reinit(telemetry, max_media_bytes=len(WAV))
    audio = ct.Media.from_bytes(WAV, "audio/wav")
    with ct.span("call"):
        ct.update_span(input=audio, output=f"replying to {audio}")
    row = exported(exporter)
    assert marker_ids(row[attrs.SPAN_INPUT]) == [audio._id]
    assert marker_ids(row[attrs.SPAN_OUTPUT]) == [audio._id]
    assert list(attachments(row)) == [audio._id]


def test_media_over_the_item_limit_becomes_a_note(telemetry):
    exporter = reinit(telemetry, max_media_bytes=len(WAV) - 1)
    with ct.span("call"):
        ct.update_span(output=ct.Media.from_bytes(WAV, "audio/wav"))
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == (
        "<inline_data: audio/wav, not captured>"
    )
    assert attrs.SPAN_ATTACHMENTS not in row


def test_a_remote_reference_attaches_its_url(telemetry):
    _, exporter = telemetry
    audio = ct.Media.from_uri("https://example.com/call.mp3")
    with ct.span("call"):
        ct.update_span(output=audio)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == f"[CONFIDENT:AUDIO:{audio._id}]"
    assert attachments(row)[audio._id] == {
        "url": "https://example.com/call.mp3",
        "mimeType": "audio/mpeg",
    }


def test_an_audio_file_is_read_when_the_field_is_written(telemetry, tmp_path):
    _, exporter = telemetry
    path = tmp_path / "turn.wav"
    path.write_bytes(WAV)
    audio = ct.Media.from_file(path)
    assert audio.mime_type == "audio/wav"
    with ct.span("call"):
        ct.update_span(output=audio)
    attachment = attachments(exported(exporter))[audio._id]
    assert b64decode(attachment["dataBase64"]) == WAV


def test_a_missing_file_becomes_a_note(telemetry, tmp_path):
    _, exporter = telemetry
    with ct.span("call"):
        ct.update_span(output=ct.Media.from_file(tmp_path / "gone.wav"))
    row = exported(exporter)
    assert "not captured" in json.loads(row[attrs.SPAN_OUTPUT])
    assert attrs.SPAN_ATTACHMENTS not in row


@pytest.mark.parametrize("mime_type", ["video/mp4", "text/csv", None])
def test_types_the_receiver_cannot_store_are_written_as_notes(telemetry, mime_type):
    _, exporter = telemetry
    media = ct.Media.from_bytes(WAV, mime_type)
    assert not MARKER.search(str(media))
    with ct.span("call"):
        ct.update_span(output=media)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == media.note()
    assert attrs.SPAN_ATTACHMENTS not in row


def test_marker_text_naming_unknown_media_is_left_alone(telemetry):
    _, exporter = telemetry
    text = "[CONFIDENT:IMAGE:" + "0" * 32 + "]"
    with ct.span("call"):
        ct.update_span(output=text)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_OUTPUT]) == text
    assert attrs.SPAN_ATTACHMENTS not in row


def test_disabled_capture_sends_neither_marker_nor_bytes(telemetry):
    exporter = reinit(telemetry, capture_content=False)
    with ct.span("call"):
        ct.update_span(output=ct.Media.from_bytes(WAV, "audio/wav"))
    row = exported(exporter)
    assert attrs.SPAN_OUTPUT not in row
    assert attrs.SPAN_ATTACHMENTS not in row


def test_a_truncated_marker_sends_no_bytes(telemetry):
    exporter = reinit(telemetry, max_content_bytes=64)
    with ct.span("call"):
        ct.update_span(output="x" * 64 + str(ct.Media.from_bytes(WAV, "audio/wav")))
    assert attrs.SPAN_ATTACHMENTS not in exported(exporter)


def test_a_captured_copy_of_a_model_request_sends_no_bytes(telemetry):
    _, exporter = telemetry
    request = [{"role": "user", "parts": [ct.Media.from_bytes(PNG, "image/png")]}]
    with ct.span("call") as span:
        spans_module.content(span, attrs.TRACE_INPUT, request)
    row = exported(exporter)
    part = json.loads(row[attrs.TRACE_INPUT])[0]["parts"][0]
    assert part["content_omitted"] is True
    assert attrs.SPAN_ATTACHMENTS not in row


def test_a_captured_write_drops_what_the_field_attached(telemetry):
    _, exporter = telemetry
    with ct.span("call") as span:
        ct.update_trace(input=ct.Media.from_bytes(WAV, "audio/wav"))
        spans_module.content(span, attrs.TRACE_INPUT, "captured later")
    assert attachments(exported(exporter)) == {}


def test_message_shaped_media_is_unchanged(telemetry):
    image = ct.Media.from_bytes(PNG, "image/png")
    assert image.to_part()["type"] == "blob"
    assert ct.Media.from_bytes(WAV, "audio/wav").to_part()["content_omitted"]


def test_span_audio_is_a_marker_beside_text_io(telemetry):
    _, exporter = telemetry
    audio = ct.Media.from_bytes(WAV, "audio/wav")
    with ct.span("turn"):
        ct.update_span(input="what's my balance?", output="It's $42.", audio=audio)
    row = exported(exporter)
    assert json.loads(row[attrs.SPAN_INPUT]) == "what's my balance?"
    assert row[attrs.SPAN_AUDIO] == f"[CONFIDENT:AUDIO:{audio._id}]"
    assert b64decode(attachments(row)[audio._id]["dataBase64"]) == WAV


def test_trace_audio_and_span_audio_share_the_attachments(telemetry):
    _, exporter = telemetry
    call = ct.Media.from_bytes(WAV, "audio/ogg")
    turn = ct.Media.from_bytes(WAV, "audio/wav")
    with ct.span("call", audio=turn):
        ct.update_trace(audio=call)
    row = exported(exporter)
    assert row[attrs.TRACE_AUDIO] == f"[CONFIDENT:AUDIO:{call._id}]"
    assert row[attrs.SPAN_AUDIO] == f"[CONFIDENT:AUDIO:{turn._id}]"
    assert set(attachments(row)) == {call._id, turn._id}


def test_replacing_audio_drops_the_old_file(telemetry):
    _, exporter = telemetry
    first = ct.Media.from_bytes(WAV, "audio/wav")
    second = ct.Media.from_bytes(WAV, "audio/wav")
    with ct.span("turn"):
        ct.update_span(audio=first)
        ct.update_span(audio=second)
    row = exported(exporter)
    assert row[attrs.SPAN_AUDIO] == f"[CONFIDENT:AUDIO:{second._id}]"
    assert set(attachments(row)) == {second._id}


def test_audio_over_the_budget_is_not_written(telemetry):
    exporter = reinit(telemetry, max_media_bytes=len(WAV) - 1)
    with ct.span("turn"):
        ct.update_span(audio=ct.Media.from_bytes(WAV, "audio/wav"))
    row = exported(exporter)
    assert attrs.SPAN_AUDIO not in row
    assert attrs.SPAN_ATTACHMENTS not in row


def test_disabled_capture_writes_no_audio(telemetry):
    exporter = reinit(telemetry, capture_content=False)
    with ct.span("turn"):
        ct.update_span(audio=ct.Media.from_bytes(WAV, "audio/wav"))
    assert attrs.SPAN_AUDIO not in exported(exporter)


@pytest.mark.parametrize(
    "value",
    [
        b"raw bytes",
        "turn.wav",
        ct.Media.from_bytes(PNG, "image/png"),
        ct.Media.from_bytes(WAV),
        ct.Media.from_uri("https://example.com/call.mp3"),
    ],
)
def test_audio_accepts_only_local_or_inline_audio_media(telemetry, value):
    with ct.span("turn"), pytest.raises(TypeError):
        ct.update_span(audio=value)
