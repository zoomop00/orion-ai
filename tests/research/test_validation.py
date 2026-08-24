from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.research.models import (
    EconomicCalendarEvent,
    EconomicCalendarSnapshot,
    EconomicCalendarSourceMeta,
    ImpactLevel,
)
from orion.research.validation import CalendarQualityIssue, validate_calendar_snapshot

QUERY_START = datetime(2026, 8, 24, 0, 0, 0, tzinfo=UTC)
QUERY_END = datetime(2026, 8, 24, 23, 59, 59, tzinfo=UTC)


def _event(
    hours_offset: int, *, country: str = "US", name: str = "CPI YoY"
) -> EconomicCalendarEvent:
    return EconomicCalendarEvent(
        event_time=QUERY_START + timedelta(hours=hours_offset),
        country=country,
        event_name=name,
        impact=ImpactLevel.MEDIUM,
    )


def _snapshot(
    events: list[EconomicCalendarEvent],
    *,
    retrieved_at: datetime = QUERY_END,
    query_start: datetime = QUERY_START,
    query_end: datetime = QUERY_END,
) -> EconomicCalendarSnapshot:
    return EconomicCalendarSnapshot(
        source=EconomicCalendarSourceMeta(
            provider="finnhub",
            query_start=query_start,
            query_end=query_end,
            retrieved_at=retrieved_at,
        ),
        events=events,
    )


def test_empty_snapshot_is_valid_but_flagged() -> None:
    result = validate_calendar_snapshot(_snapshot([]), now=QUERY_END)
    assert result.is_valid
    assert result.has(CalendarQualityIssue.EMPTY)


def test_clean_snapshot_is_valid_with_no_findings() -> None:
    events = [_event(1), _event(2, name="Retail Sales")]
    result = validate_calendar_snapshot(_snapshot(events), now=QUERY_END)
    assert result.is_valid
    assert result.findings == []


def test_duplicate_event_is_flagged_and_blocking() -> None:
    events = [_event(1), _event(1)]
    result = validate_calendar_snapshot(_snapshot(events), now=QUERY_END)
    assert not result.is_valid
    assert result.has(CalendarQualityIssue.DUPLICATE_EVENT)


def test_event_outside_queried_window_is_flagged_and_blocking() -> None:
    events = [
        _event(1),
        EconomicCalendarEvent(
            event_time=QUERY_END + timedelta(days=1),
            country="US",
            event_name="Out of range",
            impact=ImpactLevel.LOW,
        ),
    ]
    result = validate_calendar_snapshot(_snapshot(events), now=QUERY_END)
    assert not result.is_valid
    assert result.has(CalendarQualityIssue.OUT_OF_RANGE)


def test_stale_retrieval_is_flagged_and_blocking() -> None:
    now = QUERY_END + timedelta(hours=48)
    result = validate_calendar_snapshot(_snapshot([_event(1)], retrieved_at=QUERY_END), now=now)
    assert not result.is_valid
    assert result.has(CalendarQualityIssue.STALE_RETRIEVAL)


def test_future_retrieval_is_flagged_and_blocking() -> None:
    now = QUERY_END - timedelta(hours=1)
    result = validate_calendar_snapshot(_snapshot([_event(1)], retrieved_at=QUERY_END), now=now)
    assert not result.is_valid
    assert result.has(CalendarQualityIssue.FUTURE_RETRIEVAL)


def test_now_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        validate_calendar_snapshot(_snapshot([_event(1)]), now=datetime(2026, 8, 24))


def test_retrieval_exactly_at_staleness_boundary_is_valid() -> None:
    now = QUERY_END + timedelta(hours=24)
    result = validate_calendar_snapshot(_snapshot([_event(1)], retrieved_at=QUERY_END), now=now)
    assert result.is_valid
