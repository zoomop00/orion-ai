"""Finnhub implementation of EconomicCalendarProvider.

Free-tier reference: https://finnhub.io/docs/api/economic-calendar
No fabricated fallback data — any failure raises EconomicCalendarProviderError.

`retrieved_at` is stamped with `datetime.now(UTC)` only here, at the live
provider I/O boundary — nowhere else in `orion.research` reads the wall
clock, matching `orion.data.twelvedata_provider.TwelveDataProvider`'s
identical discipline.

External research data is untrusted: every event in Finnhub's response is
parsed through `_to_event`, which raises on the first malformed value rather
than skipping it or substituting a guessed default — a partially-fabricated
calendar is worse than no calendar at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from orion.research.models import (
    EconomicCalendarEvent,
    EconomicCalendarSnapshot,
    EconomicCalendarSourceMeta,
    ImpactLevel,
)
from orion.research.provider_base import EconomicCalendarProvider, EconomicCalendarProviderError

# Finnhub sends an empty string, not an omitted field, for an unclassified
# event's impact. Anything else outside this map is a fail-closed parse
# error, not silently coerced into UNKNOWN.
_IMPACT_MAP = {
    "": ImpactLevel.UNKNOWN,
    "low": ImpactLevel.LOW,
    "medium": ImpactLevel.MEDIUM,
    "high": ImpactLevel.HIGH,
}


class _FinnhubEconomicEvent(BaseModel):
    time: str | None = None
    country: str | None = None
    event: str | None = None
    impact: str | None = None
    actual: float | None = None
    estimate: float | None = None
    prev: float | None = None
    unit: str | None = None


class _FinnhubCalendarResponse(BaseModel):
    economic_calendar: list[_FinnhubEconomicEvent] | None = Field(
        default=None, alias="economicCalendar"
    )
    error: str | None = None


class FinnhubEconomicCalendarProvider(EconomicCalendarProvider):
    """Fetches real economic calendar events from the Finnhub REST API."""

    def __init__(self, api_key: str, base_url: str, *, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise EconomicCalendarProviderError("FINNHUB_API_KEY is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def fetch_calendar(self, start: datetime, end: datetime) -> EconomicCalendarSnapshot:
        if start.tzinfo is None or end.tzinfo is None:
            raise EconomicCalendarProviderError("start/end must be timezone-aware")
        if end < start:
            raise EconomicCalendarProviderError(f"end ({end}) is before start ({start})")

        try:
            response = self._client.get(
                f"{self._base_url}/calendar/economic",
                params={
                    "from": start.date().isoformat(),
                    "to": end.date().isoformat(),
                    "token": self._api_key,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EconomicCalendarProviderError(f"Finnhub request failed: {exc}") from exc

        try:
            parsed = _FinnhubCalendarResponse.model_validate(response.json())
        except ValueError as exc:
            raise EconomicCalendarProviderError(
                f"Finnhub returned an unparseable response: {exc}"
            ) from exc

        if parsed.error is not None:
            raise EconomicCalendarProviderError(f"Finnhub error: {parsed.error}")
        if parsed.economic_calendar is None:
            raise EconomicCalendarProviderError("Finnhub response has no economicCalendar field")

        events = [self._to_event(raw) for raw in parsed.economic_calendar]

        return EconomicCalendarSnapshot(
            source=EconomicCalendarSourceMeta(
                provider="finnhub",
                query_start=start,
                query_end=end,
                retrieved_at=datetime.now(UTC),
            ),
            events=events,
        )

    @staticmethod
    def _to_event(raw: _FinnhubEconomicEvent) -> EconomicCalendarEvent:
        try:
            assert raw.time is not None
            assert raw.country is not None
            assert raw.event is not None
            assert raw.impact is not None
            event_time = datetime.fromisoformat(raw.time).replace(tzinfo=UTC)
            impact = _IMPACT_MAP[raw.impact]
            return EconomicCalendarEvent(
                event_time=event_time,
                country=raw.country,
                event_name=raw.event,
                impact=impact,
                actual=raw.actual,
                estimate=raw.estimate,
                previous=raw.prev,
                unit=raw.unit,
            )
        except (AssertionError, ValueError, TypeError, KeyError) as exc:
            raise EconomicCalendarProviderError(
                f"Finnhub returned an invalid economic calendar event: {raw!r} ({exc})"
            ) from exc
