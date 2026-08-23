from __future__ import annotations

from datetime import timedelta

from orion.analysis.structure import TrendDirection, VolatilityLevel
from orion.data.models import Timeframe
from orion.data.validation import TIMEFRAME_INTERVAL
from orion.reasoning.schemas import Action, Direction
from orion.risk.controls import (
    CONTROLS,
    classify_trade_levels,
    context_codes,
    daily_loss_limit_not_breached,
    decision_is_current,
    evaluate_all,
    evidence_is_fresh,
    exposure_within_limit,
    kill_switch_disengaged,
    max_drawdown_not_breached,
    no_conflicting_open_position,
    portfolio_state_is_fresh,
    position_size_within_limits,
    proposal_was_not_upgraded,
    risk_reward_meets_minimum,
    trade_levels_are_coherent_with_price,
    volatility_regime_permitted,
    weekly_loss_limit_not_breached,
)
from orion.risk.policy import KillSwitchState
from orion.risk.schemas import ControlStatus, RiskControlName, RiskReasonCode
from tests.risk.conftest import (
    BASE_TIME,
    build_evidence,
    build_policy,
    build_portfolio,
    build_position,
    build_proposal,
)

# --- registry coverage ---


def test_registry_covers_every_control_name_exactly_once() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    outcomes = evaluate_all(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    names = [o.control for o in outcomes]
    assert len(names) == len(set(names))
    assert set(names) == set(RiskControlName)


def test_evaluate_all_never_short_circuits() -> None:
    # A known trend keeps the Decision Engine's own proposal at BUY (an
    # UNKNOWN trend would downgrade it to WAIT before the guardian ever sees
    # a trade, trivializing every trade-only control to NOT_APPLICABLE and
    # defeating this test's purpose). kill_switch_disengaged and
    # risk_reward_meets_minimum are made to fail independently of each
    # other -- proving neither short-circuits the other or anything else.
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED, min_risk_reward=10.0)
    outcomes = evaluate_all(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert len(outcomes) == len(CONTROLS)
    assert sum(1 for o in outcomes if o.status is ControlStatus.FAILED) >= 2


# --- classify_trade_levels ---


def test_classify_trade_levels_valid_long() -> None:
    assert classify_trade_levels(Direction.LONG, 2400.0, 2385.0, 2430.0) is None


def test_classify_trade_levels_valid_short() -> None:
    assert classify_trade_levels(Direction.SHORT, 2400.0, 2415.0, 2370.0) is None


def test_classify_trade_levels_missing() -> None:
    assert (
        classify_trade_levels(Direction.LONG, 2400.0, None, 2430.0)
        is RiskReasonCode.TRADE_LEVELS_MISSING
    )


def test_classify_trade_levels_degenerate_stop() -> None:
    assert (
        classify_trade_levels(Direction.LONG, 2400.0, 2400.0, 2430.0)
        is RiskReasonCode.DEGENERATE_STOP_DISTANCE
    )


def test_classify_trade_levels_long_wrong_side() -> None:
    # stop above entry — wrong side for a LONG.
    assert (
        classify_trade_levels(Direction.LONG, 2400.0, 2410.0, 2430.0)
        is RiskReasonCode.TRADE_LEVELS_INCOHERENT
    )


def test_classify_trade_levels_short_wrong_side() -> None:
    assert (
        classify_trade_levels(Direction.SHORT, 2400.0, 2390.0, 2370.0)
        is RiskReasonCode.TRADE_LEVELS_INCOHERENT
    )


# --- proposal_was_not_upgraded ---


def test_proposal_was_not_upgraded_passes_for_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = proposal_was_not_upgraded(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_proposal_was_not_upgraded_passes_for_a_normal_trade() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    outcome = proposal_was_not_upgraded(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


# --- kill_switch_disengaged ---


def test_kill_switch_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    outcome = kill_switch_disengaged(
        proposal, build_evidence(), policy, build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_kill_switch_engaged_fails_a_trade_proposal() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    outcome = kill_switch_disengaged(
        proposal, build_evidence(), policy, build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.KILL_SWITCH_ENGAGED


def test_kill_switch_disengaged_passes_a_trade_proposal() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(kill_switch=KillSwitchState.DISENGAGED)
    outcome = kill_switch_disengaged(
        proposal, build_evidence(), policy, build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


# --- evidence_is_fresh ---


def test_evidence_freshness_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = evidence_is_fresh(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_evidence_exactly_at_the_staleness_boundary_passes() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_staleness_bars=3.0)
    boundary = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 3.0
    outcome = evidence_is_fresh(proposal, evidence, policy, build_portfolio(), boundary)
    assert outcome.status is ControlStatus.PASSED


def test_evidence_just_over_the_staleness_boundary_fails() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_staleness_bars=3.0)
    just_over = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 3.0 + timedelta(microseconds=1)
    outcome = evidence_is_fresh(proposal, evidence, policy, build_portfolio(), just_over)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.EVIDENCE_STALE


def test_evidence_timestamp_in_the_future_fails() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    outcome = evidence_is_fresh(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME - timedelta(hours=1)
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.EVIDENCE_TIMESTAMP_IN_FUTURE


def test_evidence_staleness_is_timeframe_aware() -> None:
    # The same absolute age passes on H4 but fails on M15, proving the
    # allowance scales with the bar interval rather than being a fixed
    # wall-clock duration.
    m15_evidence = build_evidence()
    m15_evidence = m15_evidence.model_copy(update={"timeframe": Timeframe.M15})
    h4_evidence = m15_evidence.model_copy(update={"timeframe": Timeframe.H4})
    later = BASE_TIME + timedelta(hours=2)
    policy = build_policy(max_staleness_bars=3.0)

    m15_proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=m15_evidence
    )
    h4_proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=h4_evidence)

    assert (
        evidence_is_fresh(m15_proposal, m15_evidence, policy, build_portfolio(), later).status
        is ControlStatus.FAILED
    )
    assert (
        evidence_is_fresh(h4_proposal, h4_evidence, policy, build_portfolio(), later).status
        is ControlStatus.PASSED
    )


# --- decision_is_current ---


def test_decision_age_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = decision_is_current(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_decision_within_max_age_passes() -> None:
    evidence = build_evidence()
    proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, decided_at=BASE_TIME
    )
    policy = build_policy(max_decision_age_bars=1.0)
    within = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 1.0
    outcome = decision_is_current(proposal, evidence, policy, build_portfolio(), within)
    assert outcome.status is ControlStatus.PASSED


def test_decision_beyond_max_age_fails() -> None:
    evidence = build_evidence()
    proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, decided_at=BASE_TIME
    )
    policy = build_policy(max_decision_age_bars=1.0)
    beyond = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 1.0 + timedelta(microseconds=1)
    outcome = decision_is_current(proposal, evidence, policy, build_portfolio(), beyond)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.DECISION_EXPIRED


def test_decision_timestamp_in_the_future_fails() -> None:
    evidence = build_evidence()
    proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, decided_at=BASE_TIME
    )
    outcome = decision_is_current(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME - timedelta(hours=1)
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.DECISION_TIMESTAMP_IN_FUTURE


def test_stale_evidence_and_expired_decision_are_independent() -> None:
    # A fresh proposal over stale evidence fails only evidence freshness.
    evidence = build_evidence()
    proposal = build_proposal(
        action=Action.BUY, direction=Direction.LONG, evidence=evidence, decided_at=BASE_TIME
    )
    policy = build_policy(max_staleness_bars=1.0, max_decision_age_bars=100.0)
    later = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 2.0
    portfolio = build_portfolio()
    assert (
        evidence_is_fresh(proposal, evidence, policy, portfolio, later).status
        is ControlStatus.FAILED
    )
    assert (
        decision_is_current(proposal, evidence, policy, portfolio, later).status
        is ControlStatus.PASSED
    )


# --- portfolio_state_is_fresh ---


def test_portfolio_freshness_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = portfolio_state_is_fresh(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_portfolio_exactly_at_the_staleness_boundary_passes() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_portfolio_staleness_bars=3.0)
    portfolio = build_portfolio(as_of=BASE_TIME)
    boundary = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 3.0
    outcome = portfolio_state_is_fresh(proposal, evidence, policy, portfolio, boundary)
    assert outcome.status is ControlStatus.PASSED


def test_portfolio_just_over_the_staleness_boundary_fails() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(max_portfolio_staleness_bars=3.0)
    portfolio = build_portfolio(as_of=BASE_TIME)
    just_over = BASE_TIME + TIMEFRAME_INTERVAL[evidence.timeframe] * 3.0 + timedelta(microseconds=1)
    outcome = portfolio_state_is_fresh(proposal, evidence, policy, portfolio, just_over)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.PORTFOLIO_STALE


def test_portfolio_timestamp_in_the_future_fails() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    portfolio = build_portfolio(as_of=BASE_TIME)
    outcome = portfolio_state_is_fresh(
        proposal, evidence, build_policy(), portfolio, BASE_TIME - timedelta(hours=1)
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.PORTFOLIO_TIMESTAMP_IN_FUTURE


# --- no_conflicting_open_position ---


def test_no_conflicting_position_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    portfolio = build_portfolio(open_position=build_position())
    outcome = no_conflicting_open_position(
        proposal, build_evidence(), build_policy(), portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_no_open_position_passes() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    portfolio = build_portfolio(open_position=None)
    outcome = no_conflicting_open_position(
        proposal, build_evidence(), build_policy(), portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_existing_open_position_fails() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    portfolio = build_portfolio(open_position=build_position())
    outcome = no_conflicting_open_position(
        proposal, build_evidence(), build_policy(), portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.POSITION_ALREADY_OPEN


# --- volatility_regime_permitted ---


def test_volatility_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = volatility_regime_permitted(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_unknown_volatility_is_blocked_by_default() -> None:
    evidence = build_evidence()
    evidence = evidence.model_copy(
        update={
            "structure": evidence.structure.model_copy(
                update={"volatility": VolatilityLevel.UNKNOWN}
            )
        }
    )
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy()  # default: only UNKNOWN blocked
    outcome = volatility_regime_permitted(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.VOLATILITY_UNKNOWN


def test_high_volatility_is_permitted_by_default() -> None:
    evidence = build_evidence()
    evidence = evidence.model_copy(
        update={
            "structure": evidence.structure.model_copy(update={"volatility": VolatilityLevel.HIGH})
        }
    )
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy()  # default: HIGH not blocked
    outcome = volatility_regime_permitted(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert outcome.status is ControlStatus.PASSED


def test_high_volatility_is_vetoed_when_policy_explicitly_blocks_it() -> None:
    evidence = build_evidence()
    evidence = evidence.model_copy(
        update={
            "structure": evidence.structure.model_copy(update={"volatility": VolatilityLevel.HIGH})
        }
    )
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    policy = build_policy(blocked_volatility_levels=(VolatilityLevel.UNKNOWN, VolatilityLevel.HIGH))
    outcome = volatility_regime_permitted(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.VOLATILITY_REGIME_BLOCKED


def test_low_and_medium_volatility_always_pass() -> None:
    for level in (VolatilityLevel.LOW, VolatilityLevel.MEDIUM):
        evidence = build_evidence()
        evidence = evidence.model_copy(
            update={"structure": evidence.structure.model_copy(update={"volatility": level})}
        )
        proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
        policy = build_policy(
            blocked_volatility_levels=(VolatilityLevel.UNKNOWN, VolatilityLevel.HIGH)
        )
        outcome = volatility_regime_permitted(
            proposal, evidence, policy, build_portfolio(), BASE_TIME
        )
        assert outcome.status is ControlStatus.PASSED


# --- trade_levels_are_coherent_with_price ---


def test_trade_levels_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = trade_levels_are_coherent_with_price(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_valid_long_geometry_passes() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2385.0,
        take_profit=2430.0,
    )
    outcome = trade_levels_are_coherent_with_price(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_valid_short_geometry_passes() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.SELL,
        direction=Direction.SHORT,
        evidence=evidence,
        stop_loss=2415.0,
        take_profit=2370.0,
    )
    outcome = trade_levels_are_coherent_with_price(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_invalid_long_geometry_fails() -> None:
    evidence = build_evidence(last_price=2400.0)
    # stop_loss above entry — wrong side for a LONG.
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2410.0,
        take_profit=2430.0,
    )
    outcome = trade_levels_are_coherent_with_price(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.TRADE_LEVELS_INCOHERENT


def test_invalid_short_geometry_fails() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.SELL,
        direction=Direction.SHORT,
        evidence=evidence,
        stop_loss=2390.0,
        take_profit=2370.0,
    )
    outcome = trade_levels_are_coherent_with_price(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.TRADE_LEVELS_INCOHERENT


def test_zero_stop_distance_fails() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2400.0,
        take_profit=2430.0,
    )
    outcome = trade_levels_are_coherent_with_price(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.DEGENERATE_STOP_DISTANCE


# --- risk_reward_meets_minimum ---


def test_risk_reward_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = risk_reward_meets_minimum(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_risk_reward_not_applicable_when_levels_incoherent() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2400.0,
        take_profit=2430.0,
    )
    outcome = risk_reward_meets_minimum(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_risk_reward_exactly_at_minimum_passes() -> None:
    # entry=2400, stop=2390 (risk=10), target=2410 (reward=10) -> rr=1.0 exactly.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2390.0,
        take_profit=2410.0,
    )
    policy = build_policy(min_risk_reward=1.0)
    outcome = risk_reward_meets_minimum(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert outcome.status is ControlStatus.PASSED


def test_risk_reward_just_below_minimum_fails() -> None:
    # rr = 9.99 / 10 = 0.999 < 1.0.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2390.0,
        take_profit=2409.99,
    )
    policy = build_policy(min_risk_reward=1.0)
    outcome = risk_reward_meets_minimum(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.RISK_REWARD_BELOW_MINIMUM


def test_risk_reward_above_minimum_passes() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2390.0,
        take_profit=2430.0,  # rr = 30/10 = 3.0
    )
    policy = build_policy(min_risk_reward=1.0)
    outcome = risk_reward_meets_minimum(proposal, evidence, policy, build_portfolio(), BASE_TIME)
    assert outcome.status is ControlStatus.PASSED


# --- position_size_within_limits ---


def test_position_size_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = position_size_within_limits(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_position_size_not_applicable_when_levels_incoherent() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2400.0,
        take_profit=2430.0,
    )
    outcome = position_size_within_limits(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_position_size_within_cash_passes() -> None:
    # entry=2400, stop=2350 (distance=50), equity=100000, risk_fraction=0.01
    # -> risk_amount=1000, size=20, notional=48000 <= cash=100000.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    policy = build_policy(max_risk_per_trade_fraction=0.01)
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    outcome = position_size_within_limits(proposal, evidence, policy, portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.PASSED
    assert "size=20" in outcome.detail


def test_position_size_exceeding_cash_fails() -> None:
    # Same geometry, but a 5% risk fraction against a 50-point (~2%) stop
    # implies notional = risk_fraction * entry/stop_distance * equity =
    # 0.05 * 48 * equity = 2.4x equity -- more than any cash-covered
    # (unleveraged) account can afford, regardless of its size.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    policy = build_policy(max_risk_per_trade_fraction=0.05)
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    outcome = position_size_within_limits(proposal, evidence, policy, portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.POSITION_SIZE_EXCEEDS_AVAILABLE_CASH


def test_position_size_with_zero_equity_fails() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    portfolio = build_portfolio(
        cash=0.0, equity=0.0, starting_balance=100_000.0, equity_peak=100_000.0
    )
    outcome = position_size_within_limits(proposal, evidence, build_policy(), portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.POSITION_SIZE_INVALID


# --- exposure_within_limit ---


def test_exposure_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = exposure_within_limit(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_exposure_not_applicable_when_levels_incoherent() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2400.0,
        take_profit=2430.0,
    )
    outcome = exposure_within_limit(
        proposal, evidence, build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_exposure_within_default_limit_passes() -> None:
    # notional=48000 / equity=100000 = 0.48 <= default max_exposure_fraction=1.0.
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    outcome = exposure_within_limit(proposal, evidence, build_policy(), portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.PASSED


def test_exposure_above_explicit_limit_fails() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    policy = build_policy(max_exposure_fraction=0.1)  # 0.48 > 0.1
    portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    outcome = exposure_within_limit(proposal, evidence, policy, portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.EXPOSURE_LIMIT_EXCEEDED


def test_exposure_with_non_positive_equity_fails() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=evidence,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    portfolio = build_portfolio(
        cash=0.0, equity=0.0, starting_balance=100_000.0, equity_peak=100_000.0
    )
    outcome = exposure_within_limit(proposal, evidence, build_policy(), portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.EXPOSURE_LIMIT_EXCEEDED


# --- daily_loss_limit_not_breached / weekly_loss_limit_not_breached ---


def test_daily_loss_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = daily_loss_limit_not_breached(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_daily_loss_within_limit_passes() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_daily_loss_fraction=0.03)
    # 1% down from the day's opening equity -- within a 3% limit.
    portfolio = build_portfolio(
        cash=99_000.0, starting_balance=100_000.0, daily_opening_equity=100_000.0
    )
    outcome = daily_loss_limit_not_breached(
        proposal, build_evidence(), policy, portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_daily_loss_beyond_limit_fails() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_daily_loss_fraction=0.03)
    # 5% down from the day's opening equity -- exceeds a 3% limit.
    portfolio = build_portfolio(
        cash=95_000.0, starting_balance=100_000.0, daily_opening_equity=100_000.0
    )
    outcome = daily_loss_limit_not_breached(
        proposal, build_evidence(), policy, portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT_BREACHED


def test_daily_loss_exactly_at_limit_passes() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_daily_loss_fraction=0.03)
    portfolio = build_portfolio(
        cash=97_000.0, starting_balance=100_000.0, daily_opening_equity=100_000.0
    )
    outcome = daily_loss_limit_not_breached(
        proposal, build_evidence(), policy, portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_weekly_loss_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = weekly_loss_limit_not_breached(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_weekly_loss_within_limit_passes() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_weekly_loss_fraction=0.08)
    portfolio = build_portfolio(
        cash=95_000.0, starting_balance=100_000.0, weekly_opening_equity=100_000.0
    )
    outcome = weekly_loss_limit_not_breached(
        proposal, build_evidence(), policy, portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.PASSED


def test_weekly_loss_beyond_limit_fails() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_weekly_loss_fraction=0.08)
    portfolio = build_portfolio(
        cash=90_000.0, starting_balance=100_000.0, weekly_opening_equity=100_000.0
    )
    outcome = weekly_loss_limit_not_breached(
        proposal, build_evidence(), policy, portfolio, BASE_TIME
    )
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.WEEKLY_LOSS_LIMIT_BREACHED


def test_daily_and_weekly_loss_are_independent() -> None:
    # Daily baseline breached (5% down from 100,000), weekly baseline not
    # (~2% down from a 97,000 baseline set closer to the current equity) --
    # same current equity (95,000), proving the two controls read distinct
    # baseline fields rather than sharing one.
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_daily_loss_fraction=0.03, max_weekly_loss_fraction=0.08)
    portfolio = build_portfolio(
        cash=95_000.0,
        starting_balance=100_000.0,
        daily_opening_equity=100_000.0,
        weekly_opening_equity=97_000.0,
    )
    assert (
        daily_loss_limit_not_breached(
            proposal, build_evidence(), policy, portfolio, BASE_TIME
        ).status
        is ControlStatus.FAILED
    )
    assert (
        weekly_loss_limit_not_breached(
            proposal, build_evidence(), policy, portfolio, BASE_TIME
        ).status
        is ControlStatus.PASSED
    )


# --- max_drawdown_not_breached ---


def test_drawdown_not_applicable_on_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    outcome = max_drawdown_not_breached(
        proposal, build_evidence(), build_policy(), build_portfolio(), BASE_TIME
    )
    assert outcome.status is ControlStatus.NOT_APPLICABLE


def test_drawdown_within_limit_passes() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_drawdown_fraction=0.15)
    portfolio = build_portfolio(cash=90_000.0, starting_balance=100_000.0, equity_peak=100_000.0)
    outcome = max_drawdown_not_breached(proposal, build_evidence(), policy, portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.PASSED


def test_drawdown_beyond_limit_fails() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_drawdown_fraction=0.15)
    portfolio = build_portfolio(cash=80_000.0, starting_balance=100_000.0, equity_peak=100_000.0)
    outcome = max_drawdown_not_breached(proposal, build_evidence(), policy, portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.FAILED
    assert outcome.reason_code is RiskReasonCode.DRAWDOWN_LIMIT_BREACHED


def test_drawdown_exactly_at_limit_passes() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    policy = build_policy(max_drawdown_fraction=0.15)
    portfolio = build_portfolio(cash=85_000.0, starting_balance=100_000.0, equity_peak=100_000.0)
    outcome = max_drawdown_not_breached(proposal, build_evidence(), policy, portfolio, BASE_TIME)
    assert outcome.status is ControlStatus.PASSED


# --- context_codes ---


def test_context_codes_flags_ai_proposed_wait() -> None:
    proposal = build_proposal(action=Action.WAIT)
    assert RiskReasonCode.AI_PROPOSED_WAIT in context_codes(proposal)


def test_context_codes_flags_engine_downgrade() -> None:
    # Evidence with unknown trend downgrades a BUY proposal to WAIT inside decide().
    evidence = build_evidence(trend=TrendDirection.UNKNOWN)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    assert proposal.action is Action.WAIT
    assert RiskReasonCode.DECISION_ENGINE_DOWNGRADED_TO_WAIT in context_codes(proposal)


def test_context_codes_empty_for_a_confident_trade_proposal() -> None:
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG)
    assert context_codes(proposal) == ()
