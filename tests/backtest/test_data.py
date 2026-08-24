"""Tests for `orion.backtest.data` -- historical bar sources.

Covers: correct range slicing, symbol/timeframe mismatch, `end < start`,
a well-formed-but-empty range (not an error), never reordering/repairing
out-of-order or duplicate-timestamp data, and every `CsvHistoricalBarSource`
fail-closed path (missing file, missing column, malformed row, naive
timestamp).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from orion.backtest.data import (
    CsvHistoricalBarSource,
    HistoricalDataError,
    InMemoryHistoricalBarSource,
)
from orion.data.models import Timeframe
from tests.backtest.conftest import H1, SYMBOL, make_uptrend_bars

OTHER_SYMBOL = "EURUSD"


# --- InMemoryHistoricalBarSource ---


def test_fetch_range_returns_bars_within_the_inclusive_range() -> None:
    bars = make_uptrend_bars(10)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    snapshot = source.fetch_range(
        SYMBOL, Timeframe.H1, start=bars[2].timestamp, end=bars[5].timestamp
    )
    assert [b.timestamp for b in snapshot.bars] == [b.timestamp for b in bars[2:6]]


def test_fetch_range_boundaries_are_inclusive() -> None:
    bars = make_uptrend_bars(3)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    snapshot = source.fetch_range(
        SYMBOL, Timeframe.H1, start=bars[0].timestamp, end=bars[0].timestamp
    )
    assert len(snapshot.bars) == 1
    assert snapshot.bars[0].timestamp == bars[0].timestamp


def test_fetch_range_outside_dataset_returns_empty_snapshot_not_an_error() -> None:
    bars = make_uptrend_bars(5)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    far_future_start = bars[-1].timestamp + H1 * 100
    far_future_end = far_future_start + H1 * 10
    snapshot = source.fetch_range(SYMBOL, Timeframe.H1, start=far_future_start, end=far_future_end)
    assert snapshot.bars == []


def test_fetch_range_wrong_symbol_raises() -> None:
    bars = make_uptrend_bars(5)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    with pytest.raises(HistoricalDataError, match="EURUSD"):
        source.fetch_range(
            OTHER_SYMBOL, Timeframe.H1, start=bars[0].timestamp, end=bars[-1].timestamp
        )


def test_fetch_range_wrong_timeframe_raises() -> None:
    bars = make_uptrend_bars(5)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    with pytest.raises(HistoricalDataError):
        source.fetch_range(SYMBOL, Timeframe.H4, start=bars[0].timestamp, end=bars[-1].timestamp)


def test_fetch_range_end_before_start_raises() -> None:
    bars = make_uptrend_bars(5)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    with pytest.raises(HistoricalDataError):
        source.fetch_range(SYMBOL, Timeframe.H1, start=bars[3].timestamp, end=bars[0].timestamp)


def test_fetch_range_never_reorders_out_of_order_bars() -> None:
    """An intentionally out-of-order dataset (e.g. constructed to test
    `validate_snapshot`'s NON_MONOTONIC fail-closed path downstream) must
    come back exactly as given -- silently sorting it here would hide a
    genuinely bad dataset instead of surfacing it."""
    ordered = make_uptrend_bars(5)
    scrambled = [ordered[2], ordered[0], ordered[4], ordered[1], ordered[3]]
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=scrambled)
    snapshot = source.fetch_range(
        SYMBOL, Timeframe.H1, start=ordered[0].timestamp, end=ordered[4].timestamp
    )
    assert [b.timestamp for b in snapshot.bars] == [b.timestamp for b in scrambled]


def test_fetch_range_never_deduplicates_duplicate_timestamps() -> None:
    bars = make_uptrend_bars(3)
    duplicated = [bars[0], bars[0], bars[1], bars[2]]
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=duplicated)
    snapshot = source.fetch_range(
        SYMBOL, Timeframe.H1, start=bars[0].timestamp, end=bars[2].timestamp
    )
    assert len(snapshot.bars) == 4


# --- CsvHistoricalBarSource ---

_CSV_HEADER = "timestamp,open,high,low,close,volume\n"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(_CSV_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_csv_source_loads_and_slices_correctly(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    t1 = t0 + H1
    rows = [
        f"{t0.isoformat()},2400.0,2402.0,2399.0,2401.0,1000",
        f"{t1.isoformat()},2401.0,2404.0,2400.5,2403.0,1100",
    ]
    csv_path = _write_csv(tmp_path / "bars.csv", rows)
    source = CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=Timeframe.H1)
    snapshot = source.fetch_range(SYMBOL, Timeframe.H1, start=t0, end=t1)
    assert len(snapshot.bars) == 2
    assert snapshot.bars[0].close == 2401.0
    assert snapshot.bars[1].close == 2403.0


def test_csv_source_missing_volume_column_is_tolerated(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    csv_path = tmp_path / "no_volume.csv"
    csv_path.write_text(
        f"timestamp,open,high,low,close\n{t0.isoformat()},2400.0,2402.0,2399.0,2401.0\n",
        encoding="utf-8",
    )
    source = CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=Timeframe.H1)
    snapshot = source.fetch_range(SYMBOL, Timeframe.H1, start=t0, end=t0)
    assert snapshot.bars[0].volume is None


def test_csv_source_missing_file_raises() -> None:
    with pytest.raises(HistoricalDataError):
        CsvHistoricalBarSource("does/not/exist.csv", symbol=SYMBOL, timeframe=Timeframe.H1)


def test_csv_source_missing_required_column_raises(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "timestamp,open,high,close\n2026-01-01T00:00:00+00:00,1,2,3\n", encoding="utf-8"
    )
    with pytest.raises(HistoricalDataError, match="missing required columns"):
        CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=Timeframe.H1)


def test_csv_source_malformed_row_raises_with_row_context(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    csv_path = _write_csv(tmp_path / "malformed.csv", [f"{t0.isoformat()},not-a-number,2,1,1.5,0"])
    with pytest.raises(HistoricalDataError, match="row 0"):
        CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=Timeframe.H1)


def test_csv_source_incoherent_ohlc_row_raises(tmp_path: Path) -> None:
    """`OHLCVBar`'s own validator (high < low) fires during row parsing and
    is wrapped as a `HistoricalDataError`, not left as a raw ValidationError."""
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    csv_path = _write_csv(tmp_path / "incoherent.csv", [f"{t0.isoformat()},2400,2390,2399,2401,0"])
    with pytest.raises(HistoricalDataError):
        CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=Timeframe.H1)


def test_csv_source_naive_timestamp_raises(tmp_path: Path) -> None:
    csv_path = _write_csv(tmp_path / "naive.csv", ["2026-01-01T00:00:00,2400,2402,2399,2401,0"])
    with pytest.raises(HistoricalDataError, match="timezone"):
        CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=Timeframe.H1)
