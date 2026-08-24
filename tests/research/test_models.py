"""Tests for `orion.research.models` -- construction invariants for economic
calendar shapes. Mirrors `orion.data.models`' own test discipline (checked
indirectly there via `OHLCVBar`'s validators exercised through the provider
tests) applied directly here since these shapes carry their own field
validators.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orion.research.models import (
    EconomicCalendarEvent,
    EconomicCalendarSnapshot,
    EconomicCalendarSourceMeta,
    ImpactLevel,
)

BASE_TIME = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> EconomicCalendarEvent:
    defaults: dict[str, object] = {
        "event_time": BASE_TIME,
        "country": "US",
        "event_name": "CPI YoY",
        "impact": ImpactLevel.HIGH,
        "actual": 3.1,
        "estimate": 3.0,
        "previous": 2.9,
        "unit": "%",
    }
    defaults.update(overrides)
    return EconomicCalendarEvent(**defaults)


def test_event_constructs_with_valid_fields() -> None:
    event = _event()
    assert event.impact is ImpactLevel.HIGH


def test_event_is_frozen() -> None:
    event = _event()
    with pytest.raises(ValidationError):
        event.country = "UK"  # type: ignore[misc]


def test_event_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        EconomicCalendarEvent(
            event_time=BASE_TIME,
            country="US",
            event_name="CPI YoY",
            impact=ImpactLevel.HIGH,
            unexpected="nope",
        )  # type: ignore[call-arg]


def test_event_rejects_naive_event_time() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _event(event_time=datetime(2026, 8, 24, 12, 0, 0))


def test_event_rejects_blank_country_or_event_name() -> None:
    with pytest.raises(ValidationError):
        _event(country="")
    with pytest.raises(ValidationError):
        _event(event_name="")


def test_source_meta_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        EconomicCalendarSourceMeta(
            provider="finnhub",
            query_start=datetime(2026, 8, 24),
            query_end=BASE_TIME,
            retrieved_at=BASE_TIME,
        )


def test_snapshot_defaults_to_empty_events() -> None:
    snapshot = EconomicCalendarSnapshot(
        source=EconomicCalendarSourceMeta(
            provider="finnhub",
            query_start=BASE_TIME,
            query_end=BASE_TIME,
            retrieved_at=BASE_TIME,
        )
    )
    assert snapshot.events == []
