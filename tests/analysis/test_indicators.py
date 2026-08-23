from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.analysis.indicators import compute_indicators
from orion.data.models import OHLCVBar

BASE_TIME = datetime(2026, 8, 23, 0, 0, 0, tzinfo=UTC)


def _rising_bars(n: int, start: float = 2000.0, step: float = 1.0) -> list[OHLCVBar]:
    """Build `n` bars with a steadily rising close, each internally OHLC-consistent.

    `high`/`low` are derived from `open`/`close` (not a fixed +/-0.5 offset) so this
    stays valid for any `step`, including steps larger than the old fixed offset.
    """
    bars = []
    price = start
    for i in range(n):
        open_ = price
        close = price + step
        bars.append(
            OHLCVBar(
                timestamp=BASE_TIME + timedelta(hours=i),
                open=open_,
                high=max(open_, close) + 0.5,
                low=min(open_, close) - 0.5,
                close=close,
                volume=100.0,
            )
        )
        price += step
    return bars


def test_insufficient_bars_returns_all_none() -> None:
    snapshot = compute_indicators(_rising_bars(3))
    assert snapshot.ema_fast is None
    assert snapshot.ema_slow is None
    assert snapshot.sma_slow is None
    assert snapshot.rsi is None
    assert snapshot.atr is None


def test_sufficient_bars_produce_values() -> None:
    bars = _rising_bars(60)
    snapshot = compute_indicators(
        bars, ema_fast_length=20, ema_slow_length=50, sma_length=50, rsi_length=14, atr_length=14
    )

    assert snapshot.ema_fast is not None
    assert snapshot.ema_slow is not None
    assert snapshot.sma_slow is not None
    assert snapshot.rsi is not None
    assert snapshot.atr is not None


def test_steadily_rising_prices_give_high_rsi() -> None:
    bars = _rising_bars(30)
    snapshot = compute_indicators(bars, rsi_length=14)
    assert snapshot.rsi is not None
    assert snapshot.rsi > 70  # a monotonic uptrend should read strongly overbought


def test_ema_fast_tracks_price_above_ema_slow_in_uptrend() -> None:
    bars = _rising_bars(60)
    snapshot = compute_indicators(bars, ema_fast_length=10, ema_slow_length=30)
    assert snapshot.ema_fast is not None and snapshot.ema_slow is not None
    assert snapshot.ema_fast > snapshot.ema_slow


def test_atr_is_positive_for_bars_with_range() -> None:
    bars = _rising_bars(30)
    snapshot = compute_indicators(bars, atr_length=14)
    assert snapshot.atr is not None
    assert snapshot.atr > 0
