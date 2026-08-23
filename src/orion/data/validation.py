"""Data quality checks: stale, missing (gapped), invalid, or out-of-order bars.

This module never repairs or fabricates data — it only classifies what a
snapshot already contains. Any finding here makes the snapshot untrustworthy
for a trading decision; the pipeline's job is to WAIT when that happens, not
to guess around bad data.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from orion.data.models import MarketSnapshot, Timeframe

TIMEFRAME_INTERVAL: dict[Timeframe, timedelta] = {
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}

# Shared with orion.risk.policy.RiskPolicy (via Settings) so data validation
# and the Risk Guardian's own staleness re-check agree on what "3 bars old"
# means without maintaining two independent copies of the same number. See
# docs/DECISIONS.md for why this specific figure was kept rather than guessed
# at again for the Risk Guardian.
DEFAULT_MAX_STALENESS_BARS: float = 3.0


class DataQualityIssue(StrEnum):
    EMPTY = "empty"
    STALE = "stale"
    GAP = "gap"
    NON_MONOTONIC = "non_monotonic"
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"


class ValidationFinding(BaseModel):
    issue: DataQualityIssue
    detail: str
    bar_index: int | None = None


class ValidationResult(BaseModel):
    is_valid: bool
    findings: list[ValidationFinding] = []

    def has(self, issue: DataQualityIssue) -> bool:
        return any(f.issue is issue for f in self.findings)


def validate_snapshot(
    snapshot: MarketSnapshot,
    *,
    now: datetime,
    max_staleness_bars: float = DEFAULT_MAX_STALENESS_BARS,
    gap_tolerance_bars: float = 1.5,
) -> ValidationResult:
    """Classify data-quality issues in `snapshot`.

    `is_valid` is True only when zero issues are found — any single finding
    (stale, gapped, out of order, duplicated, or empty) is enough reason for
    the pipeline to refuse to reason over this snapshot.
    """
    findings: list[ValidationFinding] = []
    bars = snapshot.bars

    if not bars:
        findings.append(
            ValidationFinding(issue=DataQualityIssue.EMPTY, detail="snapshot has no bars")
        )
        return ValidationResult(is_valid=False, findings=findings)

    interval = TIMEFRAME_INTERVAL[snapshot.source.timeframe]

    latest = bars[-1]
    age = now - latest.timestamp
    if age > interval * max_staleness_bars:
        findings.append(
            ValidationFinding(
                issue=DataQualityIssue.STALE,
                detail=f"latest bar is {age} old; expected within {interval * max_staleness_bars}",
                bar_index=len(bars) - 1,
            )
        )

    seen_timestamps: set[datetime] = set()
    for i, bar in enumerate(bars):
        if bar.timestamp in seen_timestamps:
            findings.append(
                ValidationFinding(
                    issue=DataQualityIssue.DUPLICATE_TIMESTAMP,
                    detail=f"duplicate timestamp {bar.timestamp} at bar {i}",
                    bar_index=i,
                )
            )
        seen_timestamps.add(bar.timestamp)

        if i == 0:
            continue
        prev = bars[i - 1]
        if bar.timestamp <= prev.timestamp:
            findings.append(
                ValidationFinding(
                    issue=DataQualityIssue.NON_MONOTONIC,
                    detail=(
                        f"bar {i} timestamp {bar.timestamp} does not follow "
                        f"bar {i - 1} timestamp {prev.timestamp}"
                    ),
                    bar_index=i,
                )
            )
            continue
        gap = bar.timestamp - prev.timestamp
        if gap > interval * gap_tolerance_bars:
            findings.append(
                ValidationFinding(
                    issue=DataQualityIssue.GAP,
                    detail=f"gap of {gap} between bar {i - 1} and bar {i}; expected ~{interval}",
                    bar_index=i,
                )
            )

    return ValidationResult(is_valid=not findings, findings=findings)
