"""Audio-input abstraction for Mission 9 Phase 3: `AudioInput.capture() ->
bytes` is the one boundary the voice runtime (`orion.voice.runtime`) uses to
get one complete utterance of raw audio, regardless of where it came from.
Mirrors the "one ABC per concern" precedent used throughout this codebase
(`orion.data.provider_base.MarketDataProvider`, `orion.voice.provider_base.
TranscriptionProvider`, etc.) -- a future streaming/VAD-based capture
implementation is a new subclass here, never a change to the runtime.

Only this file (and `orion.voice.audio_output`) may import `sounddevice`
inside `orion.voice`, enforced by `tests/voice/test_import_hygiene.py` the
same way `httpx` is confined to `deepgram_stt.py`/`deepgram_tts.py`. The
import is deferred to inside `MicrophoneAudioInput`'s methods, not made at
module level: `sounddevice` requires a working PortAudio binding, which can
fail to load in an environment with the package installed but no usable
audio subsystem (a headless CI container, for instance) -- deferring the
import means merely importing this module, or constructing/using
`InMemoryAudioInput`, never requires a working audio backend at all.

`capture()` returns a single complete utterance's audio, matching
`TranscriptionProvider.transcribe(audio: bytes)`'s existing contract
exactly (opaque, vendor-agnostic bytes, no assumption about container/
codec) -- `orion.voice.runtime.VoiceRuntime` passes this return value
straight into a `TranscriptionProvider` unmodified.

SIZE/DURATION LIMITS: `MAX_AUDIO_INPUT_BYTES` intentionally matches
`orion.voice.deepgram_stt.MAX_AUDIO_BYTES`'s numeric value (25 MB) without
importing it -- this module has no vendor dependency (see the module
docstring's import-boundary note above), so the limit is declared
independently rather than coupling this vendor-agnostic abstraction to one
STT vendor's constant. The intent is that this layer's own limit is never
the looser one: rejecting oversized/empty audio here, before a
`TranscriptionProvider` is ever called, matches `orion.analysts.
claude_macro_provider`'s "reject, before any request, never silently
truncate" discipline, applied one layer earlier than Phase 2 already
applies it.

PRIVACY: audio is processed entirely in memory. `MicrophoneAudioInput`
never writes a temporary file -- `sounddevice.rec()` returns a numpy array
directly, and `_wrap_pcm_as_wav` builds the WAV container in an in-memory
`io.BytesIO` buffer (stdlib `wave`, no new dependency for the container
format itself). Nothing captured here is retained beyond the single
`capture()` call that produced it -- no history, no cache, no file.
"""

from __future__ import annotations

import io
import wave
from abc import ABC, abstractmethod

DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_RECORDING_SECONDS = 6.0

# A conventional, not-vendor-calibrated ceiling for "one utterance" -- see
# Transcript.text's identical "generously long, not vendor-calibrated"
# 2000-char cap (orion.voice.schemas) for the same style of starting point.
MAX_RECORDING_SECONDS = 30.0
MAX_AUDIO_INPUT_BYTES = 25 * 1024 * 1024


class AudioInputError(RuntimeError):
    """Raised when one utterance of audio cannot be captured.

    Never caught and papered over with empty/placeholder/silent audio --
    matching every provider error in this codebase's identical "failure is
    not a verdict" doctrine (see `orion.voice.provider_base`).
    """


class AudioInput(ABC):
    """A source of one complete utterance of raw audio."""

    @abstractmethod
    def capture(self) -> bytes:
        """Capture and return one complete utterance as raw audio bytes.

        Must raise AudioInputError (never return empty or fabricated audio)
        if capture fails for any reason -- hardware unavailable, capture
        error, or the result violates a size/duration limit.
        """
        raise NotImplementedError


def _validate_captured_audio(audio: bytes) -> None:
    if not audio:
        raise AudioInputError("captured audio is empty")
    if len(audio) > MAX_AUDIO_INPUT_BYTES:
        raise AudioInputError(
            f"captured audio is {len(audio)} bytes, exceeding the "
            f"{MAX_AUDIO_INPUT_BYTES}-byte limit"
        )


class InMemoryAudioInput(AudioInput):
    """Returns a fixed, caller-supplied byte string on every `capture()`.

    The test/dev double for `AudioInput` -- mirrors `InMemoryPortfolioStore`
    (`orion.paper.store`)/`InMemoryHistoricalBarSource` (`orion.backtest.
    data`)'s identical "ship a real, deterministic in-memory implementation
    next to the ABC" precedent, not a `unittest.mock` stand-in invented per
    test file.
    """

    def __init__(self, audio: bytes) -> None:
        _validate_captured_audio(audio)
        self._audio = audio

    def capture(self) -> bytes:
        return self._audio


def _wrap_pcm_as_wav(
    pcm_bytes: bytes, *, sample_rate: int, channels: int, sample_width: int
) -> bytes:
    """Wrap headerless PCM samples in a standard RIFF/WAVE container, built
    entirely in memory (`io.BytesIO`, stdlib `wave` -- no temp file, no new
    dependency). A WAV header is what lets `TranscriptionProvider`
    implementations auto-detect sample rate/format from the bytes alone,
    the same self-describing-container approach `orion.voice.deepgram_tts`
    uses for its own output (see that module's docstring)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


class MicrophoneAudioInput(AudioInput):
    """Real local microphone capture via `sounddevice` (PortAudio).

    Fixed-duration blocking capture -- records exactly `duration_seconds`
    of audio, then returns. No voice-activity detection, no early stop on
    silence, no streaming. This is a deliberate Phase 3 simplification, not
    an oversight: `ORION_MASTER_ENGINEERING_BRIEF_V2.md` explicitly defers
    interruption/streaming, and "record a fixed window, then process it" is
    the smallest robust way to prove the physical mic -> STT path (see
    `docs/DECISIONS.md`).
    """

    def __init__(
        self,
        *,
        duration_seconds: float = DEFAULT_RECORDING_SECONDS,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        device: int | str | None = None,
    ) -> None:
        if duration_seconds <= 0:
            raise AudioInputError(f"duration_seconds must be positive, got {duration_seconds}")
        if duration_seconds > MAX_RECORDING_SECONDS:
            raise AudioInputError(
                f"duration_seconds {duration_seconds} exceeds the "
                f"{MAX_RECORDING_SECONDS}-second limit"
            )
        if sample_rate <= 0:
            raise AudioInputError(f"sample_rate must be positive, got {sample_rate}")
        self._duration_seconds = duration_seconds
        self._sample_rate = sample_rate
        self._device = device

    def capture(self) -> bytes:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            # No credential/secret is ever involved in local hardware
            # access, unlike orion.voice.deepgram_stt/deepgram_tts -- str(exc)
            # is safe to include here.
            raise AudioInputError(f"local microphone backend unavailable: {exc}") from exc

        frames = max(1, round(self._duration_seconds * self._sample_rate))
        try:
            recording = sd.rec(
                frames,
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                device=self._device,
            )
            sd.wait()
        except Exception as exc:
            # Deliberately broad: PortAudio/sounddevice can raise several
            # different, not-narrowly-typed error classes (PortAudioError,
            # OSError, sounddevice's own internal errors) for hardware
            # failures, and every one of them must become AudioInputError
            # -- never propagate as a raw, untyped exception the runtime
            # would not recognize as a recoverable capture failure.
            raise AudioInputError(f"microphone capture failed: {exc}") from exc

        audio = _wrap_pcm_as_wav(
            recording.tobytes(), sample_rate=self._sample_rate, channels=1, sample_width=2
        )
        _validate_captured_audio(audio)
        return audio
