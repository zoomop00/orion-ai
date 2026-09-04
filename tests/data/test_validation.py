from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.data.sessions import XAUUSD_WEEKLY
from orion.data.validation import DataQualityIssue, validate_snapshot

BASE_TIME = datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC)


def _bar(minutes_offset: int, price: float = 2400.0) -> OHLCVBar:
    return OHLCVBar(
        timestamp=BASE_TIME + timedelta(minutes=minutes_offset),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100.0,
    )


def _snapshot(bars: list[OHLCVBar], timeframe: Timeframe = Timeframe.M15) -> MarketSnapshot:
    return MarketSnapshot(
        source=DataSourceMeta(
            provider="test",
            symbol="XAUUSD",
            timeframe=timeframe,
            retrieved_at=BASE_TIME,
        ),
        bars=bars,
    )


def test_empty_snapshot_is_invalid() -> None:
    result = validate_snapshot(_snapshot([]), now=BASE_TIME)
    assert not result.is_valid
    assert result.has(DataQualityIssue.EMPTY)


def test_clean_consecutive_bars_are_valid() -> None:
    bars = [_bar(0), _bar(15), _bar(30), _bar(45)]
    result = validate_snapshot(_snapshot(bars), now=BASE_TIME + timedelta(minutes=45))
    assert result.is_valid
    assert result.findings == []


def test_stale_latest_bar_is_flagged() -> None:
    bars = [_bar(0), _bar(15), _bar(30)]
    now = BASE_TIME + timedelta(hours=6)  # far beyond max_staleness_bars * 15min
    result = validate_snapshot(_snapshot(bars), now=now)
    assert not result.is_valid
    assert result.has(DataQualityIssue.STALE)


def test_gap_between_bars_is_flagged() -> None:
    bars = [_bar(0), _bar(15), _bar(90)]  # large jump from bar 1 to bar 2
    result = validate_snapshot(_snapshot(bars), now=BASE_TIME + timedelta(minutes=90))
    assert not result.is_valid
    assert result.has(DataQualityIssue.GAP)


def test_out_of_order_bars_are_flagged() -> None:
    bars = [_bar(30), _bar(15), _bar(45)]
    result = validate_snapshot(_snapshot(bars), now=BASE_TIME + timedelta(minutes=45))
    assert not result.is_valid
    assert result.has(DataQualityIssue.NON_MONOTONIC)


def test_duplicate_timestamps_are_flagged() -> None:
    bars = [_bar(0), _bar(15), _bar(15)]
    result = validate_snapshot(_snapshot(bars), now=BASE_TIME + timedelta(minutes=15))
    assert not result.is_valid
    assert result.has(DataQualityIssue.DUPLICATE_TIMESTAMP)


# --- sessions: the GAP check narrows, it never loosens ---
#
# Every test below uses H1 bars around the real XAUUSD weekly closure
# (Friday 22:00 UTC to Sunday 22:00 UTC). 2026-01-09 is a Friday.

WEEK_LAST_BAR = datetime(2026, 1, 9, 21, 0, tzinfo=UTC)
NEXT_WEEK_FIRST_BAR = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)
WEEKEND = NEXT_WEEK_FIRST_BAR - WEEK_LAST_BAR


def _h1_bars(timestamps: list[datetime]) -> list[OHLCVBar]:
    return [
        OHLCVBar(timestamp=ts, open=2400.0, high=2401.0, low=2399.0, close=2400.0, volume=100.0)
        for ts in timestamps
    ]


def _h1_snapshot(timestamps: list[datetime]) -> MarketSnapshot:
    return MarketSnapshot(
        source=DataSourceMeta(
            provider="test",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            retrieved_at=timestamps[-1],
        ),
        bars=_h1_bars(timestamps),
    )


def test_weekend_gap_is_a_finding_without_a_session() -> None:
    # The default path is unchanged: this is exactly why no real XAUUSD
    # dataset could be validated before sessions existed.
    snapshot = _h1_snapshot(
        [WEEK_LAST_BAR - timedelta(hours=1), WEEK_LAST_BAR, NEXT_WEEK_FIRST_BAR]
    )
    result = validate_snapshot(snapshot, now=NEXT_WEEK_FIRST_BAR)
    assert not result.is_valid
    assert result.has(DataQualityIssue.GAP)


def test_weekend_gap_is_accepted_with_the_declared_session() -> None:
    snapshot = _h1_snapshot(
        [WEEK_LAST_BAR - timedelta(hours=1), WEEK_LAST_BAR, NEXT_WEEK_FIRST_BAR]
    )
    result = validate_snapshot(snapshot, now=NEXT_WEEK_FIRST_BAR, session=XAUUSD_WEEKLY)
    assert result.is_valid
    assert result.findings == []


def test_mid_session_gap_of_identical_duration_remains_a_gap() -> None:
    # The same 49-hour hole, moved into the middle of the trading week.
    start = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)  # a Tuesday
    snapshot = _h1_snapshot([start - timedelta(hours=1), start, start + WEEKEND])
    result = validate_snapshot(snapshot, now=start + WEEKEND, session=XAUUSD_WEEKLY)
    assert not result.is_valid
    assert result.has(DataQualityIssue.GAP)


def test_boundary_aligned_but_over_long_gap_remains_a_gap() -> None:
    # Starts at the declared close, runs a day past the declared open --
    # a holiday-extended weekend, which this codebase deliberately does not
    # model away.
    late = NEXT_WEEK_FIRST_BAR + timedelta(days=1)
    snapshot = _h1_snapshot([WEEK_LAST_BAR - timedelta(hours=1), WEEK_LAST_BAR, late])
    result = validate_snapshot(snapshot, now=late, session=XAUUSD_WEEKLY)
    assert not result.is_valid
    assert result.has(DataQualityIssue.GAP)


def test_a_session_never_suppresses_a_second_unrelated_gap() -> None:
    # One forgiven weekend and one genuine mid-week hole in the same series:
    # the session must forgive exactly one of them.
    later = NEXT_WEEK_FIRST_BAR + timedelta(hours=1)
    snapshot = _h1_snapshot(
        [WEEK_LAST_BAR, NEXT_WEEK_FIRST_BAR, later, later + timedelta(hours=12)]
    )
    result = validate_snapshot(snapshot, now=later + timedelta(hours=12), session=XAUUSD_WEEKLY)
    assert not result.is_valid
    gaps = [f for f in result.findings if f.issue is DataQualityIssue.GAP]
    assert len(gaps) == 1
    assert gaps[0].bar_index == 3


def test_a_session_does_not_affect_staleness_ordering_duplicates_or_emptiness() -> None:
    # A session narrows one check and touches no other.
    assert validate_snapshot(
        _h1_snapshot([WEEK_LAST_BAR]),
        now=WEEK_LAST_BAR + timedelta(days=30),
        session=XAUUSD_WEEKLY,
    ).has(DataQualityIssue.STALE)

    scrambled = [WEEK_LAST_BAR, WEEK_LAST_BAR - timedelta(hours=1)]
    assert validate_snapshot(_h1_snapshot(scrambled), now=WEEK_LAST_BAR, session=XAUUSD_WEEKLY).has(
        DataQualityIssue.NON_MONOTONIC
    )

    assert validate_snapshot(
        _h1_snapshot([WEEK_LAST_BAR, WEEK_LAST_BAR]),
        now=WEEK_LAST_BAR,
        session=XAUUSD_WEEKLY,
    ).has(DataQualityIssue.DUPLICATE_TIMESTAMP)

    empty = MarketSnapshot(
        source=DataSourceMeta(
            provider="test", symbol="XAUUSD", timeframe=Timeframe.H1, retrieved_at=WEEK_LAST_BAR
        ),
        bars=[],
    )
    assert validate_snapshot(empty, now=WEEK_LAST_BAR, session=XAUUSD_WEEKLY).has(
        DataQualityIssue.EMPTY
    )


def test_session_none_is_byte_identical_to_omitting_it() -> None:
    # The characterization guarantee the whole change rests on.
    for timestamps in (
        [WEEK_LAST_BAR - timedelta(hours=1), WEEK_LAST_BAR, NEXT_WEEK_FIRST_BAR],
        [WEEK_LAST_BAR, WEEK_LAST_BAR + timedelta(hours=1)],
        [WEEK_LAST_BAR, WEEK_LAST_BAR],
    ):
        snapshot = _h1_snapshot(timestamps)
        now = max(timestamps)
        assert validate_snapshot(snapshot, now=now) == validate_snapshot(
            snapshot, now=now, session=None
        )
