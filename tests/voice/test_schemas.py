"""Tests for `orion.voice.schemas` -- construction invariants, closed schema
enforcement, and the structural architectural properties Phase 1 exists to
guarantee: no field can encode an executable instruction, no session
identifier is secretly minted, and provenance is never model/user-authored.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orion.provenance import canonical_hash
from orion.voice.schemas import (
    SynthesizedSpeech,
    Transcript,
    VoiceState,
    transcript_fingerprint,
)

BASE_TIME = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def _transcript(**overrides: object) -> Transcript:
    defaults: dict[str, object] = {
        "text": "Analyze gold.",
        "provider": "deepgram",
        "model": "nova-3",
        "transcribed_at": BASE_TIME,
        "duration_seconds": 1.4,
        "confidence": 0.92,
    }
    defaults.update(overrides)
    return Transcript(**defaults)


def _speech(**overrides: object) -> SynthesizedSpeech:
    defaults: dict[str, object] = {
        "audio": b"\x00\x01\x02",
        "format": "mp3",
        "provider": "deepgram",
        "model": "aura-2",
        "synthesized_at": BASE_TIME,
    }
    defaults.update(overrides)
    return SynthesizedSpeech(**defaults)


# --- VoiceState ---


def test_voice_state_has_exactly_the_five_brief_states() -> None:
    assert {s.value for s in VoiceState} == {
        "idle",
        "listening",
        "understanding",
        "analyzing",
        "speaking",
    }


def test_voice_state_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        VoiceState("bogus")


# --- Transcript: construction / closedness ---


def test_transcript_constructs_with_valid_fields() -> None:
    transcript = _transcript()
    assert transcript.text == "Analyze gold."
    assert transcript.provider == "deepgram"


def test_transcript_is_frozen() -> None:
    transcript = _transcript()
    with pytest.raises(ValidationError):
        transcript.text = "changed"  # type: ignore[misc]


def test_transcript_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _transcript(command="do something")


def test_transcript_model_and_confidence_and_duration_are_optional() -> None:
    transcript = _transcript(model=None, duration_seconds=None, confidence=None)
    assert transcript.model is None
    assert transcript.duration_seconds is None
    assert transcript.confidence is None


# --- Transcript: bounds ---


def test_transcript_rejects_blank_text() -> None:
    with pytest.raises(ValidationError):
        _transcript(text="")


def test_transcript_rejects_oversized_text() -> None:
    with pytest.raises(ValidationError):
        _transcript(text="x" * 2001)


def test_transcript_accepts_text_at_exactly_the_length_cap() -> None:
    transcript = _transcript(text="x" * 2000)
    assert len(transcript.text) == 2000


def test_transcript_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        _transcript(confidence=1.5)
    with pytest.raises(ValidationError):
        _transcript(confidence=-0.1)


def test_transcript_rejects_non_positive_duration() -> None:
    with pytest.raises(ValidationError):
        _transcript(duration_seconds=0)
    with pytest.raises(ValidationError):
        _transcript(duration_seconds=-1.0)


def test_transcript_rejects_non_finite_duration() -> None:
    """gt=0 alone does not exclude float("inf") -- inf > 0 is True in
    Python. No real audio clip has infinite duration."""
    with pytest.raises(ValidationError, match="finite"):
        _transcript(duration_seconds=float("inf"))
    with pytest.raises(ValidationError):
        _transcript(duration_seconds=float("nan"))


def test_transcript_rejects_naive_transcribed_at() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _transcript(transcribed_at=datetime(2026, 8, 24, 12, 0, 0))


def test_transcript_rejects_blank_provider() -> None:
    with pytest.raises(ValidationError):
        _transcript(provider="")


# --- Transcript: untrusted-text handling ---


def test_transcript_accepts_unicode_text() -> None:
    transcript = _transcript(text="Quel est le prix de l'or aujourd'hui ? 黄金価格")
    assert "黄金" in transcript.text


@pytest.mark.parametrize(
    "control_char",
    ["\n", "\r", "\t", "\x00", "\x07", "\x1b", "\x7f"],
    ids=["LF", "CR", "TAB", "NUL", "BEL", "ESC", "DEL"],
)
def test_transcript_rejects_control_characters_in_text(control_char: str) -> None:
    with pytest.raises(ValidationError, match="control character"):
        _transcript(text=f"Analyze gold{control_char}now")


def test_transcript_rejects_rtl_override_character() -> None:
    """U+202E (RIGHT-TO-LEFT OVERRIDE) is Unicode category Cf ("format"),
    not Cc ("control") -- worth testing explicitly rather than assuming it
    falls under the same control-character check, since a homoglyph/text-
    spoofing character being silently accepted would be a real gap. Python's
    str.isprintable() treats the whole "Other" major category (Cc/Cf/Cs/Co/
    Cn) as non-printable, so this is already covered -- confirmed directly."""
    # Built via chr(), not embedded as literal source bytes, so this file
    # itself never contains a raw bidi-control character.
    rtl_override, pop_directional_formatting = chr(0x202E), chr(0x202C)
    with pytest.raises(ValidationError, match="control character"):
        _transcript(text=f"gold {rtl_override}won dloG{pop_directional_formatting} up")


def test_transcript_has_no_field_capable_of_encoding_a_command_or_execution() -> None:
    """Structural proof, not a convention: no field name on Transcript
    resembles a command/intent/tool/execution parameter. Assigning meaning
    to the transcript text is the eventual UNDERSTANDING layer's job, not
    this schema's -- see the module docstring."""
    forbidden_substrings = (
        "command",
        "intent",
        "action",
        "tool",
        "exec",
        "sql",
        "shell",
        "url",
        "code",
        "query",
    )
    field_names = set(Transcript.model_fields)
    for name in field_names:
        lowered = name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, f"field {name!r} looks like a command/execution field"


def test_transcript_has_no_dict_or_arbitrary_json_fields() -> None:
    """Every field must be a bounded scalar or a closed type -- no `dict`,
    `Any`, or other catch-all shape that could smuggle arbitrary structure
    past schema validation."""
    for name, field in Transcript.model_fields.items():
        annotation_str = str(field.annotation)
        assert "dict" not in annotation_str.lower(), f"field {name!r} looks dict-shaped"
        assert "Any" not in annotation_str, f"field {name!r} accepts Any"


def test_transcript_has_no_identifier_field() -> None:
    """Deliberate design choice, not an oversight: Transcript carries no
    session/utterance id. Identity is content-addressed via
    transcript_fingerprint (canonical_hash) instead of a separately minted
    value -- see the module docstring and docs/DECISIONS.md. This also
    means no uuid4()-style identifier is ever secretly generated."""
    field_names = {name.lower() for name in Transcript.model_fields}
    for forbidden in ("id", "session_id", "utterance_id", "session", "uuid"):
        assert forbidden not in field_names


# --- Transcript: fingerprint (provenance) ---


def test_transcript_fingerprint_is_reproducible() -> None:
    a = _transcript()
    b = _transcript()
    assert transcript_fingerprint(a) == transcript_fingerprint(b)


def test_transcript_fingerprint_matches_independent_recomputation() -> None:
    transcript = _transcript()
    assert transcript_fingerprint(transcript) == canonical_hash(transcript)


def test_transcript_fingerprint_changes_when_text_changes() -> None:
    a = _transcript(text="Analyze gold.")
    b = _transcript(text="Analyze silver.")
    assert transcript_fingerprint(a) != transcript_fingerprint(b)


def test_transcript_fingerprint_changes_when_provenance_changes() -> None:
    """A fingerprint answers "what is the exact content of this record,
    provenance included" -- not just "what did the user say". Two
    transcripts with identical text but different provider/confidence are
    genuinely different records."""
    a = _transcript(confidence=0.9)
    b = _transcript(confidence=0.5)
    assert transcript_fingerprint(a) != transcript_fingerprint(b)


def test_transcript_fingerprint_is_64_char_lowercase_hex() -> None:
    fp = transcript_fingerprint(_transcript())
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


# --- SynthesizedSpeech ---


def test_synthesized_speech_constructs_with_valid_fields() -> None:
    speech = _speech()
    assert speech.format == "mp3"
    assert speech.audio == b"\x00\x01\x02"


def test_synthesized_speech_is_frozen() -> None:
    speech = _speech()
    with pytest.raises(ValidationError):
        speech.format = "wav"  # type: ignore[misc]


def test_synthesized_speech_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _speech(command="play")


def test_synthesized_speech_rejects_empty_audio() -> None:
    with pytest.raises(ValidationError):
        _speech(audio=b"")


def test_synthesized_speech_rejects_blank_format() -> None:
    with pytest.raises(ValidationError):
        _speech(format="")


def test_synthesized_speech_rejects_naive_synthesized_at() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _speech(synthesized_at=datetime(2026, 8, 24, 12, 0, 0))


def test_synthesized_speech_model_is_optional() -> None:
    speech = _speech(model=None)
    assert speech.model is None


def test_synthesized_speech_has_no_field_capable_of_encoding_a_command_or_execution() -> None:
    forbidden_substrings = (
        "command",
        "intent",
        "action",
        "tool",
        "exec",
        "sql",
        "shell",
        "url",
        "code",
    )
    field_names = set(SynthesizedSpeech.model_fields)
    for name in field_names:
        lowered = name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, f"field {name!r} looks like a command/execution field"


def test_synthesized_speech_has_no_dict_or_arbitrary_json_fields() -> None:
    for name, field in SynthesizedSpeech.model_fields.items():
        annotation_str = str(field.annotation)
        assert "dict" not in annotation_str.lower(), f"field {name!r} looks dict-shaped"
        assert "Any" not in annotation_str, f"field {name!r} accepts Any"
