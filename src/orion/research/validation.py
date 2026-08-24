"""Data quality checks for economic calendar snapshots.

Mirrors `orion.data.validation`'s discipline exactly: this module never
repairs, reorders, or fabricates data — it only classifies what a snapshot
already contains. External research data is untrusted, so every finding here
is a reason for a downstream caller to distrust the snapshot; nothing in this
package decides what to do about a finding.

`EMPTY` is deliberately non-blocking, unlike `orion.data.validation`'s
identically-named `DataQualityIssue.EMPTY` (which always makes a bar snapshot
invalid). An OHLCV snapshot should never legitimately be empty — the market
always has a most-recent bar. An economic calendar for a narrow query window
can be genuinely, correctly empty (a quiet day with nothing scheduled), so
treating it as a blocking issue would falsely invalidate a large fraction of
real, correct provider responses. It is still recorded as a finding, since a
caller may want to distinguish "confirmed quiet" from "never checked."
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from orion.research.models import EconomicCalendarSnapshot

# Conventional starting point, not empirically calibrated — see
# docs/DECISIONS.md. A provider retrieval older than this is treated as too
# stale to trust for the same reason orion.data.validation.
# DEFAULT_MAX_STALENESS_BARS exists: an old snapshot silently reused is worse
# than an explicit, auditable rejection.
DEFAULT_MAX_RETRIEVAL_STALENESS = timedelta(hours=24)


class CalendarQualityIssue(StrEnum):
    EMPTY = "empty"
    DUPLICATE_EVENT = "duplicate_event"
    OUT_OF_RANGE = "out_of_range"
    STALE_RETRIEVAL = "stale_retrieval"
    FUTURE_RETRIEVAL = "future_retrieval"


# Issues that make a snapshot untrustworthy. EMPTY is deliberately excluded —
# see module docstring.
_BLOCKING_ISSUES = frozenset(
    {
        CalendarQualityIssue.DUPLICATE_EVENT,
        CalendarQualityIssue.OUT_OF_RANGE,
        CalendarQualityIssue.STALE_RETRIEVAL,
        CalendarQualityIssue.FUTURE_RETRIEVAL,
    }
)


class CalendarValidationFinding(BaseModel):
    issue: CalendarQualityIssue
    detail: str
    event_index: int | None = None


class CalendarValidationResult(BaseModel):
    is_valid: bool
    findings: list[CalendarValidationFinding] = []

    def has(self, issue: CalendarQualityIssue) -> bool:
        return any(f.issue is issue for f in self.findings)


def validate_calendar_snapshot(
    snapshot: EconomicCalendarSnapshot,
    *,
    now: datetime,
    max_retrieval_staleness: timedelta = DEFAULT_MAX_RETRIEVAL_STALENESS,
) -> CalendarValidationResult:
    """Classify data-quality issues in `snapshot`.

    `is_valid` is True whenever no *blocking* issue is found — `EMPTY` is
    recorded but never blocks (see module docstring). `now` is a required,
    caller-supplied parameter, matching every other timestamp-driven check in
    this codebase (`orion.data.validation.validate_snapshot`,
    `orion.risk.controls.evidence_is_fresh`): never `datetime.now()` read
    internally.
    """
    if now.tzinfo is None:
        raise ValueError(f"now ({now!r}) has no timezone; must be timezone-aware")

    findings: list[CalendarValidationFinding] = []
    source = snapshot.source

    if not snapshot.events:
        findings.append(
            CalendarValidationFinding(
                issue=CalendarQualityIssue.EMPTY, detail="snapshot has no events"
            )
        )

    retrieval_age = now - source.retrieved_at
    if retrieval_age < timedelta(0):
        findings.append(
            CalendarValidationFinding(
                issue=CalendarQualityIssue.FUTURE_RETRIEVAL,
                detail=f"retrieved_at ({source.retrieved_at}) is after now ({now})",
            )
        )
    elif retrieval_age > max_retrieval_staleness:
        findings.append(
            CalendarValidationFinding(
                issue=CalendarQualityIssue.STALE_RETRIEVAL,
                detail=(
                    f"retrieval is {retrieval_age} old; expected within {max_retrieval_staleness}"
                ),
            )
        )

    seen: set[tuple[datetime, str, str]] = set()
    for i, event in enumerate(snapshot.events):
        if event.event_time < source.query_start or event.event_time > source.query_end:
            findings.append(
                CalendarValidationFinding(
                    issue=CalendarQualityIssue.OUT_OF_RANGE,
                    detail=(
                        f"event_time {event.event_time} is outside the queried window "
                        f"[{source.query_start}, {source.query_end}]"
                    ),
                    event_index=i,
                )
            )

        key = (event.event_time, event.country, event.event_name)
        if key in seen:
            findings.append(
                CalendarValidationFinding(
                    issue=CalendarQualityIssue.DUPLICATE_EVENT,
                    detail=f"duplicate event {key!r} at index {i}",
                    event_index=i,
                )
            )
        seen.add(key)

    is_valid = not any(f.issue in _BLOCKING_ISSUES for f in findings)
    return CalendarValidationResult(is_valid=is_valid, findings=findings)
