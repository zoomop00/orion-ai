from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.analysis.indicators import IndicatorSnapshot
from orion.analysis.structure import (
    TrendDirection,
    VolatilityLevel,
    classify_volatility,
    determine_trend,
    find_swing_points,
    nearest_levels,
)
from orion.data.models import OHLCVBar

BASE_TIME = datetime(2026, 8, 23, 0, 0, 0, tzinfo=UTC)


def _indicator_snapshot(**overrides: float | None) -> IndicatorSnapshot:
    defaults = dict(
        ema_fast_length=20, ema_slow_length=50, sma_length=50, rsi_length=14, atr_length=14
    )
    return IndicatorSnapshot(**defaults, **overrides)


def test_determine_trend_bullish() -> None:
    ind = _indicator_snapshot(ema_fast=110.0, ema_slow=100.0)
    assert determine_trend(last_close=112.0, indicators=ind) is TrendDirection.BULLISH


def test_determine_trend_bearish() -> None:
    ind = _indicator_snapshot(ema_fast=90.0, ema_slow=100.0)
    assert determine_trend(last_close=88.0, indicators=ind) is TrendDirection.BEARISH


def test_determine_trend_sideways_when_price_and_emas_disagree() -> None:
    ind = _indicator_snapshot(ema_fast=110.0, ema_slow=100.0)
    assert determine_trend(last_close=95.0, indicators=ind) is TrendDirection.SIDEWAYS


def test_determine_trend_unknown_without_emas() -> None:
    ind = _indicator_snapshot()
    assert determine_trend(last_close=100.0, indicators=ind) is TrendDirection.UNKNOWN


def test_classify_volatility_levels() -> None:
    # last_close=2000.0 with the default low_atr_pct=0.003 / high_atr_pct=0.008
    # thresholds (see classify_volatility's docstring: uncalibrated placeholders,
    # not derived from historical ATR% data). Values below are chosen to land
    # clearly inside each bucket rather than near a boundary:
    #   atr=2.0  -> atr_pct=0.001 (0.1%)  < 0.003            -> LOW
    #   atr=10.0 -> atr_pct=0.005 (0.5%)  in [0.003, 0.008)   -> MEDIUM
    #   atr=20.0 -> atr_pct=0.010 (1.0%)  >= 0.008            -> HIGH
    ind_low = _indicator_snapshot(atr=2.0)
    ind_medium = _indicator_snapshot(atr=10.0)
    ind_high = _indicator_snapshot(atr=20.0)

    assert classify_volatility(last_close=2000.0, indicators=ind_low) is VolatilityLevel.LOW
    assert classify_volatility(last_close=2000.0, indicators=ind_medium) is VolatilityLevel.MEDIUM
    assert classify_volatility(last_close=2000.0, indicators=ind_high) is VolatilityLevel.HIGH


def test_classify_volatility_unknown_without_atr() -> None:
    ind = _indicator_snapshot()
    assert classify_volatility(last_close=2000.0, indicators=ind) is VolatilityLevel.UNKNOWN


def _bar(i: int, high: float, low: float) -> OHLCVBar:
    mid = (high + low) / 2
    return OHLCVBar(
        timestamp=BASE_TIME + timedelta(hours=i),
        open=mid,
        high=high,
        low=low,
        close=mid,
        volume=10.0,
    )


def test_find_swing_points_detects_local_extremes() -> None:
    highs = [10, 12, 15, 11, 9, 13, 16, 14, 10, 8]
    lows = [6, 4, 2, 5, 7, 3, 1, 4, 6, 7]
    bars = [_bar(i, h, low) for i, (h, low) in enumerate(zip(highs, lows, strict=True))]

    swing_highs, swing_lows = find_swing_points(bars, lookback=2)

    assert swing_highs == [15, 16]
    assert swing_lows == [2, 1]


def test_nearest_levels_picks_closest_support_and_resistance() -> None:
    support, resistance = nearest_levels(last_close=7.5, swing_highs=[15, 16], swing_lows=[2, 1])
    assert support == 2
    assert resistance == 15


def test_nearest_levels_returns_none_when_no_side_available() -> None:
    support, resistance = nearest_levels(last_close=7.5, swing_highs=[], swing_lows=[])
    assert support is None
    assert resistance is None
