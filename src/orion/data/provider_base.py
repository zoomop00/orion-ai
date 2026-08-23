"""Abstract interface every market data provider must implement.

The rest of ORION depends only on this interface, never on a specific
provider's client or response shape — swapping Twelve Data for another
provider means writing a new class here, not touching the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orion.data.models import MarketSnapshot, Timeframe


class MarketDataProviderError(RuntimeError):
    """Raised when a provider cannot return real market data.

    Never caught and papered over with synthetic data — callers must treat
    this as "no data available" and let validation/risk handle it.
    """


class MarketDataProvider(ABC):
    """A source of real OHLCV market data for a single symbol."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        """Fetch the most recent `output_size` bars for `symbol` at `timeframe`.

        Must raise MarketDataProviderError (not return partial/fabricated data)
        if the provider cannot fulfill the request.
        """
        raise NotImplementedError
