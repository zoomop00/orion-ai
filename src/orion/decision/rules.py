"""The Decision Engine's deterministic rules.

Every rule is a pure function of one `Evidence` + one `ReasoningOutput` —
already-validated, already-computed objects. A rule never recomputes a
technical judgment `orion.analysis` already made (that would duplicate
`analysis/` under a different name) and never reads `ReasoningOutput.
rationale` or `.confidence` (free-form text and an uncalibrated number must
never influence a deterministic rule). All rules always run; the engine never
short-circuits, because a record showing every check is a better audit trail
than one showing only the first failure.

`trade_requires_minimum_history`, `trade_requires_known_trend`, and
`trade_requires_momentum_and_volatility_evidence` apply only to a proposed
trade (`action is not WAIT`) — there is nothing to gate on a WAIT, so they
report `NOT_APPLICABLE` instead of a vacuous pass.
"""

from __future__ import annotations

from collections.abc import Callable

from orion.analysis.evidence import Evidence
from orion.analysis.indicators import IndicatorSnapshot
from orion.analysis.structure import TrendDirection
from orion.decision.schemas import DecisionReasonCode, DecisionRuleName, RuleOutcome, RuleStatus
from orion.reasoning.schemas import Action, Direction, ReasoningOutput

Rule = Callable[[Evidence, ReasoningOutput], RuleOutcome]


def required_bars(indicators: IndicatorSnapshot) -> int:
    """The minimum bar count for every indicator in this snapshot to be computable.

    Derived from the snapshot's own declared lengths (matching
    `compute_indicators`'s availability minimums, including the `+1` offsets
    on RSI/ATR) rather than a hardcoded constant — a fixed number here would
    be exactly the kind of unjustified magic number `docs/DECISIONS.md`
    warns against for the volatility thresholds. If `compute_indicators`'s
    minimums ever change, this must change with it.
    """
    return max(
        indicators.ema_fast_length,
        indicators.ema_slow_length,
        indicators.sma_length,
        indicators.rsi_length + 1,
        indicators.atr_length + 1,
    )


def _not_applicable(rule: DecisionRuleName) -> RuleOutcome:
    return RuleOutcome(
        rule=rule,
        status=RuleStatus.NOT_APPLICABLE,
        detail="action=wait; rule applies to trade proposals only",
    )


def ai_insufficient_data_requires_wait(
    evidence: Evidence, reasoning: ReasoningOutput
) -> RuleOutcome:
    """Restated defense-in-depth: `ReasoningVerdict` already enforces this invariant
    at construction time, but a `ReasoningOutput` reaching this engine may have been
    reconstructed from journaled JSON (Mission 2) under a relaxed or older schema, or
    produced by a future non-Claude `ReasoningProvider` — this engine's own guarantee
    must not depend on another layer's diligence.
    """
    rule = DecisionRuleName.AI_INSUFFICIENT_DATA_REQUIRES_WAIT
    if reasoning.insufficient_data and reasoning.action is not Action.WAIT:
        return RuleOutcome(
            rule=rule,
            status=RuleStatus.FAILED,
            detail=f"insufficient_data=True but action={reasoning.action.value!r}",
            reason_code=DecisionReasonCode.INSUFFICIENT_DATA_WITH_TRADE_PROPOSAL,
        )
    return RuleOutcome(
        rule=rule, status=RuleStatus.PASSED, detail="insufficient_data is consistent with action"
    )


def proposal_is_internally_consistent(
    evidence: Evidence, reasoning: ReasoningOutput
) -> RuleOutcome:
    """Restated defense-in-depth, same rationale as `ai_insufficient_data_requires_wait`."""
    rule = DecisionRuleName.PROPOSAL_IS_INTERNALLY_CONSISTENT
    mismatched = (reasoning.action is Action.BUY and reasoning.direction is not Direction.LONG) or (
        reasoning.action is Action.SELL and reasoning.direction is not Direction.SHORT
    )
    if mismatched:
        return RuleOutcome(
            rule=rule,
            status=RuleStatus.FAILED,
            detail=f"action={reasoning.action.value!r} direction={reasoning.direction.value!r}",
            reason_code=DecisionReasonCode.REASONING_SELF_CONTRADICTORY,
        )
    return RuleOutcome(
        rule=rule, status=RuleStatus.PASSED, detail="action/direction pairing is consistent"
    )


def trade_requires_minimum_history(evidence: Evidence, reasoning: ReasoningOutput) -> RuleOutcome:
    rule = DecisionRuleName.TRADE_REQUIRES_MINIMUM_HISTORY
    if reasoning.action is Action.WAIT:
        return _not_applicable(rule)
    needed = required_bars(evidence.indicators)
    if evidence.bar_count < needed:
        return RuleOutcome(
            rule=rule,
            status=RuleStatus.FAILED,
            detail=f"bar_count={evidence.bar_count} < required {needed}",
            reason_code=DecisionReasonCode.EVIDENCE_HISTORY_TOO_THIN,
        )
    return RuleOutcome(
        rule=rule,
        status=RuleStatus.PASSED,
        detail=f"bar_count={evidence.bar_count} >= required {needed}",
    )


def trade_requires_known_trend(evidence: Evidence, reasoning: ReasoningOutput) -> RuleOutcome:
    """SIDEWAYS is allowed through — a range trade is a legitimate proposal, and
    blocking it would be this engine substituting its own technical opinion for the
    AI's. Only UNKNOWN (no trend input at all) fails.
    """
    rule = DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND
    if reasoning.action is Action.WAIT:
        return _not_applicable(rule)
    if evidence.structure.trend is TrendDirection.UNKNOWN:
        return RuleOutcome(
            rule=rule,
            status=RuleStatus.FAILED,
            detail="structure.trend is unknown",
            reason_code=DecisionReasonCode.EVIDENCE_TREND_UNKNOWN,
        )
    return RuleOutcome(
        rule=rule,
        status=RuleStatus.PASSED,
        detail=f"structure.trend={evidence.structure.trend.value!r}",
    )


def trade_requires_momentum_and_volatility_evidence(
    evidence: Evidence, reasoning: ReasoningOutput
) -> RuleOutcome:
    """Checks only availability, never a value — no RSI threshold, no ATR band.
    Scoped to rsi/atr and not the EMAs, since EMA availability is what
    `trade_requires_known_trend` already covers via `structure.trend`.
    """
    rule = DecisionRuleName.TRADE_REQUIRES_MOMENTUM_AND_VOLATILITY_EVIDENCE
    if reasoning.action is Action.WAIT:
        return _not_applicable(rule)
    rsi, atr = evidence.indicators.rsi, evidence.indicators.atr
    if rsi is None or atr is None:
        return RuleOutcome(
            rule=rule,
            status=RuleStatus.FAILED,
            detail=f"rsi={rsi!r}, atr={atr!r}",
            reason_code=DecisionReasonCode.EVIDENCE_INDICATORS_UNAVAILABLE,
        )
    return RuleOutcome(rule=rule, status=RuleStatus.PASSED, detail=f"rsi={rsi}, atr={atr}")


RULES: tuple[Rule, ...] = (
    ai_insufficient_data_requires_wait,
    proposal_is_internally_consistent,
    trade_requires_minimum_history,
    trade_requires_known_trend,
    trade_requires_momentum_and_volatility_evidence,
)


def evaluate_all(evidence: Evidence, reasoning: ReasoningOutput) -> tuple[RuleOutcome, ...]:
    """Run every rule in `RULES`, in order. Never short-circuits."""
    return tuple(rule(evidence, reasoning) for rule in RULES)


def context_codes(reasoning: ReasoningOutput) -> tuple[DecisionReasonCode, ...]:
    """Explanatory codes derived from the inputs, distinct from rule failures.

    When the AI correctly flags insufficient data, `ai_insufficient_data_requires_wait`
    *passes* — and a passing rule contributes no reason code. Without these context
    codes a resulting WAIT would carry only ALL_RULES_PASSED, with no way to answer
    "why is this a WAIT?". These never change `action`; they only explain it.
    """
    codes: list[DecisionReasonCode] = []
    if reasoning.insufficient_data:
        codes.append(DecisionReasonCode.AI_FLAGGED_INSUFFICIENT_DATA)
    if reasoning.action is Action.WAIT:
        codes.append(DecisionReasonCode.AI_PROPOSED_WAIT)
    return tuple(codes)
