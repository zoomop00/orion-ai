"""Tests for `orion.backtest.execution` -- intrabar (OHLC-aware) stop/
take-profit resolution and delayed-fill execution.

Covers: the stop-loss-first tie-break convention on an ambiguous both-hit
bar (M6-Q2), only-stop / only-take-profit / neither-hit / no-open-position
resolution, fee/spread/slippage-correct pricing, cash conservation, and
every `execute_delayed` fail-closed path -- including why it exists at all
(a fill priced off Evidence the proposal was never reviewed against, which
`orion.paper.execution.execute` structurally refuses).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orion.backtest.execution import execute_delayed, resolve_stop_take_profit_ohlc
from orion.data.models import OHLCVBar
from orion.paper.execution import execute
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason, PaperExecutionError
from orion.reasoning.schemas import Direction
from orion.risk.portfolio import portfolio_state_fingerprint
from orion.risk.schemas import RiskOutcome
from tests.paper.conftest import BASE_TIME, build_approved_trade, build_evidence, build_position

ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)


def _bar(*, open_: float, high: float, low: float, close: float) -> OHLCVBar:
    return OHLCVBar(timestamp=BASE_TIME, open=open_, high=high, low=low, close=close)


# --- resolve_stop_take_profit_ohlc ---


def test_no_open_position_marks_to_market_only() -> None:
    trade = build_approved_trade()
    portfolio = trade.portfolio
    bar = _bar(open_=2400.0, high=2410.0, low=2395.0, close=2405.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is None
    assert marked.open_position is None
    assert marked.equity == portfolio.cash


def test_neither_level_reached_marks_to_market_only() -> None:
    position = build_position(direction=Direction.LONG, stop_loss=2350.0, take_profit=2500.0)
    portfolio = build_approved_trade().portfolio.model_copy(update={"open_position": position})
    bar = _bar(open_=2400.0, high=2420.0, low=2390.0, close=2410.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is None
    assert marked.open_position is not None


def test_only_stop_loss_reached_closes_as_stop_loss_hit() -> None:
    position = build_position(direction=Direction.LONG, stop_loss=2350.0, take_profit=2500.0)
    portfolio = build_approved_trade().portfolio.model_copy(update={"open_position": position})
    bar = _bar(open_=2360.0, high=2365.0, low=2340.0, close=2345.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert marked.open_position is None


def test_only_take_profit_reached_closes_as_take_profit_hit() -> None:
    position = build_position(direction=Direction.LONG, stop_loss=2350.0, take_profit=2500.0)
    portfolio = build_approved_trade().portfolio.model_copy(update={"open_position": position})
    bar = _bar(open_=2495.0, high=2510.0, low=2490.0, close=2505.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert marked.open_position is None


def test_both_levels_reached_within_one_bar_resolves_stop_loss_first() -> None:
    """The M6-Q2 conservative tie-break: a bar whose [low, high] range
    reaches both the stop and the take-profit must resolve as a stop-loss
    close, never a take-profit close -- OHLC data alone cannot say which
    was actually touched first intraday, so the engine never assumes the
    better outcome."""
    position = build_position(direction=Direction.LONG, stop_loss=2350.0, take_profit=2500.0)
    portfolio = build_approved_trade().portfolio.model_copy(update={"open_position": position})
    bar = _bar(open_=2400.0, high=2510.0, low=2340.0, close=2450.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert closed.exit_price == pytest.approx(2350.0)


def test_both_levels_reached_short_position_also_resolves_stop_loss_first() -> None:
    position = build_position(direction=Direction.SHORT, stop_loss=2450.0, take_profit=2300.0)
    portfolio = build_approved_trade().portfolio.model_copy(update={"open_position": position})
    # SHORT: hit_stop when high >= stop_loss, hit_take_profit when low <= take_profit.
    bar = _bar(open_=2400.0, high=2460.0, low=2290.0, close=2350.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT


def test_spread_and_slippage_worsen_the_exit_price_vs_the_trigger_level() -> None:
    position = build_position(direction=Direction.LONG, stop_loss=2350.0, take_profit=2500.0)
    portfolio = build_approved_trade().portfolio.model_copy(update={"open_position": position})
    bar = _bar(open_=2495.0, high=2510.0, low=2490.0, close=2505.0)
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    _, closed = resolve_stop_take_profit_ohlc(portfolio, bar, policy=policy, now=BASE_TIME)
    assert closed is not None
    # Closing a LONG sells -- a worse fill is a *lower* exit price than the
    # raw take_profit trigger level.
    assert closed.exit_price < 2500.0
    assert closed.fee > 0


def test_cash_conservation_across_a_full_round_trip() -> None:
    position = build_position(
        direction=Direction.LONG,
        quantity=1.0,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    trade = build_approved_trade()
    opening_fee = 5.0
    cash_after_open = trade.portfolio.cash - position.quantity * position.entry_price - opening_fee
    portfolio = trade.portfolio.model_copy(
        update={"open_position": position, "cash": cash_after_open, "equity": cash_after_open}
    )
    bar = _bar(open_=2495.0, high=2510.0, low=2490.0, close=2505.0)
    marked, closed = resolve_stop_take_profit_ohlc(
        portfolio, bar, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is not None
    expected_cash = cash_after_open + position.quantity * position.entry_price + closed.realized_pnl
    assert marked.cash == pytest.approx(expected_cash)


# --- execute_delayed ---


def test_execute_delayed_prices_off_the_fill_evidence_not_the_proposal_evidence() -> None:
    trade = build_approved_trade(entry_price=2400.0)
    later_evidence = build_evidence(last_price=2450.0)
    result = execute_delayed(
        trade.proposal,
        trade.verdict,
        later_evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME + timedelta(hours=2),
    )
    assert result.fill.fill_price == pytest.approx(2450.0)


def test_execute_rejects_the_same_later_evidence_execute_delayed_accepts() -> None:
    """Proves `execute_delayed` is not merely a redundant duplicate:
    `orion.paper.execution.execute` structurally refuses a fill Evidence
    that does not match `proposal.evidence_ref` -- exactly the scenario
    `execute_delayed` exists to handle."""
    trade = build_approved_trade(entry_price=2400.0)
    later_evidence = build_evidence(last_price=2450.0)
    with pytest.raises(PaperExecutionError, match="evidence"):
        execute(
            trade.proposal,
            trade.verdict,
            later_evidence,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME + timedelta(hours=2),
        )


def test_execute_delayed_still_enforces_portfolio_linkage() -> None:
    trade = build_approved_trade()
    later_evidence = build_evidence(last_price=2450.0)
    substituted_portfolio = trade.portfolio.model_copy(update={"cash": trade.portfolio.cash + 1.0})
    assert (
        portfolio_state_fingerprint(substituted_portfolio)
        != trade.verdict.portfolio_ref.content_hash
    )
    with pytest.raises(PaperExecutionError, match="portfolio_ref"):
        execute_delayed(
            trade.proposal,
            trade.verdict,
            later_evidence,
            substituted_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_delayed_rejects_non_approved_verdict() -> None:
    trade = build_approved_trade()
    veto_verdict = trade.verdict.model_copy(update={"outcome": RiskOutcome.VETOED})
    with pytest.raises(PaperExecutionError):
        execute_delayed(
            trade.proposal,
            veto_verdict,
            build_evidence(last_price=2400.0),
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_delayed_rejects_mismatched_symbol_fill_evidence() -> None:
    trade = build_approved_trade()
    mismatched = build_evidence(last_price=2450.0).model_copy(update={"symbol": "EURUSD"})
    with pytest.raises(PaperExecutionError, match="symbol"):
        execute_delayed(
            trade.proposal,
            trade.verdict,
            mismatched,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_delayed_rejects_already_open_position() -> None:
    trade = build_approved_trade()
    position = build_position()
    busy_portfolio = trade.portfolio.model_copy(update={"open_position": position})
    with pytest.raises(PaperExecutionError, match="already holds an open position"):
        execute_delayed(
            trade.proposal,
            trade.verdict,
            build_evidence(last_price=2450.0),
            busy_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_delayed_rejects_non_positive_quantity() -> None:
    trade = build_approved_trade()
    with pytest.raises(PaperExecutionError, match="quantity"):
        execute_delayed(
            trade.proposal,
            trade.verdict,
            build_evidence(last_price=2450.0),
            trade.portfolio,
            quantity=0.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_delayed_rejects_notional_exceeding_cash() -> None:
    trade = build_approved_trade(cash=10.0)
    with pytest.raises(PaperExecutionError, match="cash"):
        execute_delayed(
            trade.proposal,
            trade.verdict,
            build_evidence(last_price=2450.0),
            trade.portfolio,
            quantity=1000.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_delayed_produces_a_position_reflecting_the_delayed_fill_price() -> None:
    trade = build_approved_trade(entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0)
    later_evidence = build_evidence(last_price=2420.0)
    result = execute_delayed(
        trade.proposal,
        trade.verdict,
        later_evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME + timedelta(hours=1),
    )
    assert result.portfolio.open_position is not None
    assert result.portfolio.open_position.entry_price == pytest.approx(2420.0)
    # The trade's original stop_loss/take_profit are preserved from the
    # proposal -- only the entry price moves with the delayed fill.
    assert result.portfolio.open_position.stop_loss == 2350.0
    assert result.portfolio.open_position.take_profit == 2500.0
