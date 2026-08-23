"""Data shapes for real market data: bars, source metadata, and snapshots.

These are evidence, not decisions — nothing here decides BUY/SELL/WAIT. Every
value that reaches an OHLCVBar must have come from a real provider response;
nothing in this module synthesizes or fabricates price data.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Timeframe(StrEnum):
    """Supported bar intervals, using Twelve Data's interval strings."""

    M15 = "15min"
    H1 = "1h"
    H4 = "4h"
    D1 = "1day"


class OHLCVBar(BaseModel):
    """One immutable open/high/low/close/volume bar."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    @model_validator(mode="after")
    def _check_ohlc_consistency(self) -> OHLCVBar:
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) is below low ({self.low})")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open ({self.open}) is outside [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close ({self.close}) is outside [low, high]")
        for name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        return self


class DataSourceMeta(BaseModel):
    """Where a snapshot came from and when it was retrieved — required for audit."""

    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    timeframe: Timeframe
    retrieved_at: datetime


class MarketSnapshot(BaseModel):
    """A validated-shape (not yet quality-validated) batch of bars plus provenance."""

    model_config = ConfigDict(frozen=True)

    source: DataSourceMeta
    bars: list[OHLCVBar] = Field(default_factory=list)

    @property
    def latest_bar(self) -> OHLCVBar | None:
        return self.bars[-1] if self.bars else None
