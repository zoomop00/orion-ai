from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.analysis.structure import TrendDirection
from orion.decision.engine import ENGINE_VERSION, DecisionEngineError, EvidenceMismatchError, decide
from orion.decision.schemas import (
    DecisionReasonCode,
    DecisionRuleName,
    ProposedDecision,
    RuleStatus,
)
from orion.reasoning.schemas import Action, Direction, ReasoningOutput

from .conftest import DEFAULT_REQUIRED_BARS, build_evidence, build_reasoning

_NOW = datetime(2026, 8, 23, 12, 5, tzinfo=UTC)


def _decide(
    evidence: Evidence, reasoning: ReasoningOutput, *, analysis_id: str = "analysis-1"
) -> ProposedDecision:
    return decide(evidence, reasoning, analysis_id=analysis_id, now=_NOW)


# --- valid BUY / SELL / WAIT proposals ---


def test_valid_buy_proposal_passes_through_unchanged() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG, confidence=0.8)

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.BUY
    assert decision.direction is Direction.LONG
    assert decision.is_trade_proposal is True
    assert DecisionReasonCode.ALL_RULES_PASSED in decision.reason_codes
    assert DecisionReasonCode.DOWNGRADED_TO_WAIT not in decision.reason_codes


def test_valid_sell_proposal_passes_through_unchanged() -> None:
    evidence = build_evidence(trend=TrendDirection.BEARISH)
    reasoning = build_reasoning(
        action=Action.SELL, direction=Direction.SHORT, confidence=0.75, evidence=evidence
    )

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.SELL
    assert decision.direction is Direction.SHORT
    assert DecisionReasonCode.ALL_RULES_PASSED in decision.reason_codes


def test_wait_proposal_passes_through() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.WAIT, direction=Direction.NONE)

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert decision.is_trade_proposal is False
    assert DecisionReasonCode.AI_PROPOSED_WAIT in decision.reason_codes


def test_wait_with_directional_bias_preserves_the_bias() -> None:
    # The brief's own example: WAIT + LONG is a legitimate "biased, no entry yet".
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.WAIT, direction=Direction.LONG)

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert decision.direction is Direction.LONG


# --- insufficient data ---


def test_insufficient_data_results_in_wait() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.WAIT, insufficient_data=True)

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert DecisionReasonCode.AI_FLAGGED_INSUFFICIENT_DATA in decision.reason_codes


# --- conflicting direction/action ---


def test_self_contradictory_reasoning_is_downgraded_to_wait() -> None:
    # ReasoningVerdict's own validator blocks constructing BUY+SHORT normally;
    # model_construct bypasses it to simulate a journaled/reconstructed object
    # that could still arrive here from outside this process's own validation.
    # evidence_fingerprint must match the evidence used below, or the new
    # mismatch check (not what this test is exercising) would fire first.
    evidence = build_evidence()
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
        evidence_fingerprint=evidence_fingerprint(evidence),
    )

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert decision.direction is Direction.NONE
    assert DecisionReasonCode.REASONING_SELF_CONTRADICTORY in decision.reason_codes
    assert DecisionReasonCode.DOWNGRADED_TO_WAIT in decision.reason_codes


# --- conflicting evidence ---


def test_thin_history_downgrades_a_trade_proposal_to_wait() -> None:
    evidence = build_evidence(bar_count=DEFAULT_REQUIRED_BARS - 1)
    reasoning = build_reasoning(
        action=Action.BUY, direction=Direction.LONG, confidence=0.9, evidence=evidence
    )

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert DecisionReasonCode.EVIDENCE_HISTORY_TOO_THIN in decision.reason_codes


def test_unknown_trend_downgrades_a_trade_proposal_to_wait() -> None:
    evidence = build_evidence(trend=TrendDirection.UNKNOWN)
    reasoning = build_reasoning(
        action=Action.BUY, direction=Direction.LONG, confidence=0.9, evidence=evidence
    )

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert DecisionReasonCode.EVIDENCE_TREND_UNKNOWN in decision.reason_codes


def test_missing_indicators_downgrade_a_trade_proposal_to_wait() -> None:
    evidence = build_evidence(rsi=None, atr=None)
    reasoning = build_reasoning(
        action=Action.SELL, direction=Direction.SHORT, confidence=0.9, evidence=evidence
    )

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    assert DecisionReasonCode.EVIDENCE_INDICATORS_UNAVAILABLE in decision.reason_codes


def test_multiple_evidence_failures_are_all_recorded() -> None:
    evidence = build_evidence(bar_count=1, trend=TrendDirection.UNKNOWN, rsi=None, atr=None)
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG, evidence=evidence)

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.WAIT
    failed = {o.rule for o in decision.rule_outcomes if o.status is RuleStatus.FAILED}
    assert failed == {
        DecisionRuleName.TRADE_REQUIRES_MINIMUM_HISTORY,
        DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND,
        DecisionRuleName.TRADE_REQUIRES_MOMENTUM_AND_VOLATILITY_EVIDENCE,
    }


# --- evidence/reasoning linkage integrity ---


def test_matching_evidence_and_reasoning_are_accepted() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG, evidence=evidence)

    # Must not raise, and must actually evaluate the proposal.
    decision = _decide(evidence, reasoning)

    assert decision.action is Action.BUY


def test_mismatched_evidence_and_reasoning_raise_evidence_mismatch_error() -> None:
    evidence_used_for_reasoning = build_evidence(bar_count=60)
    evidence_given_to_engine = build_evidence(bar_count=61)
    reasoning = build_reasoning(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence_used_for_reasoning
    )

    with pytest.raises(EvidenceMismatchError, match="evidence_fingerprint"):
        decide(evidence_given_to_engine, reasoning, analysis_id="a", now=_NOW)


def test_evidence_mismatch_error_is_a_decision_engine_error() -> None:
    # Callers that only catch the general DecisionEngineError must still
    # catch this — it is a fail-closed case, not a new failure mode to miss.
    assert issubclass(EvidenceMismatchError, DecisionEngineError)


def test_mismatch_never_produces_a_wait_decision() -> None:
    # The integrity failure must be visibly unresolvable, not laundered into
    # a plausible-looking WAIT ProposedDecision that reads like ordinary
    # market uncertainty.
    evidence_used_for_reasoning = build_evidence(bar_count=60)
    evidence_given_to_engine = build_evidence(bar_count=61)
    reasoning = build_reasoning(
        action=Action.WAIT, evidence=evidence_used_for_reasoning
    )  # even a WAIT proposal must not be silently accepted on a mismatch

    with pytest.raises(EvidenceMismatchError):
        decide(evidence_given_to_engine, reasoning, analysis_id="a", now=_NOW)


def test_mismatch_is_detected_even_for_a_subtle_evidence_difference() -> None:
    evidence_a = build_evidence(last_price=2400.0)
    evidence_b = build_evidence(last_price=2400.01)
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG, evidence=evidence_a)

    with pytest.raises(EvidenceMismatchError):
        decide(evidence_b, reasoning, analysis_id="a", now=_NOW)


# --- missing evidence / fail-closed behavior ---


def test_none_evidence_raises_decision_engine_error() -> None:
    with pytest.raises(DecisionEngineError, match="evidence"):
        decide(cast(Evidence, None), build_reasoning(), analysis_id="a", now=_NOW)


def test_none_reasoning_raises_decision_engine_error() -> None:
    with pytest.raises(DecisionEngineError, match="reasoning"):
        decide(build_evidence(), cast(ReasoningOutput, None), analysis_id="a", now=_NOW)


def test_wrong_type_evidence_raises_decision_engine_error() -> None:
    with pytest.raises(DecisionEngineError):
        decide(cast(Evidence, {"not": "evidence"}), build_reasoning(), analysis_id="a", now=_NOW)


def test_blank_analysis_id_raises_decision_engine_error() -> None:
    with pytest.raises(DecisionEngineError, match="analysis_id"):
        decide(build_evidence(), build_reasoning(), analysis_id="   ", now=_NOW)


def test_non_positive_last_price_raises_decision_engine_error() -> None:
    evidence = build_evidence(last_price=0.0)
    with pytest.raises(DecisionEngineError, match="last_price"):
        decide(evidence, build_reasoning(evidence=evidence), analysis_id="a", now=_NOW)


def test_zero_bar_count_raises_decision_engine_error() -> None:
    evidence = build_evidence(bar_count=0)
    with pytest.raises(DecisionEngineError, match="bar_count"):
        decide(evidence, build_reasoning(evidence=evidence), analysis_id="a", now=_NOW)


def test_engine_error_is_raised_not_converted_to_wait() -> None:
    # A malformed input must never be laundered into a plausible-looking
    # abstention — it must be visibly unresolvable.
    with pytest.raises(DecisionEngineError):
        decide(cast(Evidence, None), build_reasoning(), analysis_id="a", now=_NOW)


# --- deterministic repeatability ---


def test_identical_inputs_produce_an_identical_decision() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)

    first = decide(evidence, reasoning, analysis_id="fixed-id", now=_NOW)
    second = decide(evidence, reasoning, analysis_id="fixed-id", now=_NOW)

    assert first == second


def test_identical_inputs_produce_an_identical_wait_decision() -> None:
    evidence = build_evidence(trend=TrendDirection.UNKNOWN)
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG, evidence=evidence)

    first = decide(evidence, reasoning, analysis_id="fixed-id", now=_NOW)
    second = decide(evidence, reasoning, analysis_id="fixed-id", now=_NOW)

    assert first == second


# --- AI confidence is never treated as authorization ---


def test_confidence_does_not_affect_the_decision() -> None:
    evidence = build_evidence()
    low = build_reasoning(action=Action.BUY, direction=Direction.LONG, confidence=0.01)
    high = build_reasoning(action=Action.BUY, direction=Direction.LONG, confidence=0.99)

    low_decision = decide(evidence, low, analysis_id="a", now=_NOW)
    high_decision = decide(evidence, high, analysis_id="a", now=_NOW)

    assert low_decision.action == high_decision.action
    assert low_decision.direction == high_decision.direction
    assert low_decision.reason_codes == high_decision.reason_codes
    assert low_decision.rule_outcomes == high_decision.rule_outcomes
    # Only the provenance reference should differ.
    assert low_decision.reasoning_ref.confidence != high_decision.reasoning_ref.confidence


def test_low_confidence_alone_does_not_force_a_wait() -> None:
    # Confidence is informational, not a gate — a low-confidence but
    # otherwise-consistent proposal still passes through unchanged. A
    # confidence floor is a Risk Guardian concern, not this engine's.
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG, confidence=0.05)

    decision = _decide(evidence, reasoning)

    assert decision.action is Action.BUY


def test_rationale_does_not_affect_the_decision() -> None:
    evidence = build_evidence()
    reasoning_a = build_reasoning(
        action=Action.BUY, direction=Direction.LONG, rationale="Short reason."
    )
    reasoning_b = build_reasoning(
        action=Action.BUY,
        direction=Direction.LONG,
        rationale="A completely different explanation entirely.",
    )

    decision_a = decide(evidence, reasoning_a, analysis_id="a", now=_NOW)
    decision_b = decide(evidence, reasoning_b, analysis_id="a", now=_NOW)

    assert decision_a.action == decision_b.action
    assert decision_a.reason_codes == decision_b.reason_codes
    assert decision_a.rule_outcomes == decision_b.rule_outcomes
    assert decision_a.reasoning_ref.content_hash != decision_b.reasoning_ref.content_hash


# --- provenance ---


def test_provenance_fields_trace_back_to_the_inputs() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)

    decision = decide(evidence, reasoning, analysis_id="analysis-42", now=_NOW)

    assert decision.analysis_id == "analysis-42"
    assert decision.decided_at == _NOW
    assert decision.engine_version == ENGINE_VERSION
    assert decision.evidence_ref.symbol == evidence.symbol
    assert decision.evidence_ref.timeframe == evidence.timeframe
    assert decision.evidence_ref.as_of == evidence.as_of
    assert decision.evidence_ref.bar_count == evidence.bar_count
    assert decision.reasoning_ref.model == reasoning.model
    assert decision.reasoning_ref.prompt_version == reasoning.prompt_version
    assert decision.reasoning_ref.action is reasoning.action


# --- rule outcomes ---


def test_rule_outcomes_report_all_five_rules_for_a_trade_proposal() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.BUY, direction=Direction.LONG)

    decision = _decide(evidence, reasoning)

    assert len(decision.rule_outcomes) == 5
    assert all(o.status is RuleStatus.PASSED for o in decision.rule_outcomes)


def test_rule_outcomes_are_not_applicable_for_a_wait_proposal() -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(action=Action.WAIT)

    decision = _decide(evidence, reasoning)

    trade_only_rules = {
        DecisionRuleName.TRADE_REQUIRES_MINIMUM_HISTORY,
        DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND,
        DecisionRuleName.TRADE_REQUIRES_MOMENTUM_AND_VOLATILITY_EVIDENCE,
    }
    for outcome in decision.rule_outcomes:
        if outcome.rule in trade_only_rules:
            assert outcome.status is RuleStatus.NOT_APPLICABLE
