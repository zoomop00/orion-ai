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
# A technical finding's computed observation and its looked-up interpretation.
# Both match the caps `orion.analysts.technical_schemas.TechnicalFinding` already
# declares, for the same reason every other ceiling here is restated: this
# boundary must never be the looser of the two.
MAX_OBSERVATION_CHARS = 240
MAX_INTERPRETATION_CHARS = 400
# One finding per supported concept per timeframe, with headroom. The registry
# holds eleven concepts; a payload larger than this could not have come from the
# analyst and must not be serialisable here.
MAX_FINDINGS = 64


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


class MacroSummary(BaseModel):
    """Macro context, or an explicit reason there is none.

    A SEPARATE TYPE FROM `CapabilitySummary`, ON PURPOSE. News is still
    structurally incapable of claiming connectivity -- its `status` remains a
    one-member `Literal` with nowhere to put a headline -- and widening that
    type to let macro report a reading would have taken the guarantee away
    from news as collateral. Two types keep both statements exact.

    WHAT CROSSES, AND WHAT DOES NOT. Four values and a count: the analyst's
    policy-pressure reading, how much it thinks the batch matters, its
    confidence, and its plain-language rationale. The per-event assessments,
    their 64-character ids, the input fingerprint, the prompt version and the
    model identifier all exist on `MacroAnalystOutput` and all stop here --
    the same allowlist discipline `_to_analysis_summary` already applies to
    reasoning provenance.

    NOTHING IS EVER PARTIALLY POPULATED. The five payload fields are present
    together when `status` is `"available"` and absent together otherwise, so
    a client can branch on one field and trust the rest.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["available", "not_connected", "macro_unavailable", "no_events"]
    detail: str = Field(min_length=1, max_length=MAX_DETAIL_CHARS)
    policy_pressure: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    market_relevance: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_CHARS)
    event_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_macro_summary_invariants(self) -> MacroSummary:
        payload = (
            self.policy_pressure,
            self.market_relevance,
            self.confidence,
            self.rationale,
            self.event_count,
        )
        available = self.status == "available"
        if available and any(value is None for value in payload):
            raise ValueError("status is 'available' but the macro read is incomplete")
        if not available and any(value is not None for value in payload):
            raise ValueError(f"status is {self.status!r} but a macro read is present")
        return self


class TechnicalFindingSummary(BaseModel):
    """One technical concept, evaluated on one timeframe.

    THE TWO TEXT FIELDS ARE DIFFERENT KINDS OF THING, AND STAY SO ON THE WIRE.
    `observation` is formatted from real values by a fixed template in
    `orion.analysts.technical`; `interpretation` is copied verbatim from the
    closed knowledge registry. Neither is ever authored at runtime, and
    `deterministic` says out loud that this finding came from computation over
    bars rather than from a model — the field exists so a future finding that
    did not could not enter the same list silently.

    `concept` and `bias` are the analyst's own closed-enum values as strings;
    this layer re-checks their bounds but does not restate the vocabularies,
    matching how `RiskSummary` carries reason codes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    timeframe: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    bias: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    observation: str = Field(min_length=1, max_length=MAX_OBSERVATION_CHARS)
    interpretation: str = Field(min_length=1, max_length=MAX_INTERPRETATION_CHARS)
    deterministic: bool


class UnevaluatedConceptSummary(BaseModel):
    """A concept ORION could not evaluate, and the reason.

    CARRIED, NOT DROPPED. A client that only received findings could not tell
    "ORION looked and found nothing" from "ORION could not look" — the exact
    distinction `MacroSummary`'s four-state `status` exists to preserve one
    panel over. Listing the reason per concept keeps it at the concept level
    too.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    reason: str = Field(min_length=1, max_length=MAX_CODE_CHARS)


class TechnicalTimeframeSummary(BaseModel):
    """One timeframe's technical reading, with the provenance that crosses.

    WHAT CROSSES, AND WHAT DOES NOT. The timeframe, when the last bar closed,
    how many bars were read, the timeframe's own lean and its volatility
    regime. The 64-character evidence fingerprint and the market-data
    provider's name both exist on `TechnicalTimeframeReport` and both stop at
    `orion.interface.service` — the same allowlist `_to_analysis_summary`
    already applies to the evidence fingerprint and `_to_macro_summary` to the
    model identifier. A browser learns what ORION read and when, never from
    exactly which bytes or which vendor. The full provenance stays on
    `AnalysisReport` and is what `orion.cli`'s terminal rendering prints.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    as_of: datetime
    bar_count: int = Field(ge=0)
    bias: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    volatility: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    findings: tuple[TechnicalFindingSummary, ...] = Field(default=(), max_length=MAX_FINDINGS)
    unevaluated: tuple[UnevaluatedConceptSummary, ...] = Field(default=(), max_length=MAX_FINDINGS)


class TechnicalSummary(BaseModel):
    """The Technical Analyst's findings, or an explicit reason there are none.

    NO `not_connected` STATE, DELIBERATELY. `MacroSummary` has one because
    macro needs a vendor and a credential. The Technical Analyst is
    deterministic computation over bars the analysis already fetched, so it
    runs in every deployment: the only reasons it can hold no findings are
    properties of the data, and both are named here.

    `analyst_version`/`knowledge_version` DO cross, unlike macro's
    `prompt_version`. That is not an inconsistency: a prompt version is only
    meaningful beside the model identifier this boundary hides, whereas these
    two identify ORION's own committed detectors and wording — they are what
    lets a reader confirm an interpretation was looked up rather than
    generated, and they name nothing external.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["available", "no_input", "insufficient_data"]
    detail: str = Field(min_length=1, max_length=MAX_DETAIL_CHARS)
    bias: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    timeframe_agreement: bool
    analyst_version: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    knowledge_version: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    timeframes: tuple[TechnicalTimeframeSummary, ...] = Field(default=(), max_length=MAX_CODE_LIST)

    @model_validator(mode="after")
    def _check_technical_summary_invariants(self) -> TechnicalSummary:
        has_findings = any(timeframe.findings for timeframe in self.timeframes)
        available = self.status == "available"
        if available and not has_findings:
            raise ValueError("status is 'available' but no timeframe carries a finding")
        if not available and has_findings:
            raise ValueError(f"status is {self.status!r} but findings are present")
        if self.status == "no_input" and self.timeframes:
            raise ValueError("status is 'no_input' but timeframe readings are present")
        return self


class ThesisPointSummary(BaseModel):
    """One thing the specialists agreed or disagreed about.

    Both readings travel together. A conflict in which one side's reading was
    dropped is a conflict a reader cannot evaluate, which is the whole reason
    `orion.analysts.synthesis` keeps both — and the reason this layer does not
    reduce a point to a single sentence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    agreed: bool
    technical_reading: str = Field(min_length=1, max_length=MAX_CODE_CHARS)
    macro_reading: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    detail: str = Field(min_length=1, max_length=MAX_DETAIL_CHARS)


class ThesisSummary(BaseModel):
    """What the specialists, taken together, say — or why there is no thesis.

    ADVISORY, AND THE SHAPE SAYS SO. There is no action, direction-to-trade,
    quantity, entry, stop, target or approval field here, and a test checks
    every field name in this module against that vocabulary. The Risk
    Guardian's answer reaches a client through `RiskSummary` and nowhere else.

    `summary` is the canonical sentence `orion.analysts.synthesis` looked up
    for this alignment, copied verbatim — never composed here and never
    composed there.

    NOTHING IS EVER PARTIALLY POPULATED, matching `MacroSummary`: the reading
    fields are present together when `status` is `"available"` and absent
    together otherwise. The macro half within a thesis is separately
    all-or-nothing, because a thesis over technical evidence alone is an
    ordinary, complete answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["available", "unavailable"]
    detail: str = Field(min_length=1, max_length=MAX_DETAIL_CHARS)
    alignment: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    summary: str | None = Field(default=None, max_length=MAX_RATIONALE_CHARS)
    thesis_version: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    technical_bias: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    technical_condition: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    macro_condition: str | None = Field(default=None, max_length=MAX_CODE_CHARS)
    agreements: tuple[ThesisPointSummary, ...] = Field(default=(), max_length=MAX_CODE_LIST)
    conflicts: tuple[ThesisPointSummary, ...] = Field(default=(), max_length=MAX_CODE_LIST)

    @model_validator(mode="after")
    def _check_thesis_summary_invariants(self) -> ThesisSummary:
        payload = (
            self.alignment,
            self.summary,
            self.thesis_version,
            self.technical_bias,
            self.technical_condition,
        )
        available = self.status == "available"
        if available and any(value is None for value in payload):
            raise ValueError("status is 'available' but the thesis is incomplete")
        if not available and any(value is not None for value in payload):
            raise ValueError(f"status is {self.status!r} but a thesis is present")
        if not available and (self.agreements or self.conflicts):
            raise ValueError(f"status is {self.status!r} but thesis points are present")
        return self


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
    macro: MacroSummary
    technical: TechnicalSummary
    thesis: ThesisSummary
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
    spoken: bool = Field(
        description="Whether this turn produced audio. False when the server has no "
        "text-to-speech credential: ORION still answered, in writing, and the four audio "
        "fields below are null rather than empty. Branch on this, never on a length."
    )
    audio_base64: str | None = Field(
        default=None,
        description="Base64 of the untranscoded synthesized speech. Decode and play. "
        "Null when `spoken` is false.",
    )
    audio_format: str | None = Field(
        default=None, description="Container/encoding of the decoded audio, e.g. 'wav'."
    )
    audio_media_type: str | None = Field(
        default=None,
        description="MIME type for the decoded bytes, ready for a Blob/Audio element.",
    )
    audio_bytes: int | None = Field(
        default=None,
        gt=0,
        description="Length of the decoded audio in bytes, for client-side verification.",
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

    @model_validator(mode="after")
    def _check_audio_invariants(self) -> VoiceTurnResponse:
        """The four audio fields arrive together or not at all.

        Same discipline as `RiskSummary` and `MacroSummary`: a client may
        branch on one field and trust the rest, and a half-populated audio
        payload is a rejected response rather than a half-playable one.
        """
        audio = (self.audio_base64, self.audio_format, self.audio_media_type, self.audio_bytes)
        if self.spoken and any(value is None for value in audio):
            raise ValueError("spoken is true but the audio payload is incomplete")
        if not self.spoken and any(value is not None for value in audio):
            raise ValueError("spoken is false but an audio payload is present")
        return self


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
