"""Strict shapes for Mission 9 (Voice) Phase 1 — the stable contract future
speech-to-text/text-to-speech providers and the eventual voice runtime build
upon. See `docs/DECISIONS.md` for the full Phase 1 architecture and why the
runtime/orchestration layer (audio I/O, the UNDERSTANDING/ANALYZING states,
streaming, interruption) is explicitly out of scope here.

`Transcript` is provider-produced speech-to-text output. It is treated as
UNTRUSTED DATA from the moment it exists: nothing here interprets `text` as
an instruction, a command, or anything executable — it is one bounded string
field, exactly like `ReasoningVerdict.rationale`/`MacroAnalystVerdict.
rationale`'s existing "display/audit text only" discipline, extended to a
package with no decision logic of any kind. A future UNDERSTANDING/LLM layer
that reads `Transcript.text` will need its own explicit prompt-injection
defense (fencing, closed output schema, no tools) — schema validation alone
here does not, and cannot, make that safe; see `orion.analysts.claude_macro_
provider` for the established pattern to follow when that layer is built.

No session/utterance identifier field exists. A `Transcript` is content-
addressed via `transcript_fingerprint` (a thin wrapper around `orion.
provenance.canonical_hash`, mirroring `decision_fingerprint`/`risk_verdict_
fingerprint`/`portfolio_state_fingerprint` exactly) rather than a separately
minted id — the same "content fingerprint as identity" precedent Mission 2's
entire journal design already established, and it avoids inventing a
uuid4()-minted identifier with no minting authority yet (no orchestrator
exists to be one — see docs/DECISIONS.md's repeated Mission 2-8 precedent).

`SynthesizedSpeech` is the smallest vendor-neutral shape for text-to-speech
output: raw audio bytes plus the provider-reported encoding and provenance.
It does not assume a specific vendor's transport (streaming, URL-based,
buffered) — a concrete provider adapts whatever its own vendor API returns
into this shape, the same way `TwelveDataProvider.fetch_ohlcv` adapts a raw
JSON response into `MarketSnapshot` without either schema knowing about the
other's original shape.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orion.provenance import canonical_hash


class VoiceState(StrEnum):
    """The eventual voice interaction state machine (brief §15):

        IDLE -> LISTENING -> UNDERSTANDING -> ANALYZING -> SPEAKING -> IDLE

    A closed vocabulary only — no transition-validation logic exists here or
    anywhere in this package. Driving actual transitions is orchestration
    work, explicitly out of scope for Phase 1 (see docs/DECISIONS.md).
    `IDLE` is both the starting and the returned-to state; nothing further
    is needed to represent that — it is simply a value this enum already
    has.
    """

    IDLE = "idle"
    LISTENING = "listening"
    UNDERSTANDING = "understanding"
    ANALYZING = "analyzing"
    SPEAKING = "speaking"


class Transcript(BaseModel):
    """One provider-produced speech-to-text result. Frozen and closed
    (`extra="forbid"`), matching every schema in this codebase: an
    unexpected field is a construction bug, not something to ignore.

    `text` is untrusted external content — see the module docstring. It is
    the only free-text field; every other field is provider-authored
    provenance. There is deliberately no `command`/`intent` field:
    assigning meaning to `text` is the eventual UNDERSTANDING layer's job,
    not this schema's.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=2000,
        description="The transcribed utterance. Untrusted external content — never "
        "interpreted as an instruction by anything in this package. A provider that "
        "detects no speech at all has nothing to transcribe and must raise "
        "TranscriptionProviderError rather than return an empty or placeholder "
        "Transcript. 2000 chars is a conventional starting point (a generously long "
        "spoken utterance), not vendor-calibrated — revisit once a real provider's "
        "typical output length is known.",
    )
    provider: str = Field(min_length=1, description="The STT vendor, e.g. 'deepgram'.")
    model: str | None = Field(
        default=None,
        min_length=1,
        description="The STT model/engine identifier, if the provider reports one. "
        "Provider-observed, never guessed.",
    )
    transcribed_at: datetime = Field(
        description="When the provider produced this transcript. Timezone-aware, "
        "provider/ORION-boundary-authored — never computed inside this schema."
    )
    duration_seconds: float | None = Field(
        default=None, gt=0, description="Audio duration, if the provider reports it."
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Provider-reported transcription confidence, if available. Advisory only.",
    )

    @field_validator("text")
    @classmethod
    def _require_printable_text(cls, value: str) -> str:
        # Real speech-to-text output is natural language; it should never
        # legitimately contain control characters (NUL, raw newlines, etc.).
        # Rejected rather than silently stripped/normalized, mirroring
        # orion.analysts.claude_macro_provider._validate_provider_field's
        # identical "reject, don't normalize" discipline — this is both a
        # data-quality check in its own right and defense-in-depth for
        # whatever future context (e.g. a prompt template) eventually
        # embeds this text, without this schema claiming to solve that
        # future problem on its own (see the module docstring).
        if not value.isprintable():
            raise ValueError(
                "text contains a non-printable/control character; rejected rather "
                "than silently normalized"
            )
        return value

    @field_validator("duration_seconds")
    @classmethod
    def _require_finite_duration(cls, value: float | None) -> float | None:
        # gt=0 alone does not exclude float("inf") -- inf > 0 is True in
        # Python. NaN is already excluded by gt=0 (NaN comparisons are
        # always False), but infinity needs an explicit check: no real
        # audio clip has infinite duration, and a provider reporting one
        # is a malformed response to fail closed on, not a value to accept.
        if value is not None and not math.isfinite(value):
            raise ValueError(f"duration_seconds must be finite, got {value!r}")
        return value

    @field_validator("transcribed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"transcribed_at {value!r} has no timezone; must be timezone-aware")
        return value


def transcript_fingerprint(transcript: Transcript) -> str:
    """A deterministic fingerprint of `transcript`'s full content.

    Thin, domain-named wrapper around `orion.provenance.canonical_hash` —
    mirrors `orion.decision.schemas.decision_fingerprint`/`orion.risk.
    schemas.risk_verdict_fingerprint`/`orion.risk.portfolio.portfolio_
    state_fingerprint` exactly, including why this is a standalone function
    and never a field on `Transcript` itself: a self-referencing hash field
    would need to exclude itself from its own input, which `canonical_hash`
    does not special-case for any model (see `risk_verdict_fingerprint`'s
    identical reasoning).
    """
    return canonical_hash(transcript)


class SynthesizedSpeech(BaseModel):
    """The smallest vendor-neutral shape describing text-to-speech output.
    Frozen and closed (`extra="forbid"`).

    `audio`/`format` are provider-authored: a concrete provider adapts
    whatever its own vendor API returns into raw bytes plus the encoding it
    used. No format is assumed or guessed here — see the module docstring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    audio: bytes = Field(min_length=1, description="Raw synthesized audio bytes.")
    format: str = Field(
        min_length=1,
        description="Provider-reported audio encoding, e.g. 'mp3', 'wav', 'pcm_s16le'. Not "
        "a closed enum — supported formats vary by vendor and are not guessed here.",
    )
    provider: str = Field(min_length=1, description="The TTS vendor, e.g. 'deepgram'.")
    model: str | None = Field(
        default=None,
        min_length=1,
        description="The TTS voice/model identifier, if the provider reports one.",
    )
    synthesized_at: datetime = Field(
        description="When the provider produced this audio. Timezone-aware, "
        "provider/ORION-boundary-authored — never computed inside this schema."
    )

    @field_validator("synthesized_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"synthesized_at {value!r} has no timezone; must be timezone-aware")
        return value
