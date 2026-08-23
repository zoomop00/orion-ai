from __future__ import annotations

import pytest
from pydantic import ValidationError

from orion.decision.schemas import (
    DecisionReasonCode,
    DecisionRuleName,
    EvidenceRef,
    ProposedDecision,
    ReasoningRef,
    RuleOutcome,
    RuleStatus,
)
from orion.reasoning.schemas import Action, Direction

from .conftest import BASE_TIME, build_evidence, build_reasoning


def _passed(rule: DecisionRuleName = DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND) -> RuleOutcome:
    return RuleOutcome(rule=rule, status=RuleStatus.PASSED, detail="ok")


def _failed(
    rule: DecisionRuleName = DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND,
    reason_code: DecisionReasonCode = DecisionReasonCode.EVIDENCE_TREND_UNKNOWN,
) -> RuleOutcome:
    return RuleOutcome(rule=rule, status=RuleStatus.FAILED, detail="bad", reason_code=reason_code)


def _decision(**overrides: object) -> ProposedDecision:
    evidence_ref = EvidenceRef.from_evidence(build_evidence())
    reasoning_ref = ReasoningRef.from_reasoning(build_reasoning(action=Action.WAIT))
    defaults: dict[str, object] = {
        "analysis_id": "test-1",
        "decided_at": BASE_TIME,
        "action": Action.WAIT,
        "direction": Direction.NONE,
        "reason_codes": (DecisionReasonCode.ALL_RULES_PASSED,),
        "rule_outcomes": (_passed(),),
        "evidence_ref": evidence_ref,
        "reasoning_ref": reasoning_ref,
        "engine_version": "test-engine-1",
    }
    defaults.update(overrides)
    return ProposedDecision(**defaults)


# --- RuleOutcome ---


def test_failed_outcome_requires_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        RuleOutcome(
            rule=DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND, status=RuleStatus.FAILED, detail="x"
        )


def test_passed_outcome_forbids_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        RuleOutcome(
            rule=DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND,
            status=RuleStatus.PASSED,
            detail="x",
            reason_code=DecisionReasonCode.EVIDENCE_TREND_UNKNOWN,
        )


def test_not_applicable_outcome_forbids_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        RuleOutcome(
            rule=DecisionRuleName.TRADE_REQUIRES_KNOWN_TREND,
            status=RuleStatus.NOT_APPLICABLE,
            detail="x",
            reason_code=DecisionReasonCode.EVIDENCE_TREND_UNKNOWN,
        )


def test_valid_failed_outcome_constructs() -> None:
    outcome = _failed()
    assert outcome.status is RuleStatus.FAILED


# --- EvidenceRef / ReasoningRef content hashing ---


def test_evidence_ref_hash_is_deterministic() -> None:
    evidence = build_evidence()
    assert (
        EvidenceRef.from_evidence(evidence).content_hash
        == EvidenceRef.from_evidence(evidence).content_hash
    )


def test_evidence_ref_hash_changes_with_content() -> None:
    ref_a = EvidenceRef.from_evidence(build_evidence(bar_count=60))
    ref_b = EvidenceRef.from_evidence(build_evidence(bar_count=61))
    assert ref_a.content_hash != ref_b.content_hash


def test_reasoning_ref_hash_is_deterministic() -> None:
    reasoning = build_reasoning()
    assert (
        ReasoningRef.from_reasoning(reasoning).content_hash
        == ReasoningRef.from_reasoning(reasoning).content_hash
    )


def test_reasoning_ref_excludes_rationale_field_but_hash_still_covers_it() -> None:
    ref = ReasoningRef.from_reasoning(build_reasoning())
    assert not hasattr(ref, "rationale")

    ref_a = ReasoningRef.from_reasoning(build_reasoning(rationale="First explanation."))
    ref_b = ReasoningRef.from_reasoning(build_reasoning(rationale="A completely different one."))
    assert ref_a.content_hash != ref_b.content_hash


# --- ProposedDecision invariants ---


def test_valid_wait_decision_constructs() -> None:
    decision = _decision()
    assert decision.action is Action.WAIT
    assert decision.is_trade_proposal is False


def test_valid_buy_decision_matching_reasoning_constructs() -> None:
    reasoning_ref = ReasoningRef.from_reasoning(
        build_reasoning(action=Action.BUY, direction=Direction.LONG)
    )
    decision = _decision(
        action=Action.BUY,
        direction=Direction.LONG,
        reasoning_ref=reasoning_ref,
        rule_outcomes=(_passed(),),
        reason_codes=(DecisionReasonCode.ALL_RULES_PASSED,),
    )
    assert decision.action is Action.BUY
    assert decision.is_trade_proposal is True


def test_failed_rule_requires_wait_action() -> None:
    with pytest.raises(ValidationError, match="failed"):
        _decision(
            action=Action.BUY,
            direction=Direction.LONG,
            rule_outcomes=(_failed(),),
            reasoning_ref=ReasoningRef.from_reasoning(
                build_reasoning(action=Action.BUY, direction=Direction.LONG)
            ),
        )


def test_action_cannot_diverge_from_reasoning_without_a_failed_rule() -> None:
    # All rules passed, but the decision claims SELL while the AI proposed BUY —
    # the engine may only narrow to WAIT, never invent a different action.
    with pytest.raises(ValidationError, match="differs from the AI's proposal"):
        _decision(
            action=Action.SELL,
            direction=Direction.SHORT,
            rule_outcomes=(_passed(),),
            reasoning_ref=ReasoningRef.from_reasoning(
                build_reasoning(action=Action.BUY, direction=Direction.LONG)
            ),
        )


def test_buy_requires_long_direction() -> None:
    with pytest.raises(ValidationError, match="BUY requires LONG"):
        _decision(
            action=Action.BUY,
            direction=Direction.SHORT,
            reasoning_ref=ReasoningRef.from_reasoning(
                build_reasoning(action=Action.BUY, direction=Direction.LONG)
            ),
        )


def test_sell_requires_short_direction() -> None:
    with pytest.raises(ValidationError, match="SELL requires SHORT"):
        _decision(
            action=Action.SELL,
            direction=Direction.LONG,
            reasoning_ref=ReasoningRef.from_reasoning(
                build_reasoning(action=Action.SELL, direction=Direction.SHORT)
            ),
        )


def test_duplicate_reason_codes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _decision(
            reason_codes=(DecisionReasonCode.ALL_RULES_PASSED, DecisionReasonCode.ALL_RULES_PASSED)
        )


def test_duplicate_rule_outcomes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _decision(rule_outcomes=(_passed(), _passed()))


def test_decision_is_frozen() -> None:
    decision = _decision()
    with pytest.raises(ValidationError):
        decision.action = Action.BUY  # type: ignore[misc]


def test_unexpected_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _decision(extra_field="nope")
