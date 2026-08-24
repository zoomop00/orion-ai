from __future__ import annotations

import wave
from io import BytesIO
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from orion.voice.audio_input import (
    MAX_AUDIO_INPUT_BYTES,
    MAX_RECORDING_SECONDS,
    AudioInputError,
    InMemoryAudioInput,
    MicrophoneAudioInput,
)

VALID_AUDIO = b"\x00\x01" * 100


def test_in_memory_audio_input_returns_supplied_bytes() -> None:
    audio_input = InMemoryAudioInput(VALID_AUDIO)
    assert audio_input.capture() == VALID_AUDIO


def test_in_memory_audio_input_is_repeatable() -> None:
    audio_input = InMemoryAudioInput(VALID_AUDIO)
    assert audio_input.capture() == audio_input.capture()


def test_in_memory_audio_input_rejects_empty_audio() -> None:
    with pytest.raises(AudioInputError, match="empty"):
        InMemoryAudioInput(b"")


def test_in_memory_audio_input_rejects_oversized_audio() -> None:
    with pytest.raises(AudioInputError, match="exceeding"):
        InMemoryAudioInput(b"\x00" * (MAX_AUDIO_INPUT_BYTES + 1))


def test_in_memory_audio_input_accepts_maximum_valid_size() -> None:
    audio = b"\x00" * MAX_AUDIO_INPUT_BYTES
    assert InMemoryAudioInput(audio).capture() == audio


def test_microphone_audio_input_rejects_non_positive_duration() -> None:
    with pytest.raises(AudioInputError, match="positive"):
        MicrophoneAudioInput(duration_seconds=0)
    with pytest.raises(AudioInputError, match="positive"):
        MicrophoneAudioInput(duration_seconds=-1.0)


def test_microphone_audio_input_rejects_oversized_duration() -> None:
    with pytest.raises(AudioInputError, match="exceeds"):
        MicrophoneAudioInput(duration_seconds=MAX_RECORDING_SECONDS + 1)


def test_microphone_audio_input_accepts_maximum_duration() -> None:
    # Should not raise at construction time.
    MicrophoneAudioInput(duration_seconds=MAX_RECORDING_SECONDS)


def test_microphone_audio_input_rejects_non_positive_sample_rate() -> None:
    with pytest.raises(AudioInputError, match="sample_rate"):
        MicrophoneAudioInput(sample_rate=0)


def _fake_sounddevice_module(samples: np.ndarray) -> MagicMock:
    fake_sd = MagicMock()
    fake_sd.rec.return_value = samples
    fake_sd.wait.return_value = None
    return fake_sd


def test_microphone_audio_input_captures_and_wraps_as_wav() -> None:
    sample_rate = 16_000
    duration = 1.0
    frames = int(sample_rate * duration)
    samples = np.zeros((frames, 1), dtype="int16")
    fake_sd = _fake_sounddevice_module(samples)

    with patch.dict("sys.modules", {"sounddevice": fake_sd}):
        audio_input = MicrophoneAudioInput(duration_seconds=duration, sample_rate=sample_rate)
        captured = audio_input.capture()

    assert captured  # non-empty
    with wave.open(BytesIO(captured), "rb") as wav_file:
        assert wav_file.getframerate() == sample_rate
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == frames

    fake_sd.rec.assert_called_once()
    fake_sd.wait.assert_called_once()


def test_microphone_audio_input_wraps_backend_import_failure() -> None:
    with patch.dict("sys.modules", {"sounddevice": None}):
        audio_input = MicrophoneAudioInput(duration_seconds=1.0)
        with pytest.raises(AudioInputError, match="unavailable"):
            audio_input.capture()


def test_microphone_audio_input_wraps_capture_failure() -> None:
    fake_sd = MagicMock()
    fake_sd.rec.side_effect = RuntimeError("PortAudio device unavailable")

    with patch.dict("sys.modules", {"sounddevice": fake_sd}):
        audio_input = MicrophoneAudioInput(duration_seconds=1.0)
        with pytest.raises(AudioInputError, match="capture failed"):
            audio_input.capture()


def test_microphone_audio_input_zero_frame_capture_still_yields_a_valid_wav_header() -> None:
    # A WAV header alone is never empty bytes (_validate_captured_audio's
    # empty-audio check cannot fire here), so a zero-frame capture from the
    # backend still produces a structurally valid, silent WAV rather than
    # raising -- documented explicitly rather than left as an untested edge.
    samples = np.zeros((0, 1), dtype="int16")
    fake_sd = _fake_sounddevice_module(samples)

    with patch.dict("sys.modules", {"sounddevice": fake_sd}):
        audio_input = MicrophoneAudioInput(duration_seconds=1.0)
        captured = audio_input.capture()

    with wave.open(BytesIO(captured), "rb") as wav_file:
        assert wav_file.getnframes() == 0
