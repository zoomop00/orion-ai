"""End-to-end tests for `orion.risk.guardian.review` -- the orchestration
function itself, as opposed to the individual controls already covered in
`tests/risk/test_controls.py`.

Confirms the full Mission 3 + Mission 4 contract: what `review()` returns
for each control's failure mode, that AI-authored fields (confidence/
risk_level/rationale) cannot move the outcome, that the guardian only ever
preserves or narrows the Decision Engine's action, that malformed/
mismatched inputs raise rather than launder into a synthetic WAIT, and that
`portfolio` -- a required parameter, never `Optional` -- correctly unlocks
the portfolio-state-dependent controls Mission 3 deferred.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orion.analysis.structure import VolatilityLevel
from orion.decision.engine import decide
from orion.decision.schemas import decision_fingerprint
from orion.provenance import canonical_hash
from orion.reasoning.schemas import Action, Direction, RiskLevel
from orion.risk.guardian import (
    EvidenceLinkageError,
    PortfolioIntegrityError,
    PortfolioLinkageError,
    RiskGuardianError,
    review,
)
from orion.risk.policy import KillSwitchState
from orion.risk.portfolio import expected_equity
from orion.risk.schemas import (
    ControlStatus,
    ExecutionMode,
    RiskControlName,
    RiskOutcome,
    RiskReasonCode,
)
from tests.risk.conftest import (
    BASE_TIME,
    build_evidence,
    build_policy,
    build_portfolio,
    build_position,
    build_proposal,
    build_reasoning,
)

# --- golden paths ---


def test_valid_buy_is_approved() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.APPROVED
    assert verdict.action is Action.BUY
    assert verdict.direction is Direction.LONG
    assert verdict.approved_execution_mode is ExecutionMode.PAPER
    assert verdict.approved is True


def test_valid_sell_is_approved() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.SELL, direction=Direction.SHORT, evidence=evidence)
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.APPROVED
    assert verdict.action is Action.SELL
    assert verdict.direction is Direction.SHORT
    assert verdict.approved_execution_mode is ExecutionMode.PAPER


def test_valid_wait_is_no_trade_proposed() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED
    assert verdict.action is Action.WAIT
    assert verdict.direction is Direction.NONE
    assert verdict.approved_execution_mode is ExecutionMode.NONE
    assert verdict.approved is False


def test_wait_proposal_is_never_upgraded_even_under_a_maximally_permissive_policy() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    policy = build_policy(kill_switch=KillSwitchState.DISENGAGED, min_risk_reward=1.0)
    verdict = review(proposal, evidence, policy=policy, portfolio=build_portfolio(), now=BASE_TIME)
    assert verdict.action is Action.WAIT
    assert verdict.direction is Direction.NONE
    assert verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED


# --- kill switch ---


def test_kill_switch_engaged_vetoes_a_trade() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    verdict = review(proposal, evidence, policy=policy, portfolio=build_portfolio(), now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert verdict.action is Action.WAIT
    assert verdict.direction is Direction.NONE
    assert verdict.approved_execution_mode is ExecutionMode.NONE
    assert RiskReasonCode.KILL_SWITCH_ENGAGED in verdict.reason_codes


def test_kill_switch_disengaged_permits_an_otherwise_eligible_proposal() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(kill_switch=KillSwitchState.DISENGAGED)
    verdict = review(proposal, evidence, policy=policy, portfolio=build_portfolio(), now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.APPROVED


# --- evidence staleness ---


def test_stale_evidence_vetoes_a_trade() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_staleness_bars=1.0)
    later = BASE_TIME + timedelta(hours=5)
    verdict = review(
        proposal, evidence, policy=policy, portfolio=build_portfolio(as_of=later), now=later
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.EVIDENCE_STALE in verdict.reason_codes


def test_future_evidence_timestamp_is_an_auditable_control_veto_not_a_raise() -> None:
    # A future-dated evidence/decision timestamp is a plausible input (clock
    # skew, a replayed record) rather than a structurally malformed one, so
    # per the fail-closed split documented in orion.risk.guardian, it fails
    # closed as a VETOED RiskVerdict -- an ordinary auditable outcome -- not
    # a raised RiskGuardianError.
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    earlier = BASE_TIME - timedelta(hours=1)
    verdict = review(
        proposal,
        evidence,
        policy=build_policy(),
        portfolio=build_portfolio(as_of=earlier),
        now=earlier,
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.EVIDENCE_TIMESTAMP_IN_FUTURE in verdict.reason_codes


# --- decision age ---


def test_expired_decision_vetoes_a_trade() -> None:
    evidence = build_evidence()
    proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, decided_at=BASE_TIME
    )
    policy = build_policy(max_decision_age_bars=1.0)
    later = BASE_TIME + timedelta(hours=5)
    verdict = review(
        proposal, evidence, policy=policy, portfolio=build_portfolio(as_of=later), now=later
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.DECISION_EXPIRED in verdict.reason_codes


def test_future_decision_timestamp_is_an_auditable_control_veto_not_a_raise() -> None:
    evidence = build_evidence()
    proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, decided_at=BASE_TIME
    )
    earlier = BASE_TIME - timedelta(hours=1)
    verdict = review(
        proposal,
        evidence,
        policy=build_policy(),
        portfolio=build_portfolio(as_of=earlier),
        now=earlier,
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.DECISION_TIMESTAMP_IN_FUTURE in verdict.reason_codes


# --- volatility regime ---


def test_unknown_volatility_vetoes_by_default() -> None:
    evidence = build_evidence()
    evidence = evidence.model_copy(
        update={
            "structure": evidence.structure.model_copy(
                update={"volatility": VolatilityLevel.UNKNOWN}
            )
        }
    )
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.VOLATILITY_UNKNOWN in verdict.reason_codes


def test_high_volatility_is_approved_by_default() -> None:
    evidence = build_evidence()
    evidence = evidence.model_copy(
        update={
            "structure": evidence.structure.model_copy(update={"volatility": VolatilityLevel.HIGH})
        }
    )
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.APPROVED


def test_high_volatility_is_vetoed_when_policy_explicitly_blocks_it() -> None:
    evidence = build_evidence()
    evidence = evidence.model_copy(
        update={
            "structure": evidence.structure.model_copy(update={"volatility": VolatilityLevel.HIGH})
        }
    )
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(blocked_volatility_levels=(VolatilityLevel.UNKNOWN, VolatilityLevel.HIGH))
    verdict = review(proposal, evidence, policy=policy, portfolio=build_portfolio(), now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.VOLATILITY_REGIME_BLOCKED in verdict.reason_codes


# --- trade level geometry ---


def test_coherent_buy_levels_are_approved() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.APPROVED


def test_coherent_sell_levels_are_approved() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.SELL,
        direction=Direction.SHORT,
        evidence=evidence,
        stop_loss=2450.0,
        take_profit=2300.0,
    )
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.APPROVED


def test_levels_on_the_wrong_side_of_entry_are_vetoed() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2410.0,  # above entry -- wrong side for a LONG
        take_profit=2430.0,
    )
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.TRADE_LEVELS_INCOHERENT in verdict.reason_codes


def test_degenerate_stop_distance_is_vetoed_never_a_zero_division() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2400.0,  # equals entry -- zero stop distance
        take_profit=2430.0,
    )
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.DEGENERATE_STOP_DISTANCE in verdict.reason_codes
    for control in (
        RiskControlName.RISK_REWARD_MEETS_MINIMUM,
        RiskControlName.POSITION_SIZE_WITHIN_LIMITS,
        RiskControlName.EXPOSURE_WITHIN_LIMIT,
    ):
        outcome = next(o for o in verdict.control_outcomes if o.control is control)
        assert outcome.status is ControlStatus.NOT_APPLICABLE


# --- risk/reward ---


def test_risk_reward_exactly_at_minimum_is_approved() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2450.0,  # risk=50, reward=50, rr=1.0 exactly
    )
    verdict = review(
        proposal,
        evidence,
        policy=build_policy(min_risk_reward=1.0),
        portfolio=build_portfolio(),
        now=BASE_TIME,
    )
    assert verdict.outcome is RiskOutcome.APPROVED


def test_risk_reward_below_minimum_is_vetoed() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2449.0,  # rr = 49/50 = 0.98
    )
    verdict = review(
        proposal,
        evidence,
        policy=build_policy(min_risk_reward=1.0),
        portfolio=build_portfolio(),
        now=BASE_TIME,
    )
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.RISK_REWARD_BELOW_MINIMUM in verdict.reason_codes


def test_risk_reward_is_computed_by_the_guardian_not_self_reported() -> None:
    # ReasoningVerdict carries no risk/reward field at all; the ratio in
    # control_outcomes must be the guardian's own computation from
    # evidence.last_price and the proposed levels.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2390.0,
        take_profit=2430.0,  # risk=10, reward=30, rr=3.0
    )
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME
    )
    rr_outcome = next(
        o
        for o in verdict.control_outcomes
        if o.control is RiskControlName.RISK_REWARD_MEETS_MINIMUM
    )
    assert rr_outcome.status is ControlStatus.PASSED
    assert "rr=3.0000" in rr_outcome.detail


# --- position sizing / exposure (Mission 4) ---


def test_position_size_is_computed_by_the_guardian_not_ai_proposed() -> None:
    # ReasoningVerdict has no size field at all -- the size in
    # control_outcomes must be orion.risk.portfolio.compute_position_size's
    # own deterministic computation from portfolio.equity and the proposed
    # levels.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,  # stop_distance=50
    )
    policy = build_policy(max_risk_per_trade_fraction=0.01)
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.APPROVED
    size_outcome = next(
        o
        for o in verdict.control_outcomes
        if o.control is RiskControlName.POSITION_SIZE_WITHIN_LIMITS
    )
    # risk_amount = 100000*0.01 = 1000; size = 1000/50 = 20.
    assert "size=20" in size_outcome.detail


def test_position_size_exceeding_cash_vetoes_the_trade() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    policy = build_policy(max_risk_per_trade_fraction=0.05)  # implies 2.4x equity notional
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.POSITION_SIZE_EXCEEDS_AVAILABLE_CASH in verdict.reason_codes


def test_exposure_above_limit_vetoes_the_trade() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    policy = build_policy(max_exposure_fraction=0.1)
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.EXPOSURE_LIMIT_EXCEEDED in verdict.reason_codes


# --- single-position-at-a-time (Mission 4, architecture decision B2) ---


def test_existing_open_position_vetoes_a_new_trade() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    portfolio = build_portfolio(open_position=build_position())
    verdict = review(proposal, evidence, policy=build_policy(), portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.POSITION_ALREADY_OPEN in verdict.reason_codes


def test_no_open_position_permits_a_trade() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    portfolio = build_portfolio(open_position=None)
    verdict = review(proposal, evidence, policy=build_policy(), portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.APPROVED


# --- daily / weekly loss limits, max drawdown (Mission 4) ---


def test_daily_loss_limit_vetoes_further_trades() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_daily_loss_fraction=0.03)
    portfolio = build_portfolio(
        cash=95_000.0, starting_balance=100_000.0, daily_opening_equity=100_000.0
    )
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.DAILY_LOSS_LIMIT_BREACHED in verdict.reason_codes


def test_weekly_loss_limit_vetoes_further_trades() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_weekly_loss_fraction=0.08)
    portfolio = build_portfolio(
        cash=90_000.0, starting_balance=100_000.0, weekly_opening_equity=100_000.0
    )
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.WEEKLY_LOSS_LIMIT_BREACHED in verdict.reason_codes


def test_max_drawdown_vetoes_further_trades() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_drawdown_fraction=0.15)
    portfolio = build_portfolio(cash=80_000.0, starting_balance=100_000.0, equity_peak=100_000.0)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.DRAWDOWN_LIMIT_BREACHED in verdict.reason_codes


def test_portfolio_limits_correctly_permit_a_healthy_account() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy()
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.APPROVED


# --- portfolio staleness / integrity (Mission 4, architecture decision B7) ---


def test_stale_portfolio_state_vetoes_a_trade_not_a_raise() -> None:
    # Ordinary staleness is a control failure -- an auditable VETOED
    # verdict, never laundered as "flat, no exposure" and never a raise.
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_portfolio_staleness_bars=1.0)
    portfolio = build_portfolio(as_of=BASE_TIME)
    later = BASE_TIME + timedelta(hours=5)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=later)
    assert verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.PORTFOLIO_STALE in verdict.reason_codes


def test_portfolio_symbol_mismatch_raises_portfolio_linkage_error() -> None:
    evidence = build_evidence()  # symbol="XAUUSD"
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    mismatched_portfolio = build_portfolio(symbol="EURUSD")
    with pytest.raises(PortfolioLinkageError):
        review(
            proposal, evidence, policy=build_policy(), portfolio=mismatched_portfolio, now=BASE_TIME
        )


# --- adversarial: fabricated/inconsistent equity must fail closed (H1) ---


def test_review_rejects_fabricated_equity_on_a_flat_account() -> None:
    # No open position -- equity must equal cash exactly. A portfolio
    # claiming far more equity than its cash, approved by no computation
    # at all, must never reach a portfolio-dependent control.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    fabricated = build_portfolio(cash=100.0, equity=1_000_000.0, starting_balance=100.0)
    with pytest.raises(PortfolioIntegrityError):
        review(proposal, evidence, policy=build_policy(), portfolio=fabricated, now=BASE_TIME)


def test_review_rejects_equity_inconsistent_with_open_position_unrealized_pnl() -> None:
    # A real open position, but equity does not reflect its unrealized P&L
    # at evidence.last_price -- e.g. marked at a stale/wrong price, or
    # hand-edited after the fact.
    evidence = build_evidence(last_price=2450.0)
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    position = build_position(direction=Direction.LONG, entry_price=2400.0, quantity=1.0)
    # True unrealized P&L at 2450.0 is +50; claim equity as if it were flat.
    inconsistent = build_portfolio(cash=100_000.0, equity=100_000.0, open_position=position)
    with pytest.raises(PortfolioIntegrityError):
        review(proposal, evidence, policy=build_policy(), portfolio=inconsistent, now=BASE_TIME)


def test_review_rejects_a_portfolio_marked_to_a_different_price_than_this_evidence() -> None:
    # portfolio.equity is internally self-consistent with *some* price, but
    # not the evidence.last_price this review actually concerns -- a stale
    # mark_to_market call, not raw fabrication, must still fail closed.
    position = build_position(direction=Direction.LONG, entry_price=2400.0, quantity=1.0)
    stale_equity = expected_equity(100_000.0, position, 2410.0)  # marked at 2410, not 2450
    stale_portfolio = build_portfolio(cash=100_000.0, equity=stale_equity, open_position=position)
    evidence = build_evidence(last_price=2450.0)
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    with pytest.raises(PortfolioIntegrityError):
        review(proposal, evidence, policy=build_policy(), portfolio=stale_portfolio, now=BASE_TIME)


def test_review_accepts_equity_correctly_marked_to_this_evidence() -> None:
    # Positive control: equity computed via the real expected_equity
    # formula at the *correct* price must pass the integrity check (and
    # this WAIT proposal has nothing else to veto it on).
    position = build_position(direction=Direction.LONG, entry_price=2400.0, quantity=1.0)
    correct_equity = expected_equity(100_000.0, position, 2450.0)
    consistent_portfolio = build_portfolio(
        cash=100_000.0, equity=correct_equity, open_position=position
    )
    evidence = build_evidence(last_price=2450.0)
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    verdict = review(
        proposal, evidence, policy=build_policy(), portfolio=consistent_portfolio, now=BASE_TIME
    )
    assert verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED


# --- AI-authored fields must never move the outcome ---


def test_confidence_cannot_change_the_outcome() -> None:
    evidence = build_evidence(last_price=2400.0)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    portfolio = build_portfolio()
    low = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, confidence=0.01
    )
    high = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, confidence=0.99
    )
    verdict_low = review(low, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    verdict_high = review(high, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict_low.outcome is verdict_high.outcome is RiskOutcome.VETOED


def test_risk_level_cannot_change_the_outcome() -> None:
    evidence = build_evidence(last_price=2400.0)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    portfolio = build_portfolio()
    low = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, risk_level=RiskLevel.LOW
    )
    high = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, risk_level=RiskLevel.HIGH
    )
    verdict_low = review(low, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    verdict_high = review(high, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict_low.outcome is verdict_high.outcome is RiskOutcome.VETOED


def test_rationale_cannot_change_the_outcome() -> None:
    evidence = build_evidence(last_price=2400.0)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    portfolio = build_portfolio()
    reasoning_a = build_reasoning(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        rationale="Strong bullish confluence across every timeframe.",
    )
    reasoning_b = build_reasoning(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        rationale="Weak, low-conviction setup, barely worth taking.",
    )
    proposal_a = decide(evidence, reasoning_a, analysis_id="a", now=BASE_TIME)
    proposal_b = decide(evidence, reasoning_b, analysis_id="b", now=BASE_TIME)
    verdict_a = review(proposal_a, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    verdict_b = review(proposal_b, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict_a.outcome is verdict_b.outcome is RiskOutcome.VETOED


# --- evidence / proposal linkage integrity ---


def test_evidence_proposal_mismatch_raises_evidence_linkage_error() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    different_evidence = build_evidence(last_price=1234.5)
    with pytest.raises(EvidenceLinkageError):
        review(
            proposal,
            different_evidence,
            policy=build_policy(),
            portfolio=build_portfolio(),
            now=BASE_TIME,
        )


# --- determinism ---


def test_repeated_calls_with_identical_inputs_produce_an_identical_verdict() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy()
    portfolio = build_portfolio()
    verdict_1 = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    verdict_2 = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict_1 == verdict_2


# --- provenance ---


def test_verdict_provenance_references_match_the_reviewed_proposal_and_policy() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy()
    portfolio = build_portfolio()
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.decision_ref.content_hash == decision_fingerprint(proposal)
    assert verdict.policy_ref.content_hash == canonical_hash(policy)
    assert verdict.portfolio_ref.content_hash == canonical_hash(portfolio)
    assert verdict.analysis_id == proposal.analysis_id


# --- no live execution capability ---


def test_no_live_execution_mode_exists() -> None:
    assert "LIVE" not in ExecutionMode.__members__


def test_approved_execution_mode_is_always_paper_or_none() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    for kill_switch in (KillSwitchState.ENGAGED, KillSwitchState.DISENGAGED):
        verdict = review(
            proposal,
            evidence,
            policy=build_policy(kill_switch=kill_switch),
            portfolio=build_portfolio(),
            now=BASE_TIME,
        )
        assert verdict.approved_execution_mode in (ExecutionMode.PAPER, ExecutionMode.NONE)


# --- hard integrity/type failures raise, never launder into a synthetic WAIT ---


def test_review_rejects_a_non_proposed_decision() -> None:
    with pytest.raises(RiskGuardianError):
        review(
            "not-a-proposal",  # type: ignore[arg-type]
            build_evidence(),
            policy=build_policy(),
            portfolio=build_portfolio(),
            now=BASE_TIME,
        )


def test_review_rejects_a_non_portfolio_state() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    with pytest.raises(RiskGuardianError):
        review(
            proposal,
            evidence,
            policy=build_policy(),
            portfolio="not-a-portfolio",  # type: ignore[arg-type]
            now=BASE_TIME,
        )


def test_review_rejects_a_naive_now() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    with pytest.raises(RiskGuardianError):
        review(
            proposal,
            evidence,
            policy=build_policy(),
            portfolio=build_portfolio(),
            now=BASE_TIME.replace(tzinfo=None),
        )


def test_review_rejects_a_naive_portfolio_as_of() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    # model_copy skips validation, matching this package's established
    # defense-in-depth pattern for testing a reconstructed/relaxed record.
    naive_portfolio = build_portfolio().model_copy(update={"as_of": BASE_TIME.replace(tzinfo=None)})
    with pytest.raises(RiskGuardianError):
        review(proposal, evidence, policy=build_policy(), portfolio=naive_portfolio, now=BASE_TIME)


def test_review_rejects_a_blank_analysis_id() -> None:
    # A well-formed ProposedDecision cannot carry a whitespace-only
    # analysis_id through decide() (it enforces the same check), so this
    # reconstructs one the way a relaxed/older journal replay might --
    # model_copy skips validation, matching this package's established
    # defense-in-depth pattern for testing reconstructed records.
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    blank = proposal.model_copy(update={"analysis_id": "   "})
    with pytest.raises(RiskGuardianError):
        review(blank, evidence, policy=build_policy(), portfolio=build_portfolio(), now=BASE_TIME)


def test_review_rejects_a_non_positive_last_price() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    bad_evidence = evidence.model_copy(update={"last_price": 0.0})
    with pytest.raises(RiskGuardianError):
        review(
            proposal,
            bad_evidence,
            policy=build_policy(),
            portfolio=build_portfolio(),
            now=BASE_TIME,
        )


def test_review_rejects_evidence_with_no_known_bar_interval() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    unknown_tf_evidence = evidence.model_copy(update={"timeframe": "weekly"})
    with pytest.raises(RiskGuardianError):
        review(
            proposal,
            unknown_tf_evidence,
            policy=build_policy(),
            portfolio=build_portfolio(),
            now=BASE_TIME,
        )
