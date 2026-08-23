from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
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
