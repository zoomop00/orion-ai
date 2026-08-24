"""Abstract interface every economic calendar provider must implement.

Mirrors `orion.data.provider_base.MarketDataProvider` exactly: the rest of
ORION depends only on this interface, never a specific provider's client or
response shape — swapping Finnhub for another provider later is a new
implementation file, not a pipeline rewrite. A separate, narrow ABC per
research data type (not one generic "research provider" superclass covering
news/macro/sentiment too) follows the same precedent `orion.backtest.data.
HistoricalBarSource` set for a structurally different query shape rather than
stretching an existing interface to cover it — news/macro/sentiment are not
implemented yet (Mission 7 v1 scope, see `docs/DECISIONS.md`), so a shared
base for them would be designed against a use case that does not exist yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from orion.research.models import EconomicCalendarSnapshot


class EconomicCalendarProviderError(RuntimeError):
    """Raised when a provider cannot return real economic calendar data.

    Never caught and papered over with synthetic/fabricated events — callers
    must treat this as "no data available" and let validation handle it.
    External research data is untrusted: a malformed or unparseable provider
    response is this error, not a best-effort partial result.
    """


class EconomicCalendarProvider(ABC):
    """A source of real economic calendar events for a date range."""

    @abstractmethod
    def fetch_calendar(self, start: datetime, end: datetime) -> EconomicCalendarSnapshot:
        """Fetch every scheduled/released economic event in `[start, end]`.

        `start`/`end` must be timezone-aware. Must raise
        `EconomicCalendarProviderError` (never return partial or fabricated
        data) if the provider cannot fulfil the request, including when
        `end < start`.
        """
        raise NotImplementedError
