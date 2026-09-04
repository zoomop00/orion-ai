"""Shared builders for `orion.datasets` tests.

Bars are real `OHLCVBar`s with deliberately awkward float values -- prices that
do not survive a two-decimal format string -- so that any test asserting a
stable fingerprint would genuinely fail if the CSV codec ever started
formatting floats instead of writing their exact repr.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.data.historical import HistoricalMarketDataProvider
from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe

BASE_TIME = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # a Monday
H1 = timedelta(hours=1)
SYMBOL = "XAUUSD"
TIMEFRAME = Timeframe.H1


def make_bars(
    n: int = 8, *, start: datetime = BASE_TIME, start_price: float = 2400.0
) -> list[OHLCVBar]:
    """`n` contiguous H1 bars with non-round prices."""
    bars: list[OHLCVBar] = []
    for i in range(n):
        close = start_price + i * 1.0 / 3.0  # 2400.0, 2400.333..., deliberately not 2 dp
        bars.append(
            OHLCVBar(
                timestamp=start + i * H1,
                open=close - 0.125,
                high=close + 0.875,
                low=close - 1.125,
                close=close,
                volume=1000.5,
            )
        )
    return bars


def make_snapshot(
    bars: list[OHLCVBar],
    *,
    provider: str = "twelvedata",
    retrieved_at: datetime | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        source=DataSourceMeta(
            provider=provider,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            retrieved_at=retrieved_at if retrieved_at is not None else BASE_TIME,
        ),
        bars=bars,
    )


class StubHistoricalProvider(HistoricalMarketDataProvider):
    """Returns a prepared snapshot, recording what it was asked for.

    Deterministic and offline: `orion.datasets` takes its provider as an
    argument precisely so a build can be tested without a vendor, a credential
    or a network.
    """

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[tuple[str, Timeframe, datetime, datetime]] = []

    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        self.calls.append((symbol, timeframe, start, end))
        return self._snapshot


class RaisingHistoricalProvider(HistoricalMarketDataProvider):
    """Always fails -- proves a build aborts rather than writing a partial
    dataset."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        raise self._error
