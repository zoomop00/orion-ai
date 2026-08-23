from __future__ import annotations

import pytest
from pydantic import ValidationError

from orion.reasoning.schemas import Action, Direction
from orion.risk.schemas import (
    ControlOutcome,
    ControlStatus,
    DecisionRef,
    ExecutionMode,
    PortfolioStateRef,
    RiskControlName,
    RiskOutcome,
    RiskPolicyRef,
    RiskReasonCode,
    RiskVerdict,
)
from tests.risk.conftest import BASE_TIME, build_policy, build_portfolio, build_proposal


def _passed(control: RiskControlName = RiskControlName.EVIDENCE_IS_FRESH) -> ControlOutcome:
    return ControlOutcome(control=control, status=ControlStatus.PASSED, detail="ok")


def _failed(
    control: RiskControlName = RiskControlName.EVIDENCE_IS_FRESH,
    reason_code: RiskReasonCode = RiskReasonCode.EVIDENCE_STALE,
) -> ControlOutcome:
    return ControlOutcome(
        control=control, status=ControlStatus.FAILED, detail="bad", reason_code=reason_code
    )


def _verdict(**overrides: object) -> RiskVerdict:
    proposal = build_proposal(action=Action.WAIT)
    defaults: dict[str, object] = {
        "analysis_id": "test-1",
        "reviewed_at": BASE_TIME,
        "outcome": RiskOutcome.NO_TRADE_PROPOSED,
        "action": Action.WAIT,
        "direction": Direction.NONE,
        "approved_execution_mode": ExecutionMode.NONE,
        "reason_codes": (RiskReasonCode.NO_TRADE_PROPOSED,),
        "control_outcomes": (_passed(),),
        "decision_ref": DecisionRef.from_proposal(proposal),
        "policy_ref": RiskPolicyRef.from_policy(build_policy()),
        "portfolio_ref": PortfolioStateRef.from_portfolio(build_portfolio()),
        "guardian_version": "test-guardian-1",
    }
    defaults.update(overrides)
    return RiskVerdict(**defaults)


# --- ControlOutcome ---


def test_failed_outcome_requires_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        ControlOutcome(
            control=RiskControlName.EVIDENCE_IS_FRESH, status=ControlStatus.FAILED, detail="x"
        )


def test_passed_outcome_forbids_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        ControlOutcome(
            control=RiskControlName.EVIDENCE_IS_FRESH,
            status=ControlStatus.PASSED,
            detail="x",
            reason_code=RiskReasonCode.EVIDENCE_STALE,
        )


def test_not_applicable_outcome_forbids_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        ControlOutcome(
            control=RiskControlName.EVIDENCE_IS_FRESH,
            status=ControlStatus.NOT_APPLICABLE,
            detail="x",
            reason_code=RiskReasonCode.EVIDENCE_STALE,
        )


# --- DecisionRef / RiskPolicyRef ---


def test_decision_ref_does_not_expose_confidence_or_risk_level() -> None:
    assert "confidence" not in DecisionRef.model_fields
    assert "risk_level" not in DecisionRef.model_fields


def test_decision_ref_carries_ai_action_distinct_from_engine_action() -> None:
    proposal = build_proposal(action=Action.WAIT)  # AI proposed WAIT and it stayed WAIT
    ref = DecisionRef.from_proposal(proposal)
    assert ref.ai_action is Action.WAIT
    assert ref.action is Action.WAIT


def test_risk_policy_ref_hash_changes_with_any_threshold() -> None:
    ref_a = RiskPolicyRef.from_policy(build_policy(min_risk_reward=1.0))
    ref_b = RiskPolicyRef.from_policy(build_policy(min_risk_reward=1.5))
    assert ref_a.content_hash != ref_b.content_hash


# --- RiskVerdict invariants ---


def test_valid_no_trade_verdict_constructs() -> None:
    verdict = _verdict()
    assert verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED
    assert verdict.approved is False


def test_valid_approved_verdict_constructs() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    verdict = _verdict(
        outcome=RiskOutcome.APPROVED,
        action=Action.BUY,
        direction=Direction.LONG,
        approved_execution_mode=ExecutionMode.PAPER,
        reason_codes=(RiskReasonCode.ALL_CONTROLS_PASSED,),
        control_outcomes=(_passed(),),
        decision_ref=DecisionRef.from_proposal(proposal),
    )
    assert verdict.approved is True


def test_failed_control_requires_wait_action_and_none_direction() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    with pytest.raises(ValidationError, match="failed"):
        _verdict(
            outcome=RiskOutcome.VETOED,
            action=Action.BUY,
            direction=Direction.LONG,
            approved_execution_mode=ExecutionMode.NONE,
            reason_codes=(RiskReasonCode.VETOED_BY_RISK_CONTROL,),
            control_outcomes=(_failed(),),
            decision_ref=DecisionRef.from_proposal(proposal),
        )


def test_action_cannot_diverge_from_decision_ref_without_a_failed_control() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    with pytest.raises(ValidationError, match="differs from the Decision Engine's proposal"):
        _verdict(
            outcome=RiskOutcome.VETOED,
            action=Action.WAIT,
            direction=Direction.NONE,
            approved_execution_mode=ExecutionMode.NONE,
            reason_codes=(RiskReasonCode.VETOED_BY_RISK_CONTROL,),
            control_outcomes=(_passed(),),
            decision_ref=DecisionRef.from_proposal(proposal),
        )


def test_wait_proposal_can_never_be_upgraded() -> None:
    proposal = build_proposal(action=Action.WAIT)
    with pytest.raises(ValidationError, match="never upgrade"):
        _verdict(
            outcome=RiskOutcome.APPROVED,
            action=Action.BUY,
            direction=Direction.LONG,
            approved_execution_mode=ExecutionMode.PAPER,
            reason_codes=(RiskReasonCode.ALL_CONTROLS_PASSED,),
            control_outcomes=(_passed(),),
            decision_ref=DecisionRef.from_proposal(proposal),
        )


def test_buy_requires_long_direction() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    with pytest.raises(ValidationError, match="BUY requires LONG"):
        _verdict(
            outcome=RiskOutcome.APPROVED,
            action=Action.BUY,
            direction=Direction.SHORT,
            approved_execution_mode=ExecutionMode.PAPER,
            reason_codes=(RiskReasonCode.ALL_CONTROLS_PASSED,),
            control_outcomes=(_passed(),),
            decision_ref=DecisionRef.from_proposal(proposal),
        )


def test_no_trade_proposed_requires_wait_decision_ref() -> None:
    # A failed control is what makes action=WAIT legitimate here (see
    # `test_failed_control_requires_wait_action_and_none_direction`) --
    # using _passed() would trip the earlier "action differs from the
    # Decision Engine's proposal" check first and never reach the
    # NO_TRADE_PROPOSED-specific invariant this test targets.
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    with pytest.raises(ValidationError, match="NO_TRADE_PROPOSED"):
        _verdict(
            outcome=RiskOutcome.NO_TRADE_PROPOSED,
            action=Action.WAIT,
            direction=Direction.NONE,
            approved_execution_mode=ExecutionMode.NONE,
            reason_codes=(RiskReasonCode.NO_TRADE_PROPOSED,),
            control_outcomes=(_failed(),),
            decision_ref=DecisionRef.from_proposal(proposal),
        )


def test_approved_execution_mode_must_match_outcome() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    with pytest.raises(ValidationError, match="approved_execution_mode"):
        _verdict(
            outcome=RiskOutcome.APPROVED,
            action=Action.BUY,
            direction=Direction.LONG,
            approved_execution_mode=ExecutionMode.NONE,
            reason_codes=(RiskReasonCode.ALL_CONTROLS_PASSED,),
            control_outcomes=(_passed(),),
            decision_ref=DecisionRef.from_proposal(proposal),
        )


def test_duplicate_reason_codes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _verdict(reason_codes=(RiskReasonCode.NO_TRADE_PROPOSED, RiskReasonCode.NO_TRADE_PROPOSED))


def test_duplicate_control_outcomes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _verdict(control_outcomes=(_passed(), _passed()))


def test_verdict_is_frozen() -> None:
    verdict = _verdict()
    with pytest.raises(ValidationError):
        verdict.action = Action.BUY  # type: ignore[misc]


def test_unexpected_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _verdict(extra_field="nope")


def test_no_live_execution_mode_exists() -> None:
    assert "LIVE" not in ExecutionMode.__members__


def test_verdict_round_trips_through_json() -> None:
    verdict = _verdict()
    assert RiskVerdict.model_validate_json(verdict.model_dump_json()) == verdict
