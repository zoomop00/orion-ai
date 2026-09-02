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


class PriceObservation(BaseModel):
    """One symbol's most recent traded price, and when it was observed.

    THE SMALLEST HONEST ANSWER TO "WHAT IS IT WORTH RIGHT NOW". Mission 9
    separates resolving an open position from analysing the market, and this is
    what the resolving half needs: `orion.paper.execution.resolve_stop_take_
    profit` reads a symbol and a last price and nothing else, but until now the
    only type carrying those was `orion.analysis.evidence.Evidence` — which
    cannot exist without every indicator, every structure feature and the bars
    they were computed from. Asking for an `Evidence` to check whether a stop
    was reached made a stop-loss depend on the whole analysis pipeline; this
    type is the dependency that check actually has.

    IT IS NOT EVIDENCE AND MUST NEVER GROW INTO IT. Nothing here is computed:
    a price is read off a bar, not derived from a window of them. There is no
    indicator, no structure, no bar count and no fingerprint, and none should
    be added — the moment this type can answer "what is the trend", something
    will reason from it, and reasoning is `Evidence`'s job. `Evidence` remains
    the only input to a proposal; this remains the only input to a resolution.

    `as_of` IS THE BAR'S OWN TIMESTAMP, NOT THE MOMENT OF THE FETCH. It says
    when this price was true, which is the question a reader of a close needs
    answered; when it was retrieved is `DataSourceMeta.retrieved_at`'s, and the
    two are deliberately different fields on deliberately different types.
    `resolve_stop_take_profit` does not read `as_of` — it stamps a close with
    the caller-supplied `now`, exactly as it always has — so this field is
    carried for provenance and rendering, and that limit is stated rather than
    left for a reader to discover.

    Frozen and validated like every other model here: a price that is not
    positive, or a timestamp with no timezone, is not constructible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    last_price: float = Field(gt=0)
    as_of: datetime

    @model_validator(mode="after")
    def _require_timezone(self) -> PriceObservation:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be a timezone-aware datetime")
        return self
