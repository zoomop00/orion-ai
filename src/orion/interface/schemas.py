"""Wire shapes for the Mission 10 Phase 2 HTTP boundary — the only things a
client is ever allowed to send in or read back.

DELIBERATELY THIN. `TextTurnRequest.text` carries no length limit, no
character rules and no format validation, because `orion.voice.schemas.
Transcript.text` already owns every one of those rules (1–2000 chars,
`isprintable()`, rejected rather than normalized) and this layer must not
grow a second, silently-drifting copy of them. `orion.interface.service`
feeds the raw string straight into `Transcript` and lets Mission 9's
validators decide — the same "reuse the existing contract, do not restate
it" discipline `orion.voice.audio_output` follows by taking a whole
`SynthesizedSpeech` rather than re-deriving its fields. The only limit this
module does declare is `MAX_REQUEST_BODY_BYTES`, and that is not a business
rule at all: it is a transport-layer ceiling, enforced by
`orion.interface.app` before a single byte reaches the voice runtime.

RESPONSE SURFACE. `VoiceTurnResponse` is an allowlist, not a projection of
whatever the runtime happened to produce. `orion.voice.runtime.
VoiceTurnResult` carries a full `Transcript` (provider name, model
identifier, vendor-reported confidence and duration) and a full
`SynthesizedSpeech` (vendor, voice model, synthesis timestamp). None of
that vendor detail crosses this boundary. A browser needs the words, the
audio, and enough metadata to play it — nothing about which vendor ORION
happens to use this month, which is both an information-disclosure
reduction and the thing that keeps the client from accidentally
depending on a provider we intend to be swappable.

AUDIO TRANSPORT. `audio_base64` is standard base64 of the *unmodified*
WAV bytes Deepgram returned (linear16, 24 kHz, RIFF/WAVE container). The
audio is never transcoded, re-encoded, resampled or rewrapped — only
transport-encoded, and `audio_bytes` reports the true decoded length so a
client can verify the round trip. See `docs/DECISIONS.md` for why base64 in
one JSON response beat the alternatives (a second "fetch the audio"
endpoint would require storing audio between requests, which this phase
forbids outright; metadata-in-headers hits the ~8 KB header ceiling once a
2000-character multilingual transcript is percent-encoded).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.voice.audio_input import MAX_AUDIO_INPUT_BYTES

# The transport ceiling for any request body. Deliberately *is*
# `orion.voice.audio_input.MAX_AUDIO_INPUT_BYTES` rather than a second
# hand-picked number: the HTTP boundary must never be the looser of the two
# limits, and the cheapest way to guarantee that forever is to have exactly
# one number. A deployment that wants a tighter ceiling should lower this
# one; it can never accidentally drift above what Mission 9 accepts.
MAX_REQUEST_BODY_BYTES = MAX_AUDIO_INPUT_BYTES

# Content types accepted on the audio turn endpoint. Raw bytes with an
# explicit type, never multipart — no `python-multipart` dependency, no
# parser between the socket and the size check.
ACCEPTED_AUDIO_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/flac",
        "audio/l16",
    }
)


# Independent ceilings for every string and list that crosses this boundary
# as part of an analysis. Deliberately restated rather than imported from the
# engines that produce them: this layer must never be the looser of the two,
# and a value that could not have come from an engine must not be
# serialisable here at all. `MAX_RATIONALE_CHARS` matches
# `orion.reasoning.schemas.ReasoningVerdict.rationale`'s own 400-character
# cap; the rest are generous bounds on closed enum vocabularies.
MAX_RATIONALE_CHARS = 400
MAX_DETAIL_CHARS = 200
MAX_CODE_CHARS = 64
MAX_SYMBOL_CHARS = 16
MAX_ID_CHARS = 128
MAX_CODE_LIST = 32


class TextTurnRequest(BaseModel):
    """The text-fallback request body: one field, no local rules.

    This path exists so Phase 3 can drive the entire interface — real
    response text, real Deepgram speech, real audio playback — on a machine
    with no microphone, or in a browser that has not been granted
    microphone permission. It is a genuine product affordance (the brief
    lists "text fallback" as a Mission 9 requirement), not a test hook.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(
        description="What the user said, as text. Validated by "
        "orion.voice.schemas.Transcript, not here — see the module docstring."
    )


# ---------------------------------------------------------------- analysis --
#
# Phase 5.2's addition, and the same allowlist discipline `VoiceTurnResponse`
# already applies to a `VoiceTurnResult`.
#
# `orion.orchestrator.schemas.AnalysisReport` embeds whole domain objects:
# `ReasoningOutput` carries the model identifier, the prompt version and a
# 64-character evidence fingerprint; `ProposedDecision` carries an engine
# version and two provenance refs; `RiskVerdict` carries a guardian version
# and a policy content hash. None of that belongs in a browser. A client
# needs what ORION found, not which model ORION happens to use this month —
# which is both an information-disclosure reduction and the thing that keeps
# the client from depending on internals we intend to be swappable.
#
# So these models are a flat, closed, independently-bounded projection built
# by `orion.interface.service`, not a re-export. Nothing here imports
# `orion.orchestrator`: the enums arrive as plain strings with their own
# length caps, so a renamed member upstream is caught by a test rather than
# silently reshaping the wire contract. Every numeric field states its real
# range, so a value that could not have come from an engine cannot be
# serialised at all.


class CapabilitySummary(BaseModel):
    """A capability the brief asks for that this build does not have.

    `status` is a `Literal`, not a string: `"not_connected"` is the only
    value this type can hold, so no code path — present or future — can
    report news or macro as connected without changing this declaration.
    There is deliberately no list, mapping, number or timestamp field, so
    there is nowhere for a fabricated headline to go.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["not_connected"] = "not_connected"
    detail: str = Field(min_length=1, max_length=MAX_DETAIL_CHARS)


class StructureSummary(BaseModel):
    """Market structure, as `orion.analysis.structure` computed it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trend: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    volatility: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    # `None` means "no level found", never zero and never a guess.
    nearest_support: float | None = Field(default=None, gt=0)
    nearest_resistance: float | None = Field(default=None, gt=0)
    swing_high_count: int = Field(ge=0)
    swing_low_count: int = Field(ge=0)


class MomentumSummary(BaseModel):
    """Indicator readings. Every value is optional for one reason only:
    `orion.analysis.indicators.IndicatorSnapshot` uses `None` for "not enough
    data", and that absence is forwarded rather than filled in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rsi: float | None = Field(default=None, ge=0, le=100)
    ema_fast: float | None = Field(default=None, gt=0)
    ema_slow: float | None = Field(default=None, gt=0)
    sma_slow: float | None = Field(default=None, gt=0)
    atr: float | None = Field(default=None, ge=0)
    rsi_length: int = Field(gt=0)
    ema_fast_length: int = Field(gt=0)
    ema_slow_length: int = Field(gt=0)


class RiskSummary(BaseModel):
    """The Risk Guardian's answer, or an explicit reason there isn't one.

    `outcome` is present if and only if a real verdict was produced. The
    validator makes "available but no outcome" and "unavailable with an
    outcome" both unconstructible, so a client can branch on `availability`
    without also defending against a contradictory payload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    availability: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    outcome: str | None = Field(default=None, min_length=1, max_length=MAX_CODE_CHARS)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=MAX_CODE_LIST)
    failed_controls: tuple[str, ...] = Field(default=(), max_length=MAX_CODE_LIST)
    portfolio_as_of: datetime | None = None

    @model_validator(mode="after")
    def _check_risk_summary_invariants(self) -> RiskSummary:
        available = self.availability == "available"
        if available and self.outcome is None:
            raise ValueError("availability is 'available' but no outcome is present")
        if not available and self.outcome is not None:
            raise ValueError(
                f"availability is {self.availability!r} but an outcome is present; "
                "an outcome may only accompany an available risk review"
            )
        for code in (*self.reason_codes, *self.failed_controls):
            if not code or len(code) > MAX_CODE_CHARS:
                raise ValueError(f"reason/control code {code!r} is empty or too long")
        return self


class ConclusionSummary(BaseModel):
    """What ORION concluded, and the model's own sentence about why.

    `rationale` is the one model-authored string that crosses this boundary.
    It is bounded here independently of `orion.reasoning.schemas.
    ReasoningVerdict`'s own 400-character cap — this layer must never be the
    looser of the two — and it is data for a client to render as text, never
    markup, a command or an instruction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    direction: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARS)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=MAX_CODE_LIST)


class AnalysisSummary(BaseModel):
    """One completed analysis, reduced to what a client actually needs.

    Maps onto the seven fields `ORION_MASTER_ENGINEERING_BRIEF_V2.md` §16
    asks the interface to transform around: market structure, momentum, news,
    macro, risk, confidence, conclusion.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    symbol: str = Field(min_length=1, max_length=MAX_SYMBOL_CHARS)
    timeframe: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    as_of: datetime = Field(description="Timestamp of the most recent bar analysed.")
    analyzed_at: datetime
    last_price: float = Field(gt=0)
    bar_count: int = Field(ge=1)

    structure: StructureSummary
    momentum: MomentumSummary
    news: CapabilitySummary
    macro: CapabilitySummary
    risk: RiskSummary
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    conclusion: ConclusionSummary


class VoiceTurnResponse(BaseModel):
    """One completed voice turn, reduced to what a browser actually needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transcript: str = Field(
        description="What ORION understood the user to have said. For the text-fallback "
        "path this is the client's own text, echoed back only after it has passed "
        "Transcript validation — never before."
    )
    response_text: str = Field(description="What ORION said back, as text.")
    audio_base64: str = Field(
        description="Base64 of the untranscoded synthesized speech. Decode and play."
    )
    audio_format: str = Field(description="Container/encoding of the decoded audio, e.g. 'wav'.")
    audio_media_type: str = Field(
        description="MIME type for the decoded bytes, ready for a Blob/Audio element."
    )
    audio_bytes: int = Field(
        gt=0, description="Length of the decoded audio in bytes, for client-side verification."
    )
    state: str = Field(
        description="The voice runtime's state after the turn, read from Mission 9's "
        "VoiceStateMachine — the one authoritative state machine. This interface owns "
        "no state of its own and never invents a value for this field."
    )
    analysis: AnalysisSummary | None = Field(
        default=None,
        description="Present only when the turn was an analysis request that completed. "
        "Absent (null) for every ordinary conversational turn, which is what keeps this "
        "field backwards compatible with every Phase 3/4 client: they ignore unknown "
        "fields and never see one. Never partially populated — a failed analysis is a "
        "failed turn, not a response with empty panels.",
    )


class HealthResponse(BaseModel):
    """Liveness plus one honest capability bit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = Field(description="Always 'ok' if the process is serving requests.")
    voice_available: bool = Field(
        description="Whether a Deepgram credential is configured, so a client can tell "
        "the audio path apart from a misconfiguration before trying it. A boolean only "
        "— never the key, never its length, never a prefix."
    )
    text_fallback_available: bool = Field(
        description="Whether the text-fallback path can run. Requires only text-to-speech, "
        "so it shares the same credential as the audio path today."
    )


class ErrorResponse(BaseModel):
    """The only error shape this service ever returns.

    Both fields are fixed strings chosen at development time. Nothing here
    is ever interpolated from an exception, a provider message, a file path,
    a header or a request body — see `orion.interface.service` for the
    complete, closed mapping and why it is deliberately coarse.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str = Field(description="A stable machine-readable code, safe to branch on.")
    detail: str = Field(description="A calm, generic, human-readable sentence.")
