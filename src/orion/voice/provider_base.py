"""Abstract interfaces the two Mission 9 (Voice) provider concerns must
implement: `TranscriptionProvider` (speech -> text) and
`SpeechSynthesisProvider` (text -> speech). Deliberately two separate ABCs,
not one "VoiceProvider" with a mode, mirroring the "one ABC per concern"
precedent already used seven times in this codebase (`orion.data.
provider_base.MarketDataProvider`, `orion.research.provider_base.
EconomicCalendarProvider`, `orion.reasoning.provider_base.ReasoningProvider`,
`orion.analysts.provider_base.MacroAnalystProvider`, `orion.backtest.data.
HistoricalBarSource`, `orion.paper.store.PortfolioStore`, `orion.memory.
journal.DecisionJournal`) — speech-to-text and text-to-speech are
structurally independent concerns with independent failure modes, and a
single vendor need not offer both. Both ABCs live in this one file only
because the approved Phase 1 file list keeps `orion.voice` to exactly
`__init__.py`/`schemas.py`/`provider_base.py`; they remain two separate,
independently-named contracts, each with its own error hierarchy.

No concrete implementation exists yet — vendor selection is a deliberately
separate, later decision (see `docs/DECISIONS.md`). The rest of ORION
(once a future orchestrator exists) would depend only on these interfaces,
never a specific vendor's SDK — swapping providers means writing a new
class here, not touching a caller, matching every other provider boundary
in this codebase.

**Failure is not a verdict**, matching every other provider in this
codebase's identical doctrine (`ReasoningProviderError`,
`MacroAnalystProviderError`, `MarketDataProviderError`,
`EconomicCalendarProviderError`): a provider that cannot fulfil a request
raises its own error, never a fabricated or empty result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orion.voice.schemas import SynthesizedSpeech, Transcript


class TranscriptionProviderError(RuntimeError):
    """Raised when a provider cannot return a validated Transcript.

    Never caught and papered over with a fabricated or empty transcript —
    callers must treat this as "no speech recognized" and handle it
    explicitly. No speech detected at all is this error, not a placeholder
    Transcript with empty text (`Transcript.text` requires at least one
    character).
    """


class TranscriptionProvider(ABC):
    """A source of speech-to-text transcription for one complete utterance
    of audio."""

    @abstractmethod
    def transcribe(self, audio: bytes) -> Transcript:
        """Turn one complete utterance of raw audio bytes into a single
        validated Transcript.

        `audio` is opaque, vendor-agnostic raw bytes — this interface makes
        no assumption about container format, sample rate, or encoding; a
        concrete implementation is responsible for whatever conversion its
        own vendor API requires.

        Must raise TranscriptionProviderError (never return a placeholder
        or fabricated transcript) if the provider cannot fulfil the
        request for any reason — no speech detected, transport failure, or
        output that does not satisfy the Transcript schema.
        """
        raise NotImplementedError


class SpeechSynthesisProviderError(RuntimeError):
    """Raised when a provider cannot return validated synthesized speech.

    Never caught and papered over with fabricated or silent audio —
    callers must treat this as "no audio available."
    """


class SpeechSynthesisProvider(ABC):
    """A source of text-to-speech synthesis for one piece of text."""

    @abstractmethod
    def synthesize(self, text: str) -> SynthesizedSpeech:
        """Turn `text` into a single validated SynthesizedSpeech result.

        `text` is ORION-authored output text (e.g. a spoken response),
        never externally-sourced — it does not carry the same untrusted-
        data concerns `Transcript.text` does.

        Must raise SpeechSynthesisProviderError (never return placeholder,
        silent, or fabricated audio) if the provider cannot fulfil the
        request for any reason — transport failure, or output that does
        not satisfy the SynthesizedSpeech schema.
        """
        raise NotImplementedError
