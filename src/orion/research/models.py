"""Data shapes for external research data: economic calendar events.

Mission 7 (`ORION_MASTER_ENGINEERING_BRIEF_V2.md` §13 — RESEARCH) eventually
covers news, economic calendar, macro data, and sentiment. This module ships
the first of those: the economic calendar. It is evidence, not decision
input — nothing here is wired into `orion.analysis`, `orion.reasoning`,
`orion.decision`, or `orion.risk` in this milestone. See `docs/DECISIONS.md`
for the approved Mission 7 v1 scope.

`EconomicCalendarSourceMeta` mirrors `orion.data.models.DataSourceMeta`'s
provenance shape (provider, retrieved_at) plus the query window the provider
was actually asked for — required so `orion.research.validation` can detect
a provider returning an event outside the range it claims to have queried.

External research data is untrusted: every field on `EconomicCalendarEvent`
is populated only from a real provider response, validated at construction
(`impact` is a closed enum; `event_time` must be timezone-aware), and nothing
in this module fabricates or repairs a malformed value.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImpactLevel(StrEnum):
    """Closed vocabulary for a calendar event's expected market impact.

    `UNKNOWN` is a distinct, explicit member for a provider response that
    declines to classify an event (e.g. an empty impact field) — never a
    silent default guessed by this module. Any impact string a provider
    sends that is not one of these four values is a fail-closed parse error
    at the provider boundary (`orion.research.finnhub_provider`), not
    coerced into `UNKNOWN`.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class EconomicCalendarEvent(BaseModel):
    """One immutable scheduled/released economic data point."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_time: datetime
    country: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    impact: ImpactLevel
    actual: float | None = None
    estimate: float | None = None
    previous: float | None = None
    unit: str | None = None

    @field_validator("event_time")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"event_time {value!r} has no timezone; must be timezone-aware")
        return value


class EconomicCalendarSourceMeta(BaseModel):
    """Where an economic calendar snapshot came from, the window it was
    queried for, and when it was retrieved — required for audit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    query_start: datetime
    query_end: datetime
    retrieved_at: datetime

    @field_validator("query_start", "query_end", "retrieved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{value!r} has no timezone; must be timezone-aware")
        return value


class EconomicCalendarSnapshot(BaseModel):
    """A validated-shape (not yet quality-validated) batch of calendar
    events plus provenance. See `orion.research.validation` for the
    calendar-specific quality checks this shape does not itself enforce
    (duplicates, out-of-range events, stale retrieval)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: EconomicCalendarSourceMeta
    events: list[EconomicCalendarEvent] = Field(default_factory=list)
