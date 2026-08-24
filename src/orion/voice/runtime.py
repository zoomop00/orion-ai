"""The Mission 9 Phase 3 voice runtime: `VoiceRuntime.run_turn()` drives one
complete cycle of

    IDLE -> LISTENING -> UNDERSTANDING -> ANALYZING -> SPEAKING -> IDLE

by composing five injected dependencies -- `AudioInput`, `TranscriptionProvider`,
`ResponseProvider`, `SpeechSynthesisProvider`, `AudioOutput` -- every one of
them an ABC (`orion.voice.audio_input`/`orion.voice.provider_base`/
`orion.voice.response`/`orion.voice.audio_output`), never a concrete vendor
or hardware class. This module does not import `orion.voice.deepgram_stt`,
`orion.voice.deepgram_tts`, `sounddevice`, or `httpx` -- proven by
`tests/voice/test_import_hygiene.py`, not merely intended. Swapping Deepgram
for a different STT/TTS vendor, or a real microphone for a synthetic audio
source, means constructing `VoiceRuntime` with different arguments, never
editing this file -- matching every other composition point in this
codebase's provider-boundary discipline.

There is no orchestrator here and no attempt to build one. `VoiceRuntime`
sequences exactly five calls and nothing else: no memory, no database, no
tool invocation, no trading logic, no conversation history across turns.
Each `run_turn()` call is independent -- nothing from one call is retained
for the next (see the module docstring's "zero persistence" note in
`docs/DECISIONS.md`).

FAILURE RECOVERY: the entire body of `run_turn()` after the initial
`IDLE`-guard runs inside one `try`/`except Exception`. This is deliberately
broader than this codebase's usual narrow, typed `except` clauses (compare
`orion.voice.deepgram_stt`'s specific `except httpx.HTTPError`) -- the
requirement here is stronger than "handle known failure modes": *no*
exception, from *any* of the five dependencies, at *any* stage, may leave
`VoiceState` stuck outside `IDLE`. `orion.voice.state.VoiceStateMachine`'s
transition graph makes this provably correct rather than merely tested:
`IDLE` is a valid edge from every non-`IDLE` state (see that module's
docstring), so `reset_to_idle()` always succeeds regardless of which stage
failed. The original exception is always re-raised unchanged after
recovery -- a caller still learns exactly what failed and why; only the
runtime's own state is protected, never the error information.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from orion.voice.audio_input import AudioInput
from orion.voice.audio_output import AudioOutput
from orion.voice.provider_base import SpeechSynthesisProvider, TranscriptionProvider
from orion.voice.response import ResponseProvider
from orion.voice.schemas import SynthesizedSpeech, Transcript, VoiceState
from orion.voice.state import VoiceStateMachine


class VoiceRuntimeBusyError(RuntimeError):
    """Raised by `run_turn()` if the runtime is not currently `IDLE`.

    `VoiceRuntime` is a single synchronous session object with no
    concurrency support (see the module docstring) -- this guards against a
    caller invoking `run_turn()` reentrantly (e.g. from within a callback
    triggered by an earlier, still-in-progress turn) rather than against
    genuine multi-threaded access, which this class does not attempt to
    support.
    """


class VoiceTurnResult(BaseModel):
    """Everything one successful `run_turn()` produced, returned together
    so a caller (a future orchestrator, a test, or eventually Mission 10's
    interface) can inspect the transcript, the response text, and the
    synthesized audio without re-deriving any of them. Frozen and closed
    (`extra="forbid"`), matching every schema in this codebase.

    Never constructed on failure -- `run_turn()` raises instead (see the
    module docstring); a `VoiceTurnResult` is a complete, successful turn
    or it does not exist.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    transcript: Transcript
    response_text: str
    speech: SynthesizedSpeech


class VoiceRuntime:
    """Drives one complete voice turn from injected provider/audio-I/O
    dependencies. See the module docstring for the full architecture."""

    def __init__(
        self,
        *,
        audio_input: AudioInput,
        transcription: TranscriptionProvider,
        response_provider: ResponseProvider,
        synthesis: SpeechSynthesisProvider,
        audio_output: AudioOutput,
    ) -> None:
        self._audio_input = audio_input
        self._transcription = transcription
        self._response_provider = response_provider
        self._synthesis = synthesis
        self._audio_output = audio_output
        self._state_machine = VoiceStateMachine()

    @property
    def state(self) -> VoiceState:
        return self._state_machine.state

    def run_turn(self) -> VoiceTurnResult:
        """Run one complete IDLE -> ... -> IDLE voice turn.

        Raises VoiceRuntimeBusyError if not currently IDLE. Otherwise
        raises whichever of AudioInputError / TranscriptionProviderError /
        ResponseProviderError / SpeechSynthesisProviderError /
        AudioOutputError the failing stage raised, unchanged -- always
        after the state has already been returned to IDLE (see the module
        docstring). Returns a VoiceTurnResult only on complete success.
        """
        if self._state_machine.state is not VoiceState.IDLE:
            raise VoiceRuntimeBusyError(
                f"cannot start a new turn while in state {self._state_machine.state.value!r}"
            )

        self._state_machine.transition(VoiceState.LISTENING)
        try:
            audio = self._audio_input.capture()

            self._state_machine.transition(VoiceState.UNDERSTANDING)
            transcript = self._transcription.transcribe(audio)

            self._state_machine.transition(VoiceState.ANALYZING)
            response_text = self._response_provider.respond(transcript)

            self._state_machine.transition(VoiceState.SPEAKING)
            speech = self._synthesis.synthesize(response_text)
            self._audio_output.play(speech)
        except Exception:
            self._state_machine.reset_to_idle()
            raise

        self._state_machine.reset_to_idle()
        return VoiceTurnResult(transcript=transcript, response_text=response_text, speech=speech)
