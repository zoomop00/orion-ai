"""Tests for `orion.paper.execution` -- deterministic paper order/fill
simulation and last-price-only stop/take-profit resolution.

Covers: correctness (fill price direction, fee, resulting portfolio),
determinism, cash conservation across a full open/close round trip, BUY/SELL
P&L sign correctness, the "only an APPROVED verdict is executable" boundary,
and every fail-closed raise path.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason, PaperExecutionError
from orion.reasoning.schemas import Action, Direction
from orion.risk.guardian import review
from orion.risk.policy import KillSwitchState
from orion.risk.schemas import RiskOutcome, RiskReasonCode, risk_verdict_fingerprint
from tests.paper.conftest import (
    BASE_TIME,
    build_approved_trade,
    build_evidence,
    build_policy,
    build_portfolio,
    build_position,
    build_proposal,
)

ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)


# --- execute(): opening a position ---


def test_execute_opens_a_long_position_with_a_worse_than_reference_fill_price() -> None:
    trade = build_approved_trade(action=Action.BUY, direction=Direction.LONG, entry_price=2400.0)
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    # A LONG fills higher than the reference price -- worse for the buyer.
    assert result.fill.fill_price > trade.evidence.last_price
    assert result.fill.direction is Direction.LONG
    assert result.fill.fee > 0
    assert result.portfolio.open_position is not None
    assert result.portfolio.open_position.entry_price == result.fill.fill_price
    assert result.portfolio.cash < trade.portfolio.cash


def test_execute_opens_a_short_position_with_a_worse_than_reference_fill_price() -> None:
    trade = build_approved_trade(
        action=Action.SELL,
        direction=Direction.SHORT,
        entry_price=2400.0,
        stop_loss=2450.0,
        take_profit=2300.0,
    )
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    # A SHORT fills lower than the reference price -- worse for the seller.
    assert result.fill.fill_price < trade.evidence.last_price
    assert result.fill.direction is Direction.SHORT


def test_execute_records_the_risk_verdict_fingerprint_on_the_fill_and_position() -> None:
    trade = build_approved_trade()
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    expected = risk_verdict_fingerprint(trade.verdict)
    assert result.fill.risk_verdict_fingerprint == expected
    assert result.portfolio.open_position is not None
    assert result.portfolio.open_position.opened_by_verdict_fingerprint == expected


def test_execute_with_zero_cost_policy_fills_at_the_reference_price() -> None:
    trade = build_approved_trade(entry_price=2400.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    assert result.fill.fill_price == pytest.approx(2400.0)
    assert result.fill.fee == 0.0


def test_execute_is_deterministic() -> None:
    trade = build_approved_trade()
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    result_1 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    result_2 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    assert result_1.fill == result_2.fill
    assert result_1.portfolio == result_2.portfolio


# --- execute(): fail-closed raise paths ---


def test_execute_rejects_a_vetoed_verdict() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    vetoing_policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    portfolio = build_portfolio()
    verdict = review(proposal, evidence, policy=vetoing_policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    with pytest.raises(PaperExecutionError, match="not APPROVED"):
        execute(
            proposal,
            verdict,
            evidence,
            portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_no_trade_proposed_verdict() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    portfolio = build_portfolio()
    verdict = review(proposal, evidence, policy=build_policy(), portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED
    with pytest.raises(PaperExecutionError, match="not APPROVED"):
        execute(
            proposal,
            verdict,
            evidence,
            portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_proposal_verdict_mismatch() -> None:
    trade = build_approved_trade()
    # A different, independently-approved verdict for an unrelated proposal.
    other_evidence = build_evidence(last_price=1234.5)
    other_proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=other_evidence,
        analysis_id="other-analysis",
    )
    other_portfolio = build_portfolio()
    other_verdict = review(
        other_proposal,
        other_evidence,
        policy=build_policy(),
        portfolio=other_portfolio,
        now=BASE_TIME,
    )
    with pytest.raises(PaperExecutionError, match="does not match proposal"):
        execute(
            trade.proposal,
            other_verdict,
            trade.evidence,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_evidence_not_linked_to_the_approved_proposal() -> None:
    # H2 regression: review/approve using Evidence A, then supply a
    # different Evidence B (same symbol, materially different last_price)
    # to execute() -- a fresh/different Evidence must never be executable
    # against an old approval, even though nothing about B is individually
    # invalid and its symbol still matches.
    trade = build_approved_trade(entry_price=2400.0)  # reviewed/approved at 2400.0 (Evidence A)
    evidence_b = build_evidence(last_price=2490.0)  # same symbol, different price (Evidence B)
    assert evidence_b.symbol == trade.evidence.symbol
    assert evidence_b.last_price != trade.evidence.last_price

    with pytest.raises(PaperExecutionError, match="does not match the Evidence"):
        execute(
            trade.proposal,
            trade.verdict,
            evidence_b,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )
    # No account mutation occurred: raising means execute() returned
    # nothing and the (frozen, unreachable-to-mutate-in-place) portfolio
    # passed in is untouched.
    assert trade.portfolio.open_position is None
    assert trade.portfolio.cash == trade.portfolio.starting_balance


def test_execute_rejects_a_portfolio_symbol_mismatch() -> None:
    trade = build_approved_trade()
    mismatched_portfolio = build_portfolio(symbol="EURUSD")
    with pytest.raises(PaperExecutionError, match="does not match evidence.symbol"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            mismatched_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_non_positive_quantity() -> None:
    trade = build_approved_trade()
    with pytest.raises(PaperExecutionError, match="quantity must be positive"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            trade.portfolio,
            quantity=0.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_opening_while_a_position_is_already_open() -> None:
    trade = build_approved_trade()
    already_open = trade.portfolio.model_copy(update={"open_position": build_position()})
    with pytest.raises(PaperExecutionError, match="single-position-at-a-time"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            already_open,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_quantity_that_exceeds_available_cash() -> None:
    # The Risk Guardian approved this proposal for *some* size; execute()
    # still independently guards cash sufficiency against whatever quantity
    # the caller actually supplies, rather than trusting it blindly.
    trade = build_approved_trade(cash=1_000.0)
    with pytest.raises(PaperExecutionError, match="exceeds available cash"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            trade.portfolio,
            quantity=1_000.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


# --- resolve_stop_take_profit ---


def test_resolve_with_no_open_position_marks_to_market_and_closes_nothing() -> None:
    portfolio = build_portfolio(open_position=None)
    evidence = build_evidence(last_price=9999.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is None
    assert new_portfolio.open_position is None


def test_resolve_when_neither_level_is_reached_leaves_the_position_open() -> None:
    position = build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2420.0)  # between stop and take-profit
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is None
    assert new_portfolio.open_position == position
    assert new_portfolio.equity == pytest.approx(90_000.0 + position.unrealized_pnl(2420.0))


def test_resolve_long_position_at_stop_loss_closes_with_a_loss() -> None:
    position = build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2350.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert closed.realized_pnl < 0
    assert new_portfolio.open_position is None


def test_resolve_long_position_at_take_profit_closes_with_a_gain() -> None:
    position = build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2500.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert closed.realized_pnl > 0
    assert new_portfolio.open_position is None


def test_resolve_short_position_at_stop_loss_closes_with_a_loss() -> None:
    position = build_position(
        direction=Direction.SHORT, entry_price=2400.0, stop_loss=2450.0, take_profit=2300.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2450.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert closed.realized_pnl < 0


def test_resolve_short_position_at_take_profit_closes_with_a_gain() -> None:
    position = build_position(
        direction=Direction.SHORT, entry_price=2400.0, stop_loss=2450.0, take_profit=2300.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2300.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert closed.realized_pnl > 0


def test_resolve_rejects_a_symbol_mismatch() -> None:
    portfolio = build_portfolio(symbol="EURUSD", open_position=build_position())
    evidence = build_evidence(last_price=2500.0)  # symbol="XAUUSD"
    with pytest.raises(PaperExecutionError, match="does not match evidence.symbol"):
        resolve_stop_take_profit(portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME)


# --- cash conservation across a full open/close round trip ---


def test_cash_conservation_across_open_and_close_long() -> None:
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=10.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2500.0)  # take-profit hit
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    expected_cash = trade.portfolio.cash - opened.fill.fee + closed.realized_pnl
    assert closed_portfolio.cash == pytest.approx(expected_cash)
    assert closed_portfolio.equity == pytest.approx(closed_portfolio.cash)


def test_cash_conservation_across_open_and_close_short() -> None:
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    trade = build_approved_trade(
        action=Action.SELL,
        direction=Direction.SHORT,
        entry_price=2400.0,
        stop_loss=2450.0,
        take_profit=2300.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=10.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2300.0)  # take-profit hit
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    expected_cash = trade.portfolio.cash - opened.fill.fee + closed.realized_pnl
    assert closed_portfolio.cash == pytest.approx(expected_cash)


def test_a_losing_round_trip_still_conserves_cash() -> None:
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=10.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2350.0)  # stop-loss hit
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.realized_pnl < 0
    expected_cash = trade.portfolio.cash - opened.fill.fee + closed.realized_pnl
    assert closed_portfolio.cash == pytest.approx(expected_cash)


# --- full feedback loop: BUY -> APPROVED -> PAPER FILL -> updated PortfolioState -> next review()


def test_full_buy_approve_fill_feedback_loop_blocks_a_second_trade() -> None:
    trade = build_approved_trade(action=Action.BUY, direction=Direction.LONG, entry_price=2400.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    assert result.portfolio.open_position is not None

    # A second BUY proposal, reviewed against the *updated* portfolio from
    # the first fill -- the feedback loop this test exists to prove closes.
    second_evidence = build_evidence(last_price=2400.0)
    second_proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=second_evidence,
        analysis_id="second-analysis",
    )
    second_verdict = review(
        second_proposal,
        second_evidence,
        policy=build_policy(),
        portfolio=result.portfolio,
        now=BASE_TIME,
    )
    assert second_verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.POSITION_ALREADY_OPEN in second_verdict.reason_codes


def test_full_loop_permits_a_new_trade_after_the_position_closes() -> None:
    policy = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2500.0)
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed_portfolio.open_position is None

    third_proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=exit_evidence,
        analysis_id="third-analysis",
    )
    third_verdict = review(
        third_proposal,
        exit_evidence,
        policy=build_policy(),
        portfolio=closed_portfolio,
        now=BASE_TIME + timedelta(hours=1),
    )
    assert third_verdict.outcome is RiskOutcome.APPROVED
