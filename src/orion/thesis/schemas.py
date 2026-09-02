"""The market thesis, and the vocabulary it is written in.

WHY THIS PACKAGE EXISTS, AND WHY IT IS NOT INSIDE `orion.analysts`. Mission 8
v2.1 refused to persist a `MarketThesis` and recorded two blockers. This module
resolves the second one. A thesis has to be reachable by three packages that
must never see an analyst: `orion.paper` journals it, `orion.memory` stores it,
and `orion.reflection` reads it back. `tests/analysts/test_import_hygiene.py`
forbids all three from importing `orion.analysts`, and that ban is the single
mechanism enforcing "specialist output reaches no decision path" — moving it so
a record could be stored would trade a real guarantee for a convenience.

So the *schema* moved instead of the wall. A `MarketThesis` is a shared domain
record, in the same sense `Evidence` and `RiskVerdict` are: several packages
produce, carry, store and read it. `orion.analysts.synthesis` is the only thing
that *builds* one, exactly as `orion.risk.guardian` is the only thing that
builds a `RiskVerdict` while `orion.risk.schemas` holds its shape.

THE DEPENDENCY DIRECTION IS UPSTREAM-ONLY AND ACYCLIC. This module imports
`orion.analysis.structure` (for `VolatilityLevel`), `orion.data.models` (for
`Timeframe`) and `orion.provenance` (the one shared hashing mechanism, which is
dependency-free by design) — no analyst, no engine, no store, no provider, no
orchestrator. Everything above it may point down at it; it points
at nothing that could point back. `tests/thesis/test_import_hygiene.py` enforces
that, and `tests/analysts/test_import_hygiene.py` now lists `thesis` among the
packages that may not import `orion.analysts`, which is what keeps the
transitive path closed: `orion.paper` -> `orion.thesis` must not become a way to
reach a specialist.

THE VOCABULARY LIVES HERE TOO, FOR THE SAME REASON. `StructureBias`,
`TechnicalAvailability`, `MacroPolicyPressure` and `MarketRelevance` are fields
of a `MarketThesis`, so a package that can read a thesis has to be able to name
their types. They are re-exported from their original modules, so every existing
import path still works and the JSON schema the Macro Analyst sends to Claude is
byte-identical — only the definition site moved.

NOTHING HERE CAN AUTHORISE ANYTHING. There is no action, direction-to-trade,
quantity, entry, stop, target, risk parameter or approval field in this file,
and a test checks every field name in every model against that vocabulary. A
thesis is evidence and reasoning; the Risk Guardian is authority, it reviews a
`ProposedDecision` and never a thesis, and `orion.risk` cannot import this
module or `orion.analysts` at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.analysis.structure import VolatilityLevel
from orion.data.models import Timeframe
from orion.provenance import canonical_hash

#: Bumped when the comparison rules or the canonical sentences below change, so
#: a *stored* thesis can be traced to the wording and rules that produced it.
#: That traceability is what makes persisting one honest rather than a snapshot
#: of forgotten reasoning.
THESIS_VERSION = "2026-08-31.1"

#: Matches `orion.interface.schemas.MAX_ID_CHARS`. An `analysis_id` is minted by
#: `orion.orchestrator.analysis.build_analysis_id` from a symbol, a timeframe,
#: a bar timestamp and the instant of the analysis; the bound exists so a value
#: that could not have come from there is not constructible here either. The
#: longest id that function can produce — a symbol, a short timeframe code and
#: two microsecond-resolution ISO timestamps — is comfortably inside it.
MAX_ANALYSIS_ID_CHARS = 128

_FINGERPRINT_FIELD = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class StructureBias(StrEnum):
    """What one reading says about the shape of price — never about what to do.

    `BULLISH`/`BEARISH` describe observed geometry: price above its averages, a
    close beyond a swing high, an unfilled gap left behind by an upward move.
    They are not recommendations, they carry no size, level or timing, and no
    package that can read them is able to act on one.

    `NEUTRAL` means a concept was evaluated and found nothing leaning either
    way — a real result. `UNCLEAR` means the values were available but do not
    resolve to a side, which is different again from the concept not having
    been evaluated at all.
    """

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNCLEAR = "unclear"


class TechnicalAvailability(StrEnum):
    """Why a technical pass does or does not carry findings.

    Shaped like `MacroAvailability` and `RiskAvailability`, for the same reason:
    "nothing found" and "could not look" must stay distinguishable, and neither
    is ever answered by inventing a reading. There is deliberately no
    `not_connected` member — the Technical Analyst is deterministic computation
    over bars the analysis already fetched, so it cannot be missing a
    credential.
    """

    AVAILABLE = "available"
    #: No snapshot was supplied at all.
    NO_INPUT = "no_input"
    #: Snapshots were supplied but no timeframe held enough bars to evaluate a
    #: single concept.
    INSUFFICIENT_DATA = "insufficient_data"


class MacroPolicyPressure(StrEnum):
    """What the aggregate of releases implies about monetary-policy-relevant
    conditions — a characterization of the DATA's implied pressure on policy,
    never a trade direction or recommendation.

    `TIGHTENING_PRESSURE` means the data collectively reads firmer/hotter than
    expected (conditions that would tend to support tighter monetary policy).
    `EASING_PRESSURE` means the reverse. Deliberately not phrased as
    "bullish"/"bearish", "hawkish"/"dovish", or any asset-direction or
    trading-signal vocabulary — this describes the data, not an instruction
    about what to do with it.
    """

    TIGHTENING_PRESSURE = "tightening_pressure"
    EASING_PRESSURE = "easing_pressure"
    MIXED = "mixed"
    IN_LINE = "in_line"
    INSUFFICIENT_DATA = "insufficient_data"


class MarketRelevance(StrEnum):
    """How much a supplied batch, taken together, matters — independent of
    which direction any surprise leans."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class ConditionLevel(StrEnum):
    """How eventful a specialist reports its own domain to be.

    Deliberately not a direction and deliberately three-valued. `UNDETERMINED`
    is the honest middle: a MEDIUM volatility regime with no structural events,
    or a MEDIUM macro relevance, is neither clearly active nor clearly quiet,
    and forcing it into one of the other two would manufacture a reading.
    """

    ACTIVE = "active"
    QUIET = "quiet"
    UNDETERMINED = "undetermined"


class ThesisAlignment(StrEnum):
    """What the specialists said about each other's domain being eventful."""

    #: Both report their domain as active.
    CORROBORATED_ACTIVE = "corroborated_active"
    #: Both report their domain as quiet.
    CORROBORATED_QUIET = "corroborated_quiet"
    #: One reports active and the other quiet. Reported, never reconciled.
    CONFLICTED = "conflicted"
    #: No Macro Analyst output was supplied, so there is nothing to compare.
    MACRO_ABSENT = "macro_absent"
    #: At least one side is UNDETERMINED, so no comparison is claimed.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ThesisTopic(StrEnum):
    """What one agreement or conflict point is about."""

    #: Technical activity against macro significance.
    MARKET_CONDITIONS = "market_conditions"
    #: Whether the technical timeframes lean the same way as each other.
    TIMEFRAME_STRUCTURE = "timeframe_structure"


#: Every sentence a thesis can carry, keyed by what it is describing. Looked up,
#: never composed — the same discipline `orion.analysts.knowledge` applies to
#: findings. It lives beside `MarketThesis` rather than beside `synthesize`
#: because `MarketThesis`'s own validator checks against it: a stored thesis
#: cannot carry prose no one committed, whoever constructed it.
ALIGNMENT_SUMMARY: Mapping[ThesisAlignment, str] = MappingProxyType(
    {
        ThesisAlignment.CORROBORATED_ACTIVE: (
            "Both specialists report an eventful picture: price is moving or breaking "
            "structure, and the economic calendar holds releases the Macro Analyst rates as "
            "significant. This says conditions are busy, not which way they resolve."
        ),
        ThesisAlignment.CORROBORATED_QUIET: (
            "Both specialists report a quiet picture: price is in a low-volatility regime "
            "with no structural events, and the calendar holds nothing the Macro Analyst "
            "rates as significant."
        ),
        ThesisAlignment.CONFLICTED: (
            "The specialists disagree about how eventful conditions are. Both readings are "
            "reported below; ORION does not resolve the disagreement, because neither "
            "specialist can see the other's domain."
        ),
        ThesisAlignment.MACRO_ABSENT: (
            "Only the Technical Analyst contributed to this thesis. No macro assessment was "
            "supplied, so nothing is claimed about the economic calendar."
        ),
        ThesisAlignment.INSUFFICIENT_EVIDENCE: (
            "At least one specialist could not characterise its domain clearly enough to "
            "compare. The individual readings are reported; the comparison is not."
        ),
    }
)

#: The canonical sentence for each kind of agreement or conflict.
POINT_DETAIL: Mapping[tuple[ThesisTopic, bool], str] = MappingProxyType(
    {
        (ThesisTopic.MARKET_CONDITIONS, True): (
            "Technical activity and macro significance point the same way about how "
            "eventful conditions are."
        ),
        (ThesisTopic.MARKET_CONDITIONS, False): (
            "Technical activity and macro significance disagree about how eventful "
            "conditions are. Both readings stand."
        ),
        (ThesisTopic.TIMEFRAME_STRUCTURE, True): (
            "Every timeframe that resolved to a direction resolved to the same one."
        ),
        (ThesisTopic.TIMEFRAME_STRUCTURE, False): (
            "The timeframes that resolved to a direction did not resolve to the same one."
        ),
    }
)


class ThesisPoint(BaseModel):
    """One thing the specialists agreed or disagreed about.

    Carries the structured readings from both sides rather than a paraphrase,
    so a conflict names exactly what each specialist said. `detail` is looked
    up from `POINT_DETAIL`; nothing here is written at runtime.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    topic: ThesisTopic
    agreed: bool
    technical_reading: str = Field(
        min_length=1,
        max_length=64,
        description="The technical side's own enum value, copied. Never prose.",
    )
    macro_reading: str | None = Field(
        default=None,
        max_length=64,
        description="The macro side's own enum value, copied. None when macro is absent or "
        "the topic does not involve it.",
    )
    detail: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def _check_the_detail_is_the_canonical_one(self) -> ThesisPoint:
        """A point cannot carry prose the committed table did not author.

        Checked on the model rather than only in `synthesize`, for the same
        reason `TechnicalFinding` checks its interpretation: a stored record is
        read long after the function that built it, and the guarantee has to
        travel with the record.
        """
        expected = POINT_DETAIL.get((self.topic, self.agreed))
        if expected is None or self.detail != expected:
            raise ValueError(
                f"detail for {self.topic.value!r}/agreed={self.agreed} is not the canonical "
                "sentence; thesis wording is looked up, never written"
            )
        return self


class ThesisTimeframe(BaseModel):
    """One technical timeframe's reading, with the provenance to re-fetch it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: Timeframe
    bias: StructureBias
    volatility: VolatilityLevel
    finding_count: int = Field(ge=0)
    unevaluated_count: int = Field(ge=0)
    evidence_fingerprint: str = _FINGERPRINT_FIELD
    source_provider: str = Field(min_length=1, max_length=64)
    retrieved_at: datetime
    as_of: datetime
    bar_count: int = Field(ge=0)


class MacroProvenance(BaseModel):
    """Everything needed to re-fetch and re-verify the macro half.

    Copied field for field off `MacroAnalystOutput`. `rationale` travels with
    it because a conflict in which the Macro Analyst's own words were dropped
    is a conflict a reader cannot evaluate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    input_fingerprint: str = _FINGERPRINT_FIELD
    source_provider: str = Field(min_length=1)
    query_start: datetime
    query_end: datetime
    retrieved_at: datetime
    analyzed_at: datetime
    event_count: int = Field(ge=0)
    rationale: str = Field(min_length=1, max_length=500)


class MarketThesis(BaseModel):
    """What several specialists, taken together, say about one market.

    `analysis_id` IS A SIBLING KEY, NOT A BACK-REFERENCE, and that distinction
    is the whole of Mission 8 v2.2's first blocker. The orchestrator mints one
    id per analysis — from the symbol, the timeframe, the last bar's timestamp
    and the instant the analysis was made — *before* any decision exists, and
    stamps it on `ProposedDecision` and `RiskVerdict` alike. A thesis carrying
    the same id is therefore findable from a journaled verdict without ever
    pointing *at* the proposal, and identifies *which* chain it was formed
    beside rather than merely which bar — which is what lets the thesis stay
    strictly advisory while becoming part of the auditable chain. A thesis that
    instead carried a `decision_fingerprint` would couple advisory evidence to
    the authority that reviewed it, in the one direction that must stay closed.

    THE EVIDENCE IDENTITY IS A DIFFERENT FIELD, AND STAYS ONE.
    `ThesisTimeframe.evidence_fingerprint` says which bytes the technical half
    read, and equals `ProposedDecision.evidence_ref.content_hash` for the same
    analysis. Two analyses of one bar share that fingerprint and no longer
    share an `analysis_id`: the first is a statement about the market, the
    second about the run.

    ADVISORY. Nothing here authorises, sizes, times or places anything. There is
    no action, no entry, no stop, no target, no quantity and no approval — the
    Risk Guardian is the only thing in ORION that approves a trade, it reviews a
    `ProposedDecision` and never a thesis, and `orion.risk` cannot import this
    module.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(min_length=1, max_length=MAX_ANALYSIS_ID_CHARS)
    symbol: str = Field(min_length=1)
    formed_at: datetime
    thesis_version: str = Field(min_length=1, max_length=32)

    alignment: ThesisAlignment
    summary: str = Field(
        min_length=1,
        max_length=400,
        description="The canonical sentence for this alignment, looked up verbatim.",
    )

    # --- the technical half, preserved ---
    technical_availability: TechnicalAvailability
    technical_bias: StructureBias
    technical_condition: ConditionLevel
    technical_timeframe_agreement: bool
    technical_analyst_version: str = Field(min_length=1, max_length=32)
    technical_knowledge_version: str = Field(min_length=1, max_length=32)
    timeframes: tuple[ThesisTimeframe, ...]

    # --- the macro half, preserved, or explicitly absent ---
    macro_policy_pressure: MacroPolicyPressure | None = None
    macro_relevance: MarketRelevance | None = None
    macro_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    macro_condition: ConditionLevel | None = None
    macro_provenance: MacroProvenance | None = None

    agreements: tuple[ThesisPoint, ...]
    conflicts: tuple[ThesisPoint, ...]

    @model_validator(mode="after")
    def _check_thesis_invariants(self) -> MarketThesis:
        if self.formed_at.tzinfo is None:
            raise ValueError("formed_at must be a timezone-aware datetime")
        if self.summary != ALIGNMENT_SUMMARY[self.alignment]:
            raise ValueError(
                "summary is not the canonical sentence for this alignment; summaries are "
                "looked up, never written"
            )
        macro_fields = (
            self.macro_policy_pressure,
            self.macro_relevance,
            self.macro_confidence,
            self.macro_condition,
            self.macro_provenance,
        )
        present = [field is not None for field in macro_fields]
        if any(present) and not all(present):
            raise ValueError(
                "the macro half is all-or-nothing: a thesis either carries every macro "
                "field or none of them, so a partial read cannot look like a complete one"
            )
        if self.alignment is ThesisAlignment.MACRO_ABSENT and any(present):
            raise ValueError("alignment is MACRO_ABSENT but macro readings are present")
        if self.alignment is not ThesisAlignment.MACRO_ABSENT and not all(present):
            raise ValueError(
                f"alignment is {self.alignment.value!r} but the macro half is missing; only "
                "MACRO_ABSENT may omit it"
            )
        for point in self.agreements:
            if not point.agreed:
                raise ValueError(f"a point in `agreements` is marked disagreed: {point.topic}")
        for point in self.conflicts:
            if point.agreed:
                raise ValueError(f"a point in `conflicts` is marked agreed: {point.topic}")
        return self


def market_thesis_fingerprint(thesis: MarketThesis) -> str:
    """A deterministic fingerprint of `thesis`'s full content.

    Thin, domain-named wrapper around `orion.provenance.canonical_hash`, exactly
    like `evidence_fingerprint`/`decision_fingerprint`/`risk_verdict_fingerprint`
    — the single source of truth for "which thesis is this" across package
    boundaries, so the journal, the writer and any later reader all agree
    without any of them owning a second hashing mechanism. `orion.provenance`
    is dependency-free by design and states in its own docstring that every
    layer may import it, so naming it here adds no layering weight.
    """
    return canonical_hash(thesis)
