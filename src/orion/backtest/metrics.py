"""Pure, deterministic aggregate performance statistics over one backtest
run's output.

Genuinely new computation, distinct from `orion.reflection`: Mission 5's
`TradeReflection` is deliberately per-trade, objective-facts-only, with no
aggregation across trades (see its own architecture entry in `docs/
DECISIONS.md`). Sharpe/Sortino/profit factor/expectancy/exposure are
*sequence-level* statistics — the master brief lists them explicitly under
Mission 6, not Mission 5 — and nothing here computes, returns, or implies a
"good/bad decision" judgment; these are standard, named quant formulas
applied to the numbers a backtest already produced, nothing more.

Every function raises `BacktestMetricsError` rather than returning `NaN`/
`inf`/a fabricated `0.0` when the input is insufficient to compute a
meaningful number (zero trades, zero variance, a zero-equity base, a
sample too small for a ratio to mean anything) — never silent. Sharpe/
Sortino's `risk_free_rate_per_period`/`periods_per_year` are required,
caller-supplied parameters with no default, matching this codebase's "no
guessed constants" discipline already applied to `RiskPolicy.
min_risk_reward`.
"""

from __future__ import annotations

from collections.abc import Sequence

from orion.backtest.schemas import EquityPoint
from orion.paper.schemas import ClosedPosition


class BacktestMetricsError(RuntimeError):
    """Raised when a metric cannot be meaningfully computed from the given
    input — never caught and papered over with `NaN`/`inf`/`0.0`. Own
    top-level hierarchy, matching this codebase's "every layer owns its own
    RuntimeError" rule.
    """


def expectancy(closed_positions: Sequence[ClosedPosition]) -> float:
    """Mean realized P&L per closed trade. Raises `BacktestMetricsError`
    for zero trades — the mean of an empty sequence is undefined, not 0.0."""
    if not closed_positions:
        raise BacktestMetricsError("expectancy is undefined for zero closed trades")
    return sum(c.realized_pnl for c in closed_positions) / len(closed_positions)


def profit_factor(closed_positions: Sequence[ClosedPosition]) -> float:
    """Gross profit divided by gross loss (gross loss as a positive
    number). Raises `BacktestMetricsError` for zero trades, and for zero
    losing trades specifically — a genuinely undefined/infinite ratio,
    deliberately raised rather than returned as `float("inf")`, since a
    silently-propagated `inf` can corrupt further aggregation (e.g.
    averaging profit factors across runs) in a way a raised exception
    cannot.
    """
    if not closed_positions:
        raise BacktestMetricsError("profit factor is undefined for zero closed trades")
    gross_profit = sum(c.realized_pnl for c in closed_positions if c.realized_pnl > 0)
    gross_loss = -sum(c.realized_pnl for c in closed_positions if c.realized_pnl < 0)
    if gross_loss == 0:
        raise BacktestMetricsError(
            f"profit factor is undefined (infinite): zero losing trades among "
            f"{len(closed_positions)}, gross_profit={gross_profit:.2f}"
        )
    return gross_profit / gross_loss


def exposure(equity_curve: Sequence[EquityPoint]) -> float:
    """Fraction of processed bars during which a position was open, in
    `[0.0, 1.0]`. Raises `BacktestMetricsError` for an empty equity curve."""
    if not equity_curve:
        raise BacktestMetricsError("exposure is undefined for an empty equity curve")
    open_bars = sum(1 for point in equity_curve if point.has_open_position)
    return open_bars / len(equity_curve)


def _period_returns(equity_curve: Sequence[EquityPoint]) -> list[float]:
    returns: list[float] = []
    # Deliberately `strict=False`: `equity_curve[1:]` is always exactly one
    # element shorter than `equity_curve` by construction (that offset is
    # what pairs each point with its predecessor) -- `strict=True` would
    # make every call raise `ValueError` unconditionally.
    for prev, curr in zip(equity_curve, equity_curve[1:], strict=False):
        if prev.equity == 0:
            raise BacktestMetricsError(
                f"cannot compute a period return from a zero equity base at "
                f"{prev.as_of.isoformat()}"
            )
        returns.append((curr.equity - prev.equity) / prev.equity)
    return returns


def _sample_std(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return float(variance**0.5)


def sharpe_ratio(
    equity_curve: Sequence[EquityPoint],
    *,
    risk_free_rate_per_period: float,
    periods_per_year: float,
) -> float:
    """`(mean(period_returns) - risk_free_rate_per_period) / std(period_
    returns) * sqrt(periods_per_year)`. Needs at least 3 equity points (2
    period returns — a single return has no variance) and non-zero return
    variance; raises `BacktestMetricsError` otherwise rather than a `NaN`/
    `inf` division result.
    """
    if len(equity_curve) < 3:
        raise BacktestMetricsError(
            f"Sharpe ratio needs at least 3 equity points (2 period returns), got "
            f"{len(equity_curve)}"
        )
    returns = _period_returns(equity_curve)
    mean_return = sum(returns) / len(returns)
    std = _sample_std(returns)
    if std == 0:
        raise BacktestMetricsError("Sharpe ratio is undefined: period returns have zero variance")
    return float((mean_return - risk_free_rate_per_period) / std * (periods_per_year**0.5))


def sortino_ratio(
    equity_curve: Sequence[EquityPoint],
    *,
    risk_free_rate_per_period: float,
    periods_per_year: float,
) -> float:
    """Like `sharpe_ratio`, but the denominator is the downside deviation
    (sample std of `min(0, return - risk_free_rate_per_period)`) instead of
    total return variance. Raises `BacktestMetricsError` for fewer than 3
    equity points or zero downside deviation (no return ever fell below
    the risk-free rate — an undefined ratio, not an infinite reward).
    """
    if len(equity_curve) < 3:
        raise BacktestMetricsError(
            f"Sortino ratio needs at least 3 equity points (2 period returns), got "
            f"{len(equity_curve)}"
        )
    returns = _period_returns(equity_curve)
    mean_return = sum(returns) / len(returns)
    downside = [min(0.0, r - risk_free_rate_per_period) for r in returns]
    downside_std = _sample_std(downside)
    if downside_std == 0:
        raise BacktestMetricsError(
            "Sortino ratio is undefined: zero downside deviation (no period return fell "
            "below the risk-free rate)"
        )
    return float((mean_return - risk_free_rate_per_period) / downside_std * (periods_per_year**0.5))
