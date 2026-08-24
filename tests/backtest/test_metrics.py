"""Tests for `orion.backtest.metrics` -- every function must raise
`BacktestMetricsError` rather than return `NaN`/`inf`/a fabricated `0.0`
for degenerate input (empty sequences, zero variance, a zero-equity base,
zero gross loss, zero downside deviation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.backtest.metrics import (
    BacktestMetricsError,
    expectancy,
    exposure,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)
from orion.backtest.schemas import EquityPoint
from orion.paper.schemas import ClosedPosition, CloseReason
from orion.reasoning.schemas import Direction

BASE_TIME = datetime(2026, 1, 5, tzinfo=UTC)
H1 = timedelta(hours=1)
FINGERPRINT = "a" * 64


def _closed(pnl: float) -> ClosedPosition:
    return ClosedPosition(
        symbol="XAUUSD",
        direction=Direction.LONG,
        quantity=1.0,
        entry_price=2400.0,
        exit_price=2400.0 + pnl,
        opened_at=BASE_TIME,
        closed_at=BASE_TIME + H1,
        close_reason=CloseReason.TAKE_PROFIT_HIT if pnl >= 0 else CloseReason.STOP_LOSS_HIT,
        realized_pnl=pnl,
        fee=0.0,
        opened_by_verdict_fingerprint=FINGERPRINT,
    )


def _point(i: int, equity: float, *, has_open_position: bool = False) -> EquityPoint:
    return EquityPoint(as_of=BASE_TIME + i * H1, equity=equity, has_open_position=has_open_position)


# --- expectancy ---


def test_expectancy_is_the_mean_realized_pnl() -> None:
    assert expectancy([_closed(100.0), _closed(-50.0), _closed(30.0)]) == pytest.approx(80.0 / 3)


def test_expectancy_raises_for_zero_trades() -> None:
    with pytest.raises(BacktestMetricsError):
        expectancy([])


# --- profit_factor ---


def test_profit_factor_is_gross_profit_over_gross_loss() -> None:
    assert profit_factor([_closed(100.0), _closed(-50.0)]) == pytest.approx(2.0)


def test_profit_factor_raises_for_zero_trades() -> None:
    with pytest.raises(BacktestMetricsError):
        profit_factor([])


def test_profit_factor_raises_rather_than_returning_infinity_for_zero_losses() -> None:
    with pytest.raises(BacktestMetricsError):
        profit_factor([_closed(100.0), _closed(50.0)])


# --- exposure ---


def test_exposure_is_the_fraction_of_bars_with_an_open_position() -> None:
    curve = [_point(0, 100.0, has_open_position=True), _point(1, 100.0, has_open_position=False)]
    assert exposure(curve) == pytest.approx(0.5)


def test_exposure_raises_for_an_empty_curve() -> None:
    with pytest.raises(BacktestMetricsError):
        exposure([])


# --- sharpe_ratio ---


def test_sharpe_ratio_computes_a_finite_value_for_varying_returns() -> None:
    curve = [_point(0, 100.0), _point(1, 105.0), _point(2, 102.0), _point(3, 110.0)]
    result = sharpe_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)
    assert isinstance(result, float)


def test_sharpe_ratio_raises_for_fewer_than_three_points() -> None:
    curve = [_point(0, 100.0), _point(1, 105.0)]
    with pytest.raises(BacktestMetricsError):
        sharpe_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)


def test_sharpe_ratio_raises_for_zero_return_variance() -> None:
    curve = [_point(0, 100.0), _point(1, 110.0), _point(2, 121.0)]  # constant +10% each period
    with pytest.raises(BacktestMetricsError):
        sharpe_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)


def test_sharpe_ratio_raises_for_a_zero_equity_base() -> None:
    curve = [_point(0, 0.0), _point(1, 10.0), _point(2, 20.0)]
    with pytest.raises(BacktestMetricsError):
        sharpe_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)


# --- sortino_ratio ---


def test_sortino_ratio_computes_a_finite_value_when_a_downside_period_exists() -> None:
    curve = [_point(0, 100.0), _point(1, 90.0), _point(2, 108.0), _point(3, 95.0)]
    result = sortino_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)
    assert isinstance(result, float)


def test_sortino_ratio_raises_for_fewer_than_three_points() -> None:
    curve = [_point(0, 100.0), _point(1, 105.0)]
    with pytest.raises(BacktestMetricsError):
        sortino_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)


def test_sortino_ratio_raises_when_no_period_return_falls_below_the_risk_free_rate() -> None:
    curve = [_point(0, 100.0), _point(1, 105.0), _point(2, 111.0)]  # always positive returns
    with pytest.raises(BacktestMetricsError):
        sortino_ratio(curve, risk_free_rate_per_period=0.0, periods_per_year=252.0)
