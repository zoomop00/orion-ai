"""A turn that completes without being spoken.

WHY THIS EXISTS. `VoiceRuntime` used to require a `SpeechSynthesisProvider`
and an `AudioOutput`, so answering a *typed* question insisted on speaking
the answer -- and a deployment with no text-to-speech vendor could not reach
analysis, risk or macro at all. ORION's reply is text first; the audio is a
second rendering of it.

WHAT DID NOT CHANGE. `orion.voice.state`'s transition graph is untouched.
`ANALYZING -> IDLE` was always legal, because failure recovery needed an
`IDLE` edge out of every state; an unspoken turn simply takes that edge on
success too.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orion.voice.audio_input import AudioInput
from orion.voice.audio_output import AudioOutput
from orion.voice.provider_base import SpeechSynthesisProvider, TranscriptionProvider
from orion.voice.response import ResponseProvider, StaticResponseProvider
from orion.voice.runtime import VoiceRuntime
from orion.voice.schemas import SynthesizedSpeech, Transcript, VoiceState

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class FixedAudioInput(AudioInput):
    def capture(self) -> bytes:
        return bytes([0, 1])


class FixedTranscription(TranscriptionProvider):
    def transcribe(self, audio: bytes) -> Transcript:
        return Transcript(text="analyze gold", provider="test", transcribed_at=NOW)


class RecordingSynthesis(SpeechSynthesisProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str) -> SynthesizedSpeech:
        self.calls.append(text)
        return SynthesizedSpeech(audio=b"RIFF", format="wav", provider="test", synthesized_at=NOW)


class RecordingOutput(AudioOutput):
    def __init__(self) -> None:
        self.played: list[SynthesizedSpeech] = []

    def play(self, speech: SynthesizedSpeech) -> None:
        self.played.append(speech)


def build(
    *,
    synthesis: SpeechSynthesisProvider | None = None,
    audio_output: AudioOutput | None = None,
    response_provider: ResponseProvider | None = None,
) -> VoiceRuntime:
    return VoiceRuntime(
        audio_input=FixedAudioInput(),
        transcription=FixedTranscription(),
        response_provider=response_provider or StaticResponseProvider(),
        synthesis=synthesis,
        audio_output=audio_output,
    )


class TestAnUnspokenTurn:
    def test_completes_and_returns_a_transcript_and_a_reply(self) -> None:
        result = build().run_turn()

        assert result.transcript.text == "analyze gold"
        assert len(result.response_text) > 0
        assert result.speech is None

    def test_ends_in_idle_without_ever_entering_speaking(self) -> None:
        runtime = build()

        runtime.run_turn()

        assert runtime.state is VoiceState.IDLE

    def test_reports_that_it_does_not_speak(self) -> None:
        assert build().speaks is False
        assert build(synthesis=RecordingSynthesis(), audio_output=RecordingOutput()).speaks is True

    def test_the_runtime_is_reusable_afterwards(self) -> None:
        """The state machine is genuinely back at IDLE, not merely reported."""
        runtime = build()

        first = runtime.run_turn()
        second = runtime.run_turn()

        assert first.speech is None and second.speech is None


class TestASpokenTurnIsUnchanged:
    def test_still_synthesizes_and_plays(self) -> None:
        synthesis = RecordingSynthesis()
        output = RecordingOutput()

        result = build(synthesis=synthesis, audio_output=output).run_turn()

        assert result.speech is not None
        assert result.speech.audio == b"RIFF"
        assert synthesis.calls == [result.response_text]
        assert len(output.played) == 1

    def test_ends_in_idle_as_it_always_did(self) -> None:
        runtime = build(synthesis=RecordingSynthesis(), audio_output=RecordingOutput())

        runtime.run_turn()

        assert runtime.state is VoiceState.IDLE


class TestHalfWiringIsRefused:
    @pytest.mark.parametrize(
        ("synthesis", "audio_output"),
        [
            (RecordingSynthesis(), None),
            (None, RecordingOutput()),
        ],
    )
    def test_one_without_the_other_fails_at_construction(
        self, synthesis: SpeechSynthesisProvider | None, audio_output: AudioOutput | None
    ) -> None:
        """A runtime that would fail halfway through SPEAKING is refused
        before it can be handed a turn."""
        with pytest.raises(ValueError, match="together or not at all"):
            build(synthesis=synthesis, audio_output=audio_output)


class TestNothingIsFabricated:
    def test_no_zero_length_audio_is_ever_produced(self) -> None:
        """`SynthesizedSpeech.audio` keeps `min_length=1` precisely because an
        unspoken turn says `None` instead of inventing silence."""
        result = build().run_turn()

        assert result.speech is None
        with pytest.raises(ValueError):
            SynthesizedSpeech(audio=b"", format="wav", provider="x", synthesized_at=NOW)
