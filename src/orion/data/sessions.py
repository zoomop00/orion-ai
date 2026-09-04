"""Declared weekly market closures, so a scheduled closure is not mistaken
for missing data.

`orion.data.validation.validate_snapshot` flags any inter-bar gap wider than
`gap_tolerance_bars` as `DataQualityIssue.GAP`. That is correct for an
instrument that trades continuously and wrong for one that does not: XAUUSD
closes for the weekend, so *every* real multi-week XAUUSD series contains a
~49-hour hole between two entirely healthy bars. Before this module the only
ways to accept such a series were to raise `gap_tolerance_bars` (which would
blind the check to genuinely missing bars everywhere, at every timeframe) or
to skip validation (which would blind it completely).

A `TradingSession` is the third option, and it makes the check *narrower*
rather than looser: a gap is forgiven only when it is precisely the closure
the caller declared — beginning at the declared close and ending at the
declared open, each within one bar interval. Duration alone forgives nothing.
A three-day hole in the middle of the trading week is still a GAP; so is a
weekend hole that runs a day too long.

**There is no default session anywhere.** `validate_snapshot`'s `session`
parameter defaults to `None`, which reproduces its previous behavior exactly,
and `XAUUSD_WEEKLY` below is data a caller opts into — never a threshold this
module applies on anyone's behalf. That mirrors `RiskPolicy`'s fail-closed
defaults: the permissive case must always be something a caller asked for in
writing.

**Holidays are deliberately not modelled.** A holiday calendar is external
data that can be wrong or stale, and a wrong one silently suppresses real
missing bars — the exact failure this codebase refuses everywhere else. A
holiday closure therefore still surfaces as a GAP finding, to be dealt with
explicitly. Only the *recurring, predictable* weekly closure is expressible
here.

**Times are UTC and fixed.** A session boundary quoted in a local exchange
timezone moves by an hour twice a year, and `zoneinfo` rules are external data
this module deliberately does not depend on. The one-interval tolerance
absorbs that shift at H4 and above; at M15 and H1 it does not, so a
DST-transition weekend will still be flagged. That is fail-closed and noisy
rather than silent and wrong, which is the correct direction to err — see
`docs/DECISIONS.md`.
"""

from __future__ import annotations

from calendar import Day
from datetime import UTC, datetime, time, timedelta

from pydantic import BaseModel, ConfigDict, model_validator


class TradingSession(BaseModel):
    """A recurring weekly market closure, declared in UTC.

    `calendar.Day` is reused rather than defining a parallel weekday enum:
    it is stdlib, it already agrees with `datetime.weekday()`, and a second
    enum that merely happens to agree today is exactly the kind of drift
    `orion.provenance` exists to prevent.

    Frozen and closed (`extra="forbid"`), matching every other schema in this
    codebase, so a session is a hashable value that a run record can carry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    close_weekday: Day
    close_time: time
    open_weekday: Day
    open_time: time

    @model_validator(mode="after")
    def _check_session_invariants(self) -> TradingSession:
        for name, value in (("close_time", self.close_time), ("open_time", self.open_time)):
            if value.tzinfo is not None:
                raise ValueError(
                    f"{name} must be a naive time; a TradingSession is declared in UTC and "
                    "attaching a second timezone to it would make the boundary ambiguous"
                )
        if self.close_weekday is self.open_weekday and self.close_time == self.open_time:
            raise ValueError(
                "close and open are the same instant; a zero-length closure would forgive "
                "nothing and is never what a caller means"
            )
        return self

    def is_expected_closure(
        self, previous: datetime, following: datetime, interval: timedelta
    ) -> bool:
        """True only if the gap between `previous` and `following` is exactly
        this declared closure.

        Both boundaries must match, each within one `interval`: the gap has to
        *begin* at the declared close and *end* at the declared open. A gap
        that is merely long, or that ends at the open but began mid-session,
        or that began at the close but ran past the open, is not a closure and
        must stay a `DataQualityIssue.GAP`.

        The one-interval tolerance on each side exists because a bar is
        timestamped at its own open, so the last bar before a close sits one
        interval before it — not because a boundary may drift.

        Pure function: no clock, no filesystem, no randomness. Naive datetimes
        are rejected rather than assumed to be UTC.
        """
        if interval <= timedelta(0):
            raise ValueError(f"interval must be positive, got {interval}")
        for name, value in (("previous", previous), ("following", following)):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        if following <= previous:
            return False

        previous_utc = previous.astimezone(UTC)
        following_utc = following.astimezone(UTC)

        closes_at = _next_occurrence(self.close_weekday, self.close_time, previous_utc)
        opens_at = _next_occurrence(self.open_weekday, self.open_time, closes_at)

        return (
            abs(closes_at - previous_utc) <= interval and abs(following_utc - opens_at) <= interval
        )


def _next_occurrence(weekday: Day, at: time, on_or_after: datetime) -> datetime:
    """The first UTC datetime at or after `on_or_after` landing on `weekday`
    at `at`."""
    candidate = datetime.combine(on_or_after.date(), at, tzinfo=UTC)
    candidate += timedelta(days=(weekday.value - candidate.weekday()) % 7)
    if candidate < on_or_after:
        candidate += timedelta(days=7)
    return candidate


#: The XAUUSD weekly closure: Friday 22:00 UTC to Sunday 22:00 UTC.
#:
#: Data a caller opts into, never a default this package applies — see the
#: module docstring. The specific hours are the conventional gold/FX week
#: (New York close to Sydney open) quoted in UTC under US winter time; the
#: one-interval tolerance is what absorbs the summer-time hour at H4 and
#: above. Nothing here is empirically derived from a fetched XAUUSD series,
#: because no such series exists in this repository yet — a caller who finds
#: this boundary disagrees with real vendor data should declare their own
#: `TradingSession` rather than widening the tolerance.
XAUUSD_WEEKLY = TradingSession(
    close_weekday=Day.FRIDAY,
    close_time=time(22, 0),
    open_weekday=Day.SUNDAY,
    open_time=time(22, 0),
)
