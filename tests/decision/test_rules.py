from __future__ import annotations

from orion.analysis.structure import TrendDirection
from orion.decision.rules import (
    RULES,
    ai_insufficient_data_requires_wait,
    context_codes,
    evaluate_all,
    proposal_is_internally_consistent,
    required_bars,
    trade_requires_known_trend,
    trade_requires_minimum_history,
    trade_requires_momentum_and_volatility_evidence,
)
from orion.decision.schemas import DecisionReasonCode, DecisionRuleName, RuleStatus
from orion.reasoning.schemas import Action, Direction, ReasoningOutput

from .conftest import DEFAULT_REQUIRED_BARS, build_evidence, build_reasoning


def test_registry_covers_every_rule_name_exactly_once() -> None:
    # A rule that is defined and tested but never wired into RULES would
    # otherwise never run — this catches that by checking the *outcomes*
    # evaluate_all actually produces, not a hand-maintained name list.
    outcomes = evaluate_all(
        build_evidence(), build_reasoning(action=Action.BUY, direction=Direction.LONG)
    )
    names = [outcome.rule for outcome in outcomes]
    assert len(names) == len(set(names))
    assert set(names) == set(DecisionRuleName)


def test_required_bars_uses_the_slowest_input() -> None:
    evidence = build_evidence()
    assert required_bars(evidence.indicators) == DEFAULT_REQUIRED_BARS


# --- ai_insufficient_data_requires_wait ---


def test_ai_insufficient_data_with_wait_passes() -> None:
    reasoning = build_reasoning(action=Action.WAIT, insufficient_data=True)
    outcome = ai_insufficient_data_requires_wait(build_evidence(), reasoning)
    assert outcome.status is RuleStatus.PASSED
    assert outcome.reason_code is None


def test_ai_insufficient_data_with_trade_proposal_fails() -> None:
    # ReasoningVerdict's own validator blocks constructing this normally —
    # model_construct bypasses validation to test this engine-side defense
    # against a value that could still arrive via a reconstructed/journaled
    # object or a future non-Claude provider.
    reasoning = ReasoningOutput.model_construct(
        action=Action.BUY,
        direction=Direction.LONG,
        confidence=0.9,
        risk_level=build_reasoning().risk_level,
        reason_codes=build_reasoning().reason_codes,
        insufficient_data=True,
        rationale="x",
        model="m",
        prompt_version="v",
        evidence_fingerprint="a" * 64,
    )
    outcome = ai_insufficient_data_requires_wait(build_evidence(), reasoning)
    assert outcome.status is RuleStatus.FAILED
    assert outcome.reason_code is DecisionReasonCode.INSUFFICIENT_DATA_WITH_TRADE_PROPOSAL


# --- proposal_is_internally_consistent ---


def test_consistent_buy_long_passes() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    outcome = proposal_is_internally_consistent(build_evidence(), reasoning)
    assert outcome.status is RuleStatus.PASSED


def test_inconsistent_buy_short_fails() -> None:
    reasoning = ReasoningOutput.model_construct(
        action=Action.BUY,
        direction=Direction.SHORT,
        confidence=0.9,
        risk_level=build_reasoning().risk_level,
        reason_codes=build_reasoning().reason_codes,
        insufficient_data=False,
        rationale="x",
        model="m",
        prompt_version="v",
        evidence_fingerprint="a" * 64,
    )
    outcome = proposal_is_internally_consistent(build_evidence(), reasoning)
    assert outcome.status is RuleStatus.FAILED
    assert outcome.reason_code is DecisionReasonCode.REASONING_SELF_CONTRADICTORY


def test_wait_with_any_direction_passes() -> None:
    for direction in (Direction.LONG, Direction.SHORT, Direction.NONE):
        reasoning = build_reasoning(action=Action.WAIT, direction=direction)
        outcome = proposal_is_internally_consistent(build_evidence(), reasoning)
        assert outcome.status is RuleStatus.PASSED


# --- trade_requires_minimum_history ---


def test_minimum_history_not_applicable_on_wait() -> None:
    reasoning = build_reasoning(action=Action.WAIT)
    outcome = trade_requires_minimum_history(build_evidence(bar_count=1), reasoning)
    assert outcome.status is RuleStatus.NOT_APPLICABLE


def test_minimum_history_passes_at_exactly_the_required_count() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(bar_count=DEFAULT_REQUIRED_BARS)
    outcome = trade_requires_minimum_history(evidence, reasoning)
    assert outcome.status is RuleStatus.PASSED


def test_minimum_history_fails_one_below_the_required_count() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(bar_count=DEFAULT_REQUIRED_BARS - 1)
    outcome = trade_requires_minimum_history(evidence, reasoning)
    assert outcome.status is RuleStatus.FAILED
    assert outcome.reason_code is DecisionReasonCode.EVIDENCE_HISTORY_TOO_THIN


# --- trade_requires_known_trend ---


def test_known_trend_not_applicable_on_wait() -> None:
    reasoning = build_reasoning(action=Action.WAIT)
    evidence = build_evidence(trend=TrendDirection.UNKNOWN)
    outcome = trade_requires_known_trend(evidence, reasoning)
    assert outcome.status is RuleStatus.NOT_APPLICABLE


def test_unknown_trend_fails_a_trade_proposal() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(trend=TrendDirection.UNKNOWN)
    outcome = trade_requires_known_trend(evidence, reasoning)
    assert outcome.status is RuleStatus.FAILED
    assert outcome.reason_code is DecisionReasonCode.EVIDENCE_TREND_UNKNOWN


def test_sideways_trend_passes_a_trade_proposal() -> None:
    # A range trade is a legitimate proposal — only UNKNOWN blocks.
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(trend=TrendDirection.SIDEWAYS)
    outcome = trade_requires_known_trend(evidence, reasoning)
    assert outcome.status is RuleStatus.PASSED


# --- trade_requires_momentum_and_volatility_evidence ---


def test_momentum_volatility_not_applicable_on_wait() -> None:
    reasoning = build_reasoning(action=Action.WAIT)
    evidence = build_evidence(rsi=None, atr=None)
    outcome = trade_requires_momentum_and_volatility_evidence(evidence, reasoning)
    assert outcome.status is RuleStatus.NOT_APPLICABLE


def test_missing_rsi_fails_a_trade_proposal() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(rsi=None)
    outcome = trade_requires_momentum_and_volatility_evidence(evidence, reasoning)
    assert outcome.status is RuleStatus.FAILED
    assert outcome.reason_code is DecisionReasonCode.EVIDENCE_INDICATORS_UNAVAILABLE


def test_missing_atr_fails_a_trade_proposal() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(atr=None)
    outcome = trade_requires_momentum_and_volatility_evidence(evidence, reasoning)
    assert outcome.status is RuleStatus.FAILED


def test_available_rsi_and_atr_pass_a_trade_proposal() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(rsi=55.0, atr=7.0)
    outcome = trade_requires_momentum_and_volatility_evidence(evidence, reasoning)
    assert outcome.status is RuleStatus.PASSED


# --- evaluate_all / context_codes ---


def test_evaluate_all_runs_every_rule_and_never_short_circuits() -> None:
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)
    evidence = build_evidence(bar_count=1, trend=TrendDirection.UNKNOWN, rsi=None, atr=None)
    outcomes = evaluate_all(evidence, reasoning)
    assert len(outcomes) == len(RULES)
    assert sum(1 for o in outcomes if o.status is RuleStatus.FAILED) == 3


def test_context_codes_flags_insufficient_data() -> None:
    reasoning = build_reasoning(action=Action.WAIT, insufficient_data=True)
    assert DecisionReasonCode.AI_FLAGGED_INSUFFICIENT_DATA in context_codes(reasoning)


def test_context_codes_flags_ai_proposed_wait() -> None:
    reasoning = build_reasoning(action=Action.WAIT)
    assert DecisionReasonCode.AI_PROPOSED_WAIT in context_codes(reasoning)


def test_context_codes_empty_for_a_confident_trade_proposal() -> None:
    reasoning = build_reasoning(
        action=Action.BUY, direction=Direction.LONG, insufficient_data=False
    )
    assert context_codes(reasoning) == ()
