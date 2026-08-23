"""Deterministic technical indicators computed from real OHLCV bars.

Indicators are evidence, not decisions. Every function here is a pure
computation over `bars` — no network calls, no AI, no randomness — so results
are always exactly reproducible from the same input data.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta
from pydantic import BaseModel

from orion.data.models import OHLCVBar


def bars_to_dataframe(bars: list[OHLCVBar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [b.timestamp for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume if b.volume is not None else 0.0 for b in bars],
        }
    )


class IndicatorSnapshot(BaseModel):
    """Latest value of each indicator. None means "not enough data" — never a guess."""

    ema_fast: float | None = None
    ema_slow: float | None = None
    sma_slow: float | None = None
    rsi: float | None = None
    atr: float | None = None

    ema_fast_length: int
    ema_slow_length: int
    sma_length: int
    rsi_length: int
    atr_length: int


def _last_value(series: pd.Series | None) -> float | None:
    if series is None or series.empty:
        return None
    value = series.iloc[-1]
    return float(value) if pd.notna(value) else None


def compute_indicators(
    bars: list[OHLCVBar],
    *,
    ema_fast_length: int = 20,
    ema_slow_length: int = 50,
    sma_length: int = 50,
    rsi_length: int = 14,
    atr_length: int = 14,
) -> IndicatorSnapshot:
    """Compute the latest value of each indicator, or None where `bars` is too short."""
    snapshot_kwargs = dict(
        ema_fast_length=ema_fast_length,
        ema_slow_length=ema_slow_length,
        sma_length=sma_length,
        rsi_length=rsi_length,
        atr_length=atr_length,
    )

    if len(bars) < 2:
        return IndicatorSnapshot(**snapshot_kwargs)

    df = bars_to_dataframe(bars)

    ema_fast = (
        _last_value(ta.ema(df["close"], length=ema_fast_length))
        if len(df) >= ema_fast_length
        else None
    )
    ema_slow = (
        _last_value(ta.ema(df["close"], length=ema_slow_length))
        if len(df) >= ema_slow_length
        else None
    )
    sma_slow = (
        _last_value(ta.sma(df["close"], length=sma_length)) if len(df) >= sma_length else None
    )
    rsi = _last_value(ta.rsi(df["close"], length=rsi_length)) if len(df) >= rsi_length + 1 else None
    atr = (
        _last_value(ta.atr(df["high"], df["low"], df["close"], length=atr_length))
        if len(df) >= atr_length + 1
        else None
    )

    return IndicatorSnapshot(
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        sma_slow=sma_slow,
        rsi=rsi,
        atr=atr,
        **snapshot_kwargs,
    )
