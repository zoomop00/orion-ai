from __future__ import annotations

import io
import wave
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from orion.voice.audio_output import AudioOutputError, InMemoryAudioOutput, SpeakerAudioOutput
from orion.voice.schemas import SynthesizedSpeech


def _wav_bytes(*, frames: int = 100, sample_rate: int = 24_000, channels: int = 1) -> bytes:
    samples = np.zeros((frames, channels), dtype="int16")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())
    return buffer.getvalue()


def _speech(audio: bytes = b"", *, fmt: str = "wav") -> SynthesizedSpeech:
    return SynthesizedSpeech(
        audio=audio or _wav_bytes(),
        format=fmt,
        provider="deepgram",
        model="aura-2-thalia-en",
        synthesized_at=datetime.now(UTC),
    )


def _speech_with_empty_audio() -> SynthesizedSpeech:
    # SynthesizedSpeech.audio has min_length=1, so a normally-constructed
    # instance can never carry empty audio -- model_construct() bypasses
    # that validation deliberately, to prove AudioOutput's own explicit
    # empty-audio check (defense in depth, not the schema's job alone)
    # actually fires rather than being dead code.
    return SynthesizedSpeech.model_construct(
        audio=b"",
        format="wav",
        provider="deepgram",
        model=None,
        synthesized_at=datetime.now(UTC),
    )


def test_in_memory_audio_output_rejects_empty_audio() -> None:
    with pytest.raises(AudioOutputError, match="empty"):
        InMemoryAudioOutput().play(_speech_with_empty_audio())


def test_speaker_audio_output_rejects_empty_audio() -> None:
    with pytest.raises(AudioOutputError, match="empty"):
        SpeakerAudioOutput().play(_speech_with_empty_audio())


def test_in_memory_audio_output_records_played_speech() -> None:
    output = InMemoryAudioOutput()
    speech = _speech()

    output.play(speech)

    assert output.played == [speech]


def test_in_memory_audio_output_records_multiple_plays_in_order() -> None:
    output = InMemoryAudioOutput()
    first = _speech(_wav_bytes(frames=10))
    second = _speech(_wav_bytes(frames=20))

    output.play(first)
    output.play(second)

    assert output.played == [first, second]


def test_speaker_audio_output_rejects_unsupported_format() -> None:
    # SynthesizedSpeech itself allows any non-empty format string; only
    # SpeakerAudioOutput enforces "wav only" for real local playback.
    speech = SynthesizedSpeech(
        audio=b"not-actually-audio",
        format="mp3",
        provider="deepgram",
        model="aura-2-thalia-en",
        synthesized_at=datetime.now(UTC),
    )
    with pytest.raises(AudioOutputError, match="unsupported audio format"):
        SpeakerAudioOutput().play(speech)


def test_speaker_audio_output_rejects_malformed_wav_bytes() -> None:
    speech = SynthesizedSpeech(
        audio=b"not a real wav file",
        format="wav",
        provider="deepgram",
        model="aura-2-thalia-en",
        synthesized_at=datetime.now(UTC),
    )
    with pytest.raises(AudioOutputError, match="not a valid WAV file"):
        SpeakerAudioOutput().play(speech)


def test_speaker_audio_output_plays_valid_wav_via_sounddevice() -> None:
    fake_sd = MagicMock()
    fake_sd.play.return_value = None
    fake_sd.wait.return_value = None
    speech = _speech(_wav_bytes(frames=50, sample_rate=24_000))

    with patch.dict("sys.modules", {"sounddevice": fake_sd}):
        SpeakerAudioOutput().play(speech)

    fake_sd.play.assert_called_once()
    played_samples, played_kwargs = fake_sd.play.call_args
    assert played_samples[0].shape == (50, 1)
    assert played_kwargs["samplerate"] == 24_000
    fake_sd.wait.assert_called_once()


def test_speaker_audio_output_wraps_backend_import_failure() -> None:
    speech = _speech()
    with (
        patch.dict("sys.modules", {"sounddevice": None}),
        pytest.raises(AudioOutputError, match="unavailable"),
    ):
        SpeakerAudioOutput().play(speech)


def test_speaker_audio_output_wraps_playback_failure() -> None:
    fake_sd = MagicMock()
    fake_sd.play.side_effect = RuntimeError("PortAudio output device unavailable")
    speech = _speech()

    with (
        patch.dict("sys.modules", {"sounddevice": fake_sd}),
        pytest.raises(AudioOutputError, match="playback failed"),
    ):
        SpeakerAudioOutput().play(speech)


def test_speaker_audio_output_rejects_unsupported_sample_width() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(3)  # 24-bit -- not in _SAMPLE_WIDTH_TO_DTYPE
        wav_file.setframerate(24_000)
        wav_file.writeframes(b"\x00\x00\x00" * 10)
    speech = _speech(buffer.getvalue())

    with pytest.raises(AudioOutputError, match="sample width"):
        SpeakerAudioOutput().play(speech)
