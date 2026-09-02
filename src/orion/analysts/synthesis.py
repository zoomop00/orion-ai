"""The synthesis seam: several specialists' structured evidence, one thesis.

THE SMALLEST THING THAT WORKS. This is a pure function over the outputs two
specialists already produced. There is no agent framework here, no message bus,
no scheduler, no tool loop and no model call — a specialist is a function that
returns a validated object, and synthesis is a function that reads several of
them. Adding a specialist means adding a parameter and a comparison, not
registering a participant with a runtime.

WHAT IS ACTUALLY COMPARABLE, AND WHAT IS NOT. The tempting move is to fuse the
Technical Analyst's directional bias with the Macro Analyst's policy pressure
into one directional call. ORION does not do that, and the reason is recorded
here rather than left implicit: `MacroPolicyPressure` is deliberately *not*
asset-direction vocabulary (see `orion.analysts.schemas`) — it describes what
economic releases imply about monetary-policy-relevant conditions. Turning
"tightening pressure" into "bearish for gold" requires an asset-specific
economic assumption this build has never established, tested or calibrated.
Asserting it would be exactly the fabricated-inference failure every other
layer here refuses.

SO THE AXIS IS SIGNIFICANCE, NOT DIRECTION — and both specialists genuinely
measure it. Technical measures whether price is doing something: a HIGH
volatility regime, a break of structure, a liquidity sweep. Macro measures
whether the calendar holds anything that matters: `MarketRelevance`. Those two
readings can corroborate each other ("the calendar is heavy and price is
already moving"), and they can conflict in a way a human should see ("the
calendar says a major release is landing and price has done nothing yet" — or
the reverse, "price is breaking structure with nothing on the calendar to
explain it"). That is a real, checkable comparison, and it is the only one
ORION can make honestly today.

NEITHER SIDE IS DISCARDED. Every thesis carries the technical bias, every
timeframe's own reading, the macro pressure, the macro relevance and the Macro
Analyst's own `rationale` verbatim — including, and especially, when the two
conflict. A conflict is reported as a conflict with both readings attached, not
resolved by dropping the inconvenient one or averaging them into mush.

PROVENANCE SURVIVES. `MarketThesis` carries the evidence fingerprint, provider,
retrieval time and bar count for every technical timeframe, and the model,
prompt version, input fingerprint, source provider and window for macro. A
reader can take any fingerprint off a thesis and go fetch the exact record it
came from.

ADVISORY, AND STRUCTURALLY INCAPABLE OF BEING ANYTHING ELSE. There is no
action, direction-to-trade, quantity, entry, stop, target, risk parameter or
approval field anywhere in this file, and a test checks every field name in
every model here against a blocklist. `orion.decision`, `orion.risk` and
`orion.paper` cannot import `orion.analysts` at all, so no thesis can reach the
proposal, the Risk Guardian or the execution path. The Guardian's authority is
untouched: it reviews a `ProposedDecision` against a `RiskPolicy` and a
portfolio, and nothing in this module is any of those three.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.structure import VolatilityLevel
from orion.analysts.knowledge import StructureBias, TechnicalConcept
from orion.analysts.schemas import MacroAnalystOutput
from orion.analysts.technical_schemas import (
    TechnicalAnalystOutput,
    TechnicalAvailability,
)
from orion.thesis.schemas import (
    ALIGNMENT_SUMMARY,
    POINT_DETAIL,
    THESIS_VERSION,
    ConditionLevel,
    MacroProvenance,
    MarketRelevance,
    MarketThesis,
    ThesisAlignment,
    ThesisPoint,
    ThesisTimeframe,
    ThesisTopic,
)

# Re-exported so every existing `from orion.analysts.synthesis import ...`
# keeps working. Mission 8 v2.2 moved the *shapes* to `orion.thesis.schemas`
# so `orion.paper`, `orion.memory` and `orion.reflection` can name a thesis
# without importing an analyst; this module still owns the one function that
# builds one.
__all__ = [
    "ALIGNMENT_SUMMARY",
    "POINT_DETAIL",
    "THESIS_VERSION",
    "ConditionLevel",
    "MacroProvenance",
    "MarketThesis",
    "SynthesisError",
    "ThesisAlignment",
    "ThesisPoint",
    "ThesisTimeframe",
    "ThesisTopic",
    "synthesize",
]

#: The concepts whose presence means price actually did something structural.
#: A directional finding on one of these means at least one event was detected;
#: `orion.analysts.technical` reports a count of zero as NEUTRAL, so this reads
#: "something happened" rather than "the concept was evaluated".
_EVENT_CONCEPTS = frozenset(
    {
        TechnicalConcept.BREAK_OF_STRUCTURE,
        TechnicalConcept.CHANGE_OF_CHARACTER,
        TechnicalConcept.LIQUIDITY_SWEEP,
    }
)


class SynthesisError(RuntimeError):
    """Raised when a thesis cannot be formed at all.

    Own top-level hierarchy, never inheriting another layer's — this
    codebase's established rule. Raised for a naive `now` or for specialist
    outputs that describe different symbols; never for thin or absent
    evidence, which is a reportable state.
    """


def _technical_condition(technical: TechnicalAnalystOutput) -> ConditionLevel:
    """How eventful the price side is, from structured readings only.

    ACTIVE when any timeframe is in a HIGH volatility regime, or when any
    timeframe detected a structure break, change of character or liquidity
    sweep. QUIET when every timeframe that reported a regime reported LOW and
    no such event was detected anywhere. Everything else — a MEDIUM regime, an
    unknown one, no findings at all — is UNDETERMINED, because neither of the
    other two would be true.
    """
    if technical.availability is not TechnicalAvailability.AVAILABLE:
        return ConditionLevel.UNDETERMINED

    regimes = [report.volatility for report in technical.timeframes]
    has_event = any(
        finding.concept in _EVENT_CONCEPTS
        and finding.bias in (StructureBias.BULLISH, StructureBias.BEARISH)
        for finding in technical.findings
    )
    if has_event or VolatilityLevel.HIGH in regimes:
        return ConditionLevel.ACTIVE
    known = [regime for regime in regimes if regime is not VolatilityLevel.UNKNOWN]
    if known and all(regime is VolatilityLevel.LOW for regime in known):
        return ConditionLevel.QUIET
    return ConditionLevel.UNDETERMINED


def _macro_condition(macro: MacroAnalystOutput) -> ConditionLevel:
    """How significant the calendar side is, from `MarketRelevance` alone.

    HIGH is active; LOW and NONE are quiet; MEDIUM is UNDETERMINED, for the
    same reason a MEDIUM volatility regime is: it is the analyst declining to
    commit, and reading it as either extreme would put words in its mouth.
    """
    return {
        MarketRelevance.HIGH: ConditionLevel.ACTIVE,
        MarketRelevance.MEDIUM: ConditionLevel.UNDETERMINED,
        MarketRelevance.LOW: ConditionLevel.QUIET,
        MarketRelevance.NONE: ConditionLevel.QUIET,
    }[macro.market_relevance]


def _alignment(
    technical_condition: ConditionLevel, macro_condition: ConditionLevel | None
) -> ThesisAlignment:
    if macro_condition is None:
        return ThesisAlignment.MACRO_ABSENT
    if ConditionLevel.UNDETERMINED in (technical_condition, macro_condition):
        return ThesisAlignment.INSUFFICIENT_EVIDENCE
    if technical_condition is macro_condition:
        return (
            ThesisAlignment.CORROBORATED_ACTIVE
            if technical_condition is ConditionLevel.ACTIVE
            else ThesisAlignment.CORROBORATED_QUIET
        )
    return ThesisAlignment.CONFLICTED


def synthesize(
    *,
    technical: TechnicalAnalystOutput,
    macro: MacroAnalystOutput | None,
    analysis_id: str,
    now: datetime,
) -> MarketThesis:
    """Combine the specialists' evidence into one structured, advisory thesis.

    Pure and deterministic: no network, no model, no clock. `now` is required
    and caller-supplied, matching every other engine in this codebase.

    `analysis_id` IS THE ORCHESTRATOR'S OWN, PASSED THROUGH UNCHANGED. It is
    minted once per analysis from the symbol, the timeframe and the last bar's
    timestamp, and stamped identically on `ProposedDecision` and `RiskVerdict`.
    Carrying it here makes a thesis findable from a journaled verdict without
    the thesis ever referencing the proposal — a sibling key, not a
    back-reference. This function neither builds nor validates the id beyond
    its length; minting one is the orchestrator's job and always was.

    Raises `SynthesisError` for a naive `now` or a blank `analysis_id`. Absent
    macro is not an error — it is `MACRO_ABSENT`, and the technical half is
    reported in full.

    THIS FUNCTION CANNOT APPROVE ANYTHING. It returns a `MarketThesis`, which
    has no field capable of expressing an action, a size or a permission, and
    it is never consulted by `orion.decision`, `orion.risk` or `orion.paper` —
    none of which can import this package.
    """
    if now.tzinfo is None:
        raise SynthesisError("now must be a timezone-aware datetime")
    if not analysis_id.strip():
        raise SynthesisError("analysis_id must name the analysis this thesis belongs to")

    technical_condition = _technical_condition(technical)
    macro_condition = _macro_condition(macro) if macro is not None else None
    alignment = _alignment(technical_condition, macro_condition)

    agreements: list[ThesisPoint] = []
    conflicts: list[ThesisPoint] = []

    # ------------------------------------------------- technical vs macro
    if macro_condition is not None and alignment in (
        ThesisAlignment.CORROBORATED_ACTIVE,
        ThesisAlignment.CORROBORATED_QUIET,
        ThesisAlignment.CONFLICTED,
    ):
        agreed = alignment is not ThesisAlignment.CONFLICTED
        point = ThesisPoint(
            topic=ThesisTopic.MARKET_CONDITIONS,
            agreed=agreed,
            technical_reading=technical_condition.value,
            macro_reading=macro_condition.value,
            detail=POINT_DETAIL[(ThesisTopic.MARKET_CONDITIONS, agreed)],
        )
        (agreements if agreed else conflicts).append(point)

    # ------------------------------------------- technical against itself
    directional = [
        report.bias
        for report in technical.timeframes
        if report.bias in (StructureBias.BULLISH, StructureBias.BEARISH)
    ]
    if len(directional) > 1:
        agreed = technical.timeframe_agreement
        point = ThesisPoint(
            topic=ThesisTopic.TIMEFRAME_STRUCTURE,
            agreed=agreed,
            technical_reading=technical.bias.value,
            macro_reading=None,
            detail=POINT_DETAIL[(ThesisTopic.TIMEFRAME_STRUCTURE, agreed)],
        )
        (agreements if agreed else conflicts).append(point)

    return MarketThesis(
        analysis_id=analysis_id,
        symbol=technical.symbol,
        formed_at=now,
        thesis_version=THESIS_VERSION,
        alignment=alignment,
        summary=ALIGNMENT_SUMMARY[alignment],
        technical_availability=technical.availability,
        technical_bias=technical.bias,
        technical_condition=technical_condition,
        technical_timeframe_agreement=technical.timeframe_agreement,
        technical_analyst_version=technical.analyst_version,
        technical_knowledge_version=technical.knowledge_version,
        timeframes=tuple(
            ThesisTimeframe(
                timeframe=report.timeframe,
                bias=report.bias,
                volatility=report.volatility,
                finding_count=len(report.findings),
                unevaluated_count=len(report.unevaluated),
                evidence_fingerprint=report.evidence_fingerprint,
                source_provider=report.source_provider,
                retrieved_at=report.retrieved_at,
                as_of=report.as_of,
                bar_count=report.bar_count,
            )
            for report in technical.timeframes
        ),
        macro_policy_pressure=macro.policy_pressure if macro is not None else None,
        macro_relevance=macro.market_relevance if macro is not None else None,
        macro_confidence=macro.confidence if macro is not None else None,
        macro_condition=macro_condition,
        macro_provenance=(
            MacroProvenance(
                model=macro.model,
                prompt_version=macro.prompt_version,
                input_fingerprint=macro.input_fingerprint,
                source_provider=macro.source_provider,
                query_start=macro.query_start,
                query_end=macro.query_end,
                retrieved_at=macro.retrieved_at,
                analyzed_at=macro.analyzed_at,
                event_count=len(macro.key_event_assessments),
                rationale=macro.rationale,
            )
            if macro is not None
            else None
        ),
        agreements=tuple(agreements),
        conflicts=tuple(conflicts),
    )
