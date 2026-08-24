"""Audio-output abstraction for Mission 9 Phase 3: `AudioOutput.play
(speech: SynthesizedSpeech) -> None` is the one boundary the voice runtime
(`orion.voice.runtime`) uses to turn synthesized speech into sound,
regardless of where that sound goes. Mirrors `orion.voice.audio_input`'s
identical design (see that module's docstring for the shared "one ABC per
concern"/deferred-import/import-boundary rationale, which applies here
unchanged).

`play` takes the whole validated `SynthesizedSpeech` object, not just
`speech.audio` -- `.format` is required to interpret the bytes at all (raw
PCM has no self-describing header the way a WAV/MP3 file does), so a
narrower `play(audio: bytes)` signature would force every implementation to
either guess the format or silently assume one. Passing the full object
matches this codebase's established "pass the validated object, not a raw
field" convention (`orion.analysts.provider_base.MacroAnalystProvider.
analyze(snapshot)`, `orion.voice.response.ResponseProvider.respond
(transcript)`).

FORMAT CONTRACT: `SpeakerAudioOutput` only supports `speech.format == "wav"`
-- a complete RIFF/WAVE file, self-describing its own sample rate/channels/
bit depth, decoded with the stdlib `wave` module (no new dependency for
container parsing). `orion.voice.deepgram_tts.DeepgramSpeechSynthesisProvider`
was changed in this same phase to produce exactly that (`encoding=linear16`,
`container=wav`) instead of Phase 2's original `mp3` default -- avoiding
the need for an MP3-decoding dependency purely to support real local
playback (see `docs/DECISIONS.md`). Any other `format` value fails closed
with `AudioOutputError` rather than guessing a decoder.
"""

from __future__ import annotations

import io
import wave
from abc import ABC, abstractmethod

from orion.voice.schemas import SynthesizedSpeech

_SUPPORTED_FORMAT = "wav"
_SAMPLE_WIDTH_TO_DTYPE = {1: "int8", 2: "int16", 4: "int32"}


class AudioOutputError(RuntimeError):
    """Raised when synthesized speech cannot be played.

    Never caught and papered over -- matching every provider error in this
    codebase's identical "failure is not a verdict" doctrine (see
    `orion.voice.provider_base`).
    """


class AudioOutput(ABC):
    """A destination that can play back one piece of synthesized speech."""

    @abstractmethod
    def play(self, speech: SynthesizedSpeech) -> None:
        """Play `speech` to completion (or fail).

        Must raise AudioOutputError (never silently do nothing, and never
        raise a raw, untyped exception) if `speech.audio` is empty, its
        `format` is unsupported, or playback itself fails.
        """
        raise NotImplementedError


class InMemoryAudioOutput(AudioOutput):
    """Records every `SynthesizedSpeech` "played" into `self.played`
    instead of emitting sound.

    The test/dev double for `AudioOutput`, mirroring `orion.voice.
    audio_input.InMemoryAudioInput`'s identical "ship a real, deterministic
    in-memory implementation next to the ABC" precedent. `self.played` is a
    plain in-process Python list, garbage-collected with the object -- not
    persistence in this codebase's sense of the word (durable storage); see
    `docs/DECISIONS.md`.
    """

    def __init__(self) -> None:
        self.played: list[SynthesizedSpeech] = []

    def play(self, speech: SynthesizedSpeech) -> None:
        if not speech.audio:
            raise AudioOutputError("synthesized audio is empty")
        self.played.append(speech)


class SpeakerAudioOutput(AudioOutput):
    """Real local playback via `sounddevice` (PortAudio).

    Blocking: `play()` does not return until playback finishes (`sd.wait()`
    after `sd.play()`), matching `MicrophoneAudioInput`'s equally blocking
    `capture()` -- Phase 3's single-utterance-turn design has no concurrent
    audio I/O to coordinate.
    """

    def play(self, speech: SynthesizedSpeech) -> None:
        if not speech.audio:
            raise AudioOutputError("synthesized audio is empty")
        if speech.format != _SUPPORTED_FORMAT:
            raise AudioOutputError(
                f"unsupported audio format for local playback: {speech.format!r} "
                f"(only {_SUPPORTED_FORMAT!r} is supported)"
            )

        try:
            import numpy as np
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise AudioOutputError(f"local speaker backend unavailable: {exc}") from exc

        try:
            with wave.open(io.BytesIO(speech.audio), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                frames = wav_file.readframes(frame_count)
        except wave.Error as exc:
            raise AudioOutputError(f"synthesized audio is not a valid WAV file: {exc}") from exc

        dtype = _SAMPLE_WIDTH_TO_DTYPE.get(sample_width)
        if dtype is None:
            raise AudioOutputError(f"unsupported WAV sample width: {sample_width} bytes")
        if channels < 1:
            raise AudioOutputError(f"unsupported WAV channel count: {channels}")

        try:
            samples = np.frombuffer(frames, dtype=dtype).reshape(-1, channels)
            sd.play(samples, samplerate=sample_rate)
            sd.wait()
        except Exception as exc:
            # Deliberately broad -- see MicrophoneAudioInput.capture's
            # identical rationale: PortAudio/sounddevice/numpy failures are
            # not uniformly typed, and every one must become AudioOutputError.
            raise AudioOutputError(f"playback failed: {exc}") from exc
