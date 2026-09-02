"""Deterministic market structure: trend, volatility regime, support/resistance.

Like indicators.py, everything here is a pure function of `bars` (plus an
already-computed IndicatorSnapshot) — evidence for the reasoning layer, never
a decision by itself.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from orion.analysis.indicators import IndicatorSnapshot
from orion.analysis.price_action import SwingKind, find_swings
from orion.data.models import OHLCVBar


class TrendDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"


class VolatilityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class StructureSnapshot(BaseModel):
    trend: TrendDirection
    volatility: VolatilityLevel
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    swing_high_count: int = 0
    swing_low_count: int = 0


def determine_trend(last_close: float, indicators: IndicatorSnapshot) -> TrendDirection:
    if indicators.ema_fast is None or indicators.ema_slow is None:
        return TrendDirection.UNKNOWN
    if indicators.ema_fast > indicators.ema_slow and last_close > indicators.ema_slow:
        return TrendDirection.BULLISH
    if indicators.ema_fast < indicators.ema_slow and last_close < indicators.ema_slow:
        return TrendDirection.BEARISH
    return TrendDirection.SIDEWAYS


def classify_volatility(
    last_close: float,
    indicators: IndicatorSnapshot,
    *,
    low_atr_pct: float = 0.003,
    high_atr_pct: float = 0.008,
) -> VolatilityLevel:
    """Classify ATR-as-percent-of-price into a LOW/MEDIUM/HIGH regime.

    Threshold status: `low_atr_pct`/`high_atr_pct` are engineering placeholders
    (order-of-magnitude intraday bands), not derived from a historical XAUUSD
    ATR% distribution — no backtest or empirical calibration has been run. Two
    known gaps to resolve (Mission 6 — Backtesting & Validation) before this
    feeds a real decision: (1) the thresholds are not timeframe-aware, even
    though ATR% naturally grows with the bar interval (M15 vs H4 vs D1), and
    (2) no historical percentile analysis backs the specific 0.3%/0.8% cut
    points. Treat `VolatilityLevel` as a rough regime signal, not a calibrated
    one, until that analysis exists.
    """
    if indicators.atr is None or last_close <= 0:
        return VolatilityLevel.UNKNOWN
    atr_pct = indicators.atr / last_close
    if atr_pct < low_atr_pct:
        return VolatilityLevel.LOW
    if atr_pct < high_atr_pct:
        return VolatilityLevel.MEDIUM
    return VolatilityLevel.HIGH


def find_swing_points(
    bars: list[OHLCVBar], *, lookback: int = 2
) -> tuple[list[float], list[float]]:
    """Simple fractal swing detection: a bar is a swing high/low if it is the
    max/min of the `2 * lookback + 1` bars centered on it.

    The prices only, in index order — the shape this module and `Evidence`
    have always used. The detection itself now lives in
    `orion.analysis.price_action.find_swings`, which returns the same swings
    with their index, timestamp and confirmation bar attached; structure
    breaks, sweeps and Fibonacci anchors need those and this does not. One
    fractal rule, two views of it, rather than a second implementation that
    could drift from the first.
    """
    swings = find_swings(bars, lookback=lookback)
    return (
        [swing.price for swing in swings if swing.kind is SwingKind.HIGH],
        [swing.price for swing in swings if swing.kind is SwingKind.LOW],
    )


def nearest_levels(
    last_close: float, swing_highs: list[float], swing_lows: list[float]
) -> tuple[float | None, float | None]:
    resistances = sorted(h for h in swing_highs if h > last_close)
    supports = sorted((low for low in swing_lows if low < last_close), reverse=True)
    return (supports[0] if supports else None, resistances[0] if resistances else None)


def compute_structure(
    bars: list[OHLCVBar], indicators: IndicatorSnapshot, *, swing_lookback: int = 2
) -> StructureSnapshot:
    if not bars:
        return StructureSnapshot(trend=TrendDirection.UNKNOWN, volatility=VolatilityLevel.UNKNOWN)

    last_close = bars[-1].close
    swing_highs, swing_lows = find_swing_points(bars, lookback=swing_lookback)
    support, resistance = nearest_levels(last_close, swing_highs, swing_lows)

    return StructureSnapshot(
        trend=determine_trend(last_close, indicators),
        volatility=classify_volatility(last_close, indicators),
        nearest_support=support,
        nearest_resistance=resistance,
        swing_high_count=len(swing_highs),
        swing_low_count=len(swing_lows),
    )
