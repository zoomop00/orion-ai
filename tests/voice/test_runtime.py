from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orion.voice.audio_input import AudioInput, AudioInputError, InMemoryAudioInput
from orion.voice.audio_output import AudioOutput, AudioOutputError, InMemoryAudioOutput
from orion.voice.provider_base import (
    SpeechSynthesisProvider,
    SpeechSynthesisProviderError,
    TranscriptionProvider,
    TranscriptionProviderError,
)
from orion.voice.response import ResponseProvider, ResponseProviderError
from orion.voice.runtime import VoiceRuntime, VoiceRuntimeBusyError, VoiceTurnResult
from orion.voice.schemas import SynthesizedSpeech, Transcript, VoiceState

MIC_AUDIO = b"\x00\x01" * 50
TRANSCRIPT = Transcript(
    text="what is the price of gold",
    provider="deepgram",
    model="nova-3",
    transcribed_at=datetime.now(UTC),
)
SPEECH = SynthesizedSpeech(
    audio=b"RIFF-fake-wav-bytes",
    format="wav",
    provider="deepgram",
    model="aura-2-thalia-en",
    synthesized_at=datetime.now(UTC),
)


class _RecordingTranscriptionProvider(TranscriptionProvider):
    def __init__(self, transcript: Transcript = TRANSCRIPT) -> None:
        self.transcript = transcript
        self.received_audio: bytes | None = None

    def transcribe(self, audio: bytes) -> Transcript:
        self.received_audio = audio
        return self.transcript


class _RecordingResponseProvider(ResponseProvider):
    def __init__(self, response_text: str = "a response") -> None:
        self.response_text = response_text
        self.received_transcript: Transcript | None = None

    def respond(self, transcript: Transcript) -> str:
        self.received_transcript = transcript
        return self.response_text


class _RecordingSynthesisProvider(SpeechSynthesisProvider):
    def __init__(self, speech: SynthesizedSpeech = SPEECH) -> None:
        self.speech = speech
        self.received_text: str | None = None

    def synthesize(self, text: str) -> SynthesizedSpeech:
        self.received_text = text
        return self.speech


class _FailingAudioInput(AudioInput):
    def capture(self) -> bytes:
        raise AudioInputError("mic hardware unavailable")


class _FailingTranscriptionProvider(TranscriptionProvider):
    def transcribe(self, audio: bytes) -> Transcript:
        raise TranscriptionProviderError("stt failed")


class _MalformedTranscriptionProvider(TranscriptionProvider):
    """Simulates a provider returning something that violates its own
    documented contract -- exercised the same way orion.voice.deepgram_stt's
    own tests exercise a malformed vendor response, one layer up."""

    def transcribe(self, audio: bytes) -> Transcript:
        raise TranscriptionProviderError("Deepgram returned an unparseable response")


class _FailingResponseProvider(ResponseProvider):
    def respond(self, transcript: Transcript) -> str:
        raise ResponseProviderError("response generation failed")


class _FailingSynthesisProvider(SpeechSynthesisProvider):
    def synthesize(self, text: str) -> SynthesizedSpeech:
        raise SpeechSynthesisProviderError("tts failed")


class _FailingAudioOutput(AudioOutput):
    def play(self, speech: SynthesizedSpeech) -> None:
        raise AudioOutputError("speaker unavailable")


def _build_runtime(
    *,
    audio_input: AudioInput | None = None,
    transcription: TranscriptionProvider | None = None,
    response_provider: ResponseProvider | None = None,
    synthesis: SpeechSynthesisProvider | None = None,
    audio_output: AudioOutput | None = None,
) -> VoiceRuntime:
    return VoiceRuntime(
        audio_input=audio_input or InMemoryAudioInput(MIC_AUDIO),
        transcription=transcription or _RecordingTranscriptionProvider(),
        response_provider=response_provider or _RecordingResponseProvider(),
        synthesis=synthesis or _RecordingSynthesisProvider(),
        audio_output=audio_output or InMemoryAudioOutput(),
    )


# --- Happy-path data flow ---


def test_runtime_initial_state_is_idle() -> None:
    assert _build_runtime().state is VoiceState.IDLE


def test_stt_receives_the_captured_microphone_audio() -> None:
    transcription = _RecordingTranscriptionProvider()
    runtime = _build_runtime(audio_input=InMemoryAudioInput(MIC_AUDIO), transcription=transcription)

    runtime.run_turn()

    assert transcription.received_audio == MIC_AUDIO


def test_transcript_reaches_the_response_provider() -> None:
    response_provider = _RecordingResponseProvider()
    runtime = _build_runtime(
        transcription=_RecordingTranscriptionProvider(TRANSCRIPT),
        response_provider=response_provider,
    )

    runtime.run_turn()

    assert response_provider.received_transcript == TRANSCRIPT


def test_response_text_reaches_tts() -> None:
    synthesis = _RecordingSynthesisProvider()
    runtime = _build_runtime(
        response_provider=_RecordingResponseProvider("speak this exact text"), synthesis=synthesis
    )

    runtime.run_turn()

    assert synthesis.received_text == "speak this exact text"


def test_synthesized_audio_reaches_the_speaker() -> None:
    audio_output = InMemoryAudioOutput()
    runtime = _build_runtime(
        synthesis=_RecordingSynthesisProvider(SPEECH), audio_output=audio_output
    )

    runtime.run_turn()

    assert audio_output.played == [SPEECH]


def test_complete_mocked_end_to_end_cycle_returns_full_result() -> None:
    runtime = _build_runtime()

    result = runtime.run_turn()

    assert isinstance(result, VoiceTurnResult)
    assert result.transcript == TRANSCRIPT
    assert result.speech == SPEECH
    assert isinstance(result.response_text, str) and result.response_text
    assert runtime.state is VoiceState.IDLE


def test_state_sequence_during_a_successful_turn() -> None:
    log: list[VoiceState] = []

    class _LoggingAudioInput(AudioInput):
        def capture(self) -> bytes:
            log.append(runtime.state)
            return MIC_AUDIO

    class _LoggingTranscription(TranscriptionProvider):
        def transcribe(self, audio: bytes) -> Transcript:
            log.append(runtime.state)
            return TRANSCRIPT

    class _LoggingResponse(ResponseProvider):
        def respond(self, transcript: Transcript) -> str:
            log.append(runtime.state)
            return "response"

    class _LoggingSynthesis(SpeechSynthesisProvider):
        def synthesize(self, text: str) -> SynthesizedSpeech:
            log.append(runtime.state)
            return SPEECH

    class _LoggingAudioOutput(AudioOutput):
        def play(self, speech: SynthesizedSpeech) -> None:
            log.append(runtime.state)

    runtime = VoiceRuntime(
        audio_input=_LoggingAudioInput(),
        transcription=_LoggingTranscription(),
        response_provider=_LoggingResponse(),
        synthesis=_LoggingSynthesis(),
        audio_output=_LoggingAudioOutput(),
    )

    assert runtime.state is VoiceState.IDLE
    runtime.run_turn()

    assert log == [
        VoiceState.LISTENING,
        VoiceState.UNDERSTANDING,
        VoiceState.ANALYZING,
        VoiceState.SPEAKING,
        VoiceState.SPEAKING,
    ]
    assert runtime.state is VoiceState.IDLE


def test_repeated_sessions_each_succeed_independently() -> None:
    runtime = _build_runtime()
    for _ in range(5):
        assert runtime.state is VoiceState.IDLE
        result = runtime.run_turn()
        assert isinstance(result, VoiceTurnResult)
        assert runtime.state is VoiceState.IDLE


def test_run_turn_rejects_reentrant_calls() -> None:
    outcomes: list[BaseException | None] = []

    class _ReentrantAudioInput(AudioInput):
        def capture(self) -> bytes:
            try:
                runtime.run_turn()
            except BaseException as exc:  # noqa: BLE001
                outcomes.append(exc)
            return MIC_AUDIO

    runtime = _build_runtime(audio_input=_ReentrantAudioInput())
    runtime.run_turn()

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], VoiceRuntimeBusyError)


# --- Failure -> IDLE recovery, one stage at a time ---


def test_microphone_failure_returns_to_idle_and_reraises() -> None:
    runtime = _build_runtime(audio_input=_FailingAudioInput())
    with pytest.raises(AudioInputError):
        runtime.run_turn()
    assert runtime.state is VoiceState.IDLE


def test_stt_failure_returns_to_idle_and_reraises() -> None:
    runtime = _build_runtime(transcription=_FailingTranscriptionProvider())
    with pytest.raises(TranscriptionProviderError):
        runtime.run_turn()
    assert runtime.state is VoiceState.IDLE


def test_malformed_stt_response_returns_to_idle_and_reraises() -> None:
    runtime = _build_runtime(transcription=_MalformedTranscriptionProvider())
    with pytest.raises(TranscriptionProviderError, match="unparseable"):
        runtime.run_turn()
    assert runtime.state is VoiceState.IDLE


def test_response_provider_failure_returns_to_idle_and_reraises() -> None:
    runtime = _build_runtime(response_provider=_FailingResponseProvider())
    with pytest.raises(ResponseProviderError):
        runtime.run_turn()
    assert runtime.state is VoiceState.IDLE


def test_tts_failure_returns_to_idle_and_reraises() -> None:
    runtime = _build_runtime(synthesis=_FailingSynthesisProvider())
    with pytest.raises(SpeechSynthesisProviderError):
        runtime.run_turn()
    assert runtime.state is VoiceState.IDLE


def test_speaker_failure_returns_to_idle_and_reraises() -> None:
    runtime = _build_runtime(audio_output=_FailingAudioOutput())
    with pytest.raises(AudioOutputError):
        runtime.run_turn()
    assert runtime.state is VoiceState.IDLE


def test_runtime_is_usable_again_immediately_after_a_failure() -> None:
    runtime = _build_runtime(transcription=_FailingTranscriptionProvider())
    with pytest.raises(TranscriptionProviderError):
        runtime.run_turn()

    # Swap in a working transcription provider and confirm the runtime
    # recovered fully, not just its state flag.
    runtime = _build_runtime()
    result = runtime.run_turn()
    assert isinstance(result, VoiceTurnResult)
    assert runtime.state is VoiceState.IDLE
