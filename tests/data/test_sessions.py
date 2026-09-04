"""`TradingSession` narrows the GAP check; it never loosens it.

Every test here is written from that angle: the interesting cases are not the
weekend gap that is forgiven, but the four near-misses that are not — a gap of
identical duration in mid-session, a gap that starts at the close and runs
past the open, a gap that ends at the open but started mid-session, and a gap
spanning two closures.
"""

from __future__ import annotations

from calendar import Day
from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from orion.data.sessions import XAUUSD_WEEKLY, TradingSession

H1 = timedelta(hours=1)
M15 = timedelta(minutes=15)
D1 = timedelta(days=1)

# 2026-01-09 is a Friday; 2026-01-11 is the Sunday after it.
LAST_BAR_OF_WEEK = datetime(2026, 1, 9, 21, 0, tzinfo=UTC)  # H1 bar opening at 21:00
FIRST_BAR_OF_NEXT_WEEK = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)


def test_xauusd_weekly_is_friday_2200_to_sunday_2200_utc() -> None:
    assert XAUUSD_WEEKLY.close_weekday is Day.FRIDAY
    assert XAUUSD_WEEKLY.close_time == time(22, 0)
    assert XAUUSD_WEEKLY.open_weekday is Day.SUNDAY
    assert XAUUSD_WEEKLY.open_time == time(22, 0)


def test_session_is_frozen_and_closed() -> None:
    with pytest.raises(ValidationError):
        TradingSession(
            close_weekday=Day.FRIDAY,
            close_time=time(22, 0),
            open_weekday=Day.SUNDAY,
            open_time=time(22, 0),
            unexpected="x",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        XAUUSD_WEEKLY.close_time = time(21, 0)  # type: ignore[misc]


def test_timezone_aware_boundary_time_is_rejected() -> None:
    # A session is declared in UTC; a second timezone on the boundary itself
    # would make it ambiguous rather than more precise.
    with pytest.raises(ValidationError, match="naive time"):
        TradingSession(
            close_weekday=Day.FRIDAY,
            close_time=time(22, 0, tzinfo=UTC),
            open_weekday=Day.SUNDAY,
            open_time=time(22, 0),
        )


def test_zero_length_closure_is_rejected() -> None:
    with pytest.raises(ValidationError, match="zero-length closure"):
        TradingSession(
            close_weekday=Day.FRIDAY,
            close_time=time(22, 0),
            open_weekday=Day.FRIDAY,
            open_time=time(22, 0),
        )


# --- the closure that is forgiven ---


def test_weekend_gap_aligned_at_both_boundaries_is_an_expected_closure() -> None:
    assert XAUUSD_WEEKLY.is_expected_closure(LAST_BAR_OF_WEEK, FIRST_BAR_OF_NEXT_WEEK, H1)


def test_bar_timestamped_exactly_at_the_close_also_aligns() -> None:
    # A vendor that timestamps a bar at its close rather than its open lands
    # exactly on the boundary; the one-interval tolerance covers both
    # conventions rather than assuming one.
    assert XAUUSD_WEEKLY.is_expected_closure(
        datetime(2026, 1, 9, 22, 0, tzinfo=UTC), FIRST_BAR_OF_NEXT_WEEK, H1
    )


def test_daily_bars_across_the_weekend_are_an_expected_closure() -> None:
    # Friday and Monday D1 bars, one interval (a day) either side of the
    # declared boundary.
    assert XAUUSD_WEEKLY.is_expected_closure(
        datetime(2026, 1, 9, 22, 0, tzinfo=UTC), datetime(2026, 1, 12, 22, 0, tzinfo=UTC), D1
    )


# --- the near-misses that are not ---


def test_mid_session_gap_of_identical_duration_is_not_a_closure() -> None:
    # Exactly as long as the forgiven weekend gap, entirely inside the
    # trading week. Duration alone forgives nothing.
    weekend_length = FIRST_BAR_OF_NEXT_WEEK - LAST_BAR_OF_WEEK
    mid_week = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)  # a Tuesday
    assert not XAUUSD_WEEKLY.is_expected_closure(mid_week, mid_week + weekend_length, H1)


def test_gap_starting_at_the_close_but_running_past_the_open_is_not_a_closure() -> None:
    # A holiday that extends the weekend by a day: the close boundary matches,
    # the open boundary does not. Holidays are deliberately not modelled.
    assert not XAUUSD_WEEKLY.is_expected_closure(
        LAST_BAR_OF_WEEK, FIRST_BAR_OF_NEXT_WEEK + timedelta(days=1), H1
    )


def test_gap_ending_at_the_open_but_starting_mid_session_is_not_a_closure() -> None:
    # Missing bars from Friday morning onward, resuming correctly on Sunday.
    # One matching boundary is not enough.
    assert not XAUUSD_WEEKLY.is_expected_closure(
        datetime(2026, 1, 9, 8, 0, tzinfo=UTC), FIRST_BAR_OF_NEXT_WEEK, H1
    )


def test_gap_spanning_two_weekends_is_not_a_closure() -> None:
    # Boundary-aligned at both ends, but a fortnight apart: two closures and
    # a missing week, not one closure.
    assert not XAUUSD_WEEKLY.is_expected_closure(
        LAST_BAR_OF_WEEK, FIRST_BAR_OF_NEXT_WEEK + timedelta(days=7), H1
    )


def test_gap_starting_after_the_close_on_the_close_day_is_not_a_closure() -> None:
    # A bar timestamped past Friday 22:00 means the market did not close when
    # this session says it does. The next matching close is a week away, so
    # the gap cannot be that week's closure and must stay a finding.
    assert not XAUUSD_WEEKLY.is_expected_closure(
        datetime(2026, 1, 9, 23, 30, tzinfo=UTC), FIRST_BAR_OF_NEXT_WEEK, H1
    )


def test_tolerance_is_one_interval_so_m15_is_stricter_than_h1() -> None:
    # The same 45-minute-early last bar aligns at H1 and does not at M15 --
    # the tolerance is the caller's own bar interval, not a fixed duration.
    early = datetime(2026, 1, 9, 21, 15, tzinfo=UTC)
    assert XAUUSD_WEEKLY.is_expected_closure(early, FIRST_BAR_OF_NEXT_WEEK, H1)
    assert not XAUUSD_WEEKLY.is_expected_closure(early, FIRST_BAR_OF_NEXT_WEEK, M15)


def test_non_gap_and_reversed_ranges_are_never_a_closure() -> None:
    assert not XAUUSD_WEEKLY.is_expected_closure(LAST_BAR_OF_WEEK, LAST_BAR_OF_WEEK, H1)
    assert not XAUUSD_WEEKLY.is_expected_closure(FIRST_BAR_OF_NEXT_WEEK, LAST_BAR_OF_WEEK, H1)


# --- argument discipline ---


def test_naive_datetimes_are_rejected() -> None:
    naive = LAST_BAR_OF_WEEK.replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        XAUUSD_WEEKLY.is_expected_closure(naive, FIRST_BAR_OF_NEXT_WEEK, H1)
    with pytest.raises(ValueError, match="timezone-aware"):
        XAUUSD_WEEKLY.is_expected_closure(
            LAST_BAR_OF_WEEK, FIRST_BAR_OF_NEXT_WEEK.replace(tzinfo=None), H1
        )


def test_non_positive_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="interval must be positive"):
        XAUUSD_WEEKLY.is_expected_closure(LAST_BAR_OF_WEEK, FIRST_BAR_OF_NEXT_WEEK, timedelta(0))


def test_a_non_utc_offset_is_normalised_not_rejected() -> None:
    # The same instants, expressed in a +05:00 offset: an offset is a
    # representation, not a different moment.
    offset = timezone(timedelta(hours=5))
    assert XAUUSD_WEEKLY.is_expected_closure(
        LAST_BAR_OF_WEEK.astimezone(offset), FIRST_BAR_OF_NEXT_WEEK.astimezone(offset), H1
    )
