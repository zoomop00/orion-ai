"""Round-tripping a dataset, and refusing one that does not match its manifest.

The build/load cycle has to be exact: a dataset's identity is computed from its
bars, so anything the CSV codec loses on the way to disk would change what the
dataset is called merely by being saved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from orion.backtest.data import CsvHistoricalBarSource
from orion.data.provider_base import MarketDataProviderError
from orion.datasets.builder import build_dataset, write_manifest
from orion.datasets.csv_store import read_bars_csv, write_bars_csv
from orion.datasets.loader import load_dataset, read_manifest
from orion.datasets.manifest import DatasetError, identity_for, manifest_for
from tests.datasets.conftest import (
    BASE_TIME,
    SYMBOL,
    TIMEFRAME,
    RaisingHistoricalProvider,
    StubHistoricalProvider,
    make_bars,
    make_snapshot,
)

RANGE_END = BASE_TIME + timedelta(days=1)


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "data" / "xauusd-1h.csv", tmp_path / "manifests" / "xauusd-1h.json"


def _build(tmp_path: Path, bars: list | None = None) -> tuple[Path, Path]:
    csv_path, manifest_path = _paths(tmp_path)
    provider = StubHistoricalProvider(make_snapshot(bars if bars is not None else make_bars()))
    build_dataset(
        provider=provider,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        start=BASE_TIME,
        end=RANGE_END,
        csv_path=csv_path,
        manifest_path=manifest_path,
    )
    return csv_path, manifest_path


# --- the round trip ---


def test_build_then_load_reproduces_the_bars_exactly(tmp_path: Path) -> None:
    bars = make_bars()
    csv_path, manifest_path = _build(tmp_path, bars)
    loaded = load_dataset(csv_path=csv_path, manifest_path=manifest_path)
    assert list(loaded.bars) == bars


def test_the_fingerprint_survives_the_disk_round_trip(tmp_path: Path) -> None:
    # The property the whole design rests on: writing and reading must not
    # change what the dataset is called.
    bars = make_bars()
    csv_path, manifest_path = _build(tmp_path, bars)
    loaded = load_dataset(csv_path=csv_path, manifest_path=manifest_path)
    assert loaded.manifest.identity == identity_for(SYMBOL, TIMEFRAME, bars)


def test_non_round_float_prices_round_trip_bit_for_bit(tmp_path: Path) -> None:
    # A formatted write (f"{x:.2f}") would pass a casual eyeball and silently
    # change every fingerprint. These prices have no exact 2-dp form.
    path = tmp_path / "bars.csv"
    bars = make_bars(6)
    write_bars_csv(path, bars)
    assert read_bars_csv(path) == bars


def test_a_bar_without_volume_round_trips_as_none(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    bars = [b.model_copy(update={"volume": None}) for b in make_bars(3)]
    write_bars_csv(path, bars)
    read = read_bars_csv(path)
    assert [b.volume for b in read] == [None, None, None]
    assert read == bars


def test_the_written_csv_is_readable_by_the_backtest_source(tmp_path: Path) -> None:
    # The duplication guard. `orion.datasets` cannot import `orion.backtest`,
    # so it has its own CSV reader; this proves the two agree on the same file
    # and that a built dataset is genuinely replayable.
    bars = make_bars()
    csv_path, _ = _build(tmp_path, bars)
    source = CsvHistoricalBarSource(csv_path, symbol=SYMBOL, timeframe=TIMEFRAME)
    snapshot = source.fetch_range(
        SYMBOL, TIMEFRAME, start=bars[0].timestamp, end=bars[-1].timestamp
    )
    assert snapshot.bars == bars
    assert read_bars_csv(csv_path) == snapshot.bars


def test_the_manifest_file_is_valid_json_and_reparses(tmp_path: Path) -> None:
    _, manifest_path = _build(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["identity"]["symbol"] == SYMBOL
    assert read_manifest(manifest_path).fingerprint == payload["fingerprint"]


def test_the_manifest_records_the_requested_range_as_provenance(tmp_path: Path) -> None:
    _, manifest_path = _build(tmp_path)
    manifest = read_manifest(manifest_path)
    assert manifest.requested_start == BASE_TIME
    assert manifest.requested_end == RANGE_END
    # ...while the identity records what actually arrived, which is narrower.
    assert manifest.identity.last_bar_timestamp < RANGE_END


# --- verification refuses a mismatch ---


def test_an_edited_price_is_rejected_on_load(tmp_path: Path) -> None:
    bars = make_bars()
    csv_path, manifest_path = _build(tmp_path, bars)
    tampered = list(bars)
    tampered[4] = bars[4].model_copy(update={"close": bars[4].close + 0.01})
    write_bars_csv(csv_path, tampered)
    with pytest.raises(DatasetError, match="bar contents differ"):
        load_dataset(csv_path=csv_path, manifest_path=manifest_path)


def test_a_removed_bar_is_rejected_on_load_and_named(tmp_path: Path) -> None:
    bars = make_bars()
    csv_path, manifest_path = _build(tmp_path, bars)
    write_bars_csv(csv_path, bars[:-1])
    with pytest.raises(DatasetError, match="bar count is 7, manifest says 8"):
        load_dataset(csv_path=csv_path, manifest_path=manifest_path)


def test_a_shifted_range_is_rejected_on_load(tmp_path: Path) -> None:
    bars = make_bars()
    csv_path, manifest_path = _build(tmp_path, bars)
    write_bars_csv(csv_path, make_bars(len(bars), start=BASE_TIME + timedelta(days=7)))
    with pytest.raises(DatasetError, match="first bar is"):
        load_dataset(csv_path=csv_path, manifest_path=manifest_path)


def test_a_hand_edited_manifest_fingerprint_is_rejected(tmp_path: Path) -> None:
    _, manifest_path = _build(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["fingerprint"] = "b" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetError, match="not a valid dataset manifest"):
        read_manifest(manifest_path)


def test_a_hand_edited_manifest_bar_count_is_rejected(tmp_path: Path) -> None:
    # Editing the identity invalidates the stored fingerprint, so the manifest
    # is refused before any bar is read.
    _, manifest_path = _build(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["identity"]["bar_count"] = 999
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetError, match="not a valid dataset manifest"):
        read_manifest(manifest_path)


def test_missing_files_fail_closed(tmp_path: Path) -> None:
    csv_path, manifest_path = _build(tmp_path)
    with pytest.raises(DatasetError, match="manifest not found"):
        load_dataset(csv_path=csv_path, manifest_path=tmp_path / "nope.json")
    csv_path.unlink()
    with pytest.raises(DatasetError, match="CSV not found"):
        load_dataset(csv_path=csv_path, manifest_path=manifest_path)


def test_a_malformed_csv_row_fails_closed_with_row_context(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T00:00:00+00:00,2400.0,2401.0,2399.0,2400.0,1.0\n"
        "2026-01-05T01:00:00+00:00,2400.0,2399.0,2401.0,2400.0,1.0\n",  # high < low
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="row 1"):
        read_bars_csv(path)


def test_a_naive_csv_timestamp_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-05T00:00:00,2400.0,2401.0,2399.0,2400.0,1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="no timezone"):
        read_bars_csv(path)


def test_a_csv_missing_a_required_column_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text("timestamp,open,high,low\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="missing required columns"):
        read_bars_csv(path)


# --- the build itself fails closed ---


def test_a_failed_fetch_writes_nothing(tmp_path: Path) -> None:
    csv_path, manifest_path = _paths(tmp_path)
    provider = RaisingHistoricalProvider(MarketDataProviderError("vendor is down"))
    with pytest.raises(MarketDataProviderError):
        build_dataset(
            provider=provider,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            start=BASE_TIME,
            end=RANGE_END,
            csv_path=csv_path,
            manifest_path=manifest_path,
        )
    assert not csv_path.exists()
    assert not manifest_path.exists()


def test_an_empty_range_writes_nothing(tmp_path: Path) -> None:
    csv_path, manifest_path = _paths(tmp_path)
    provider = StubHistoricalProvider(make_snapshot([]))
    with pytest.raises(DatasetError, match="zero bars"):
        build_dataset(
            provider=provider,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            start=BASE_TIME,
            end=RANGE_END,
            csv_path=csv_path,
            manifest_path=manifest_path,
        )
    assert not csv_path.exists()
    assert not manifest_path.exists()


def test_the_builder_never_fills_or_interpolates_a_gap(tmp_path: Path) -> None:
    # A vendor range with a genuine hole in it: the dataset must contain
    # exactly the bars that arrived, no more, and its bar count must say so.
    contiguous = make_bars(4)
    later = make_bars(4, start=BASE_TIME + timedelta(hours=20), start_price=2500.0)
    gapped = contiguous + later
    csv_path, manifest_path = _build(tmp_path, gapped)

    loaded = load_dataset(csv_path=csv_path, manifest_path=manifest_path)
    assert list(loaded.bars) == gapped
    assert loaded.manifest.identity.bar_count == 8
    timestamps = [b.timestamp for b in loaded.bars]
    assert timestamps[4] - timestamps[3] == timedelta(hours=17)  # the hole is preserved


def test_the_builder_passes_the_requested_range_through_untouched(tmp_path: Path) -> None:
    csv_path, manifest_path = _paths(tmp_path)
    provider = StubHistoricalProvider(make_snapshot(make_bars()))
    build_dataset(
        provider=provider,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        start=BASE_TIME,
        end=RANGE_END,
        csv_path=csv_path,
        manifest_path=manifest_path,
    )
    assert provider.calls == [(SYMBOL, TIMEFRAME, BASE_TIME, RANGE_END)]


def test_write_manifest_is_byte_stable(tmp_path: Path) -> None:
    manifest = manifest_for(
        identity_for(SYMBOL, TIMEFRAME, make_bars()),
        provider="twelvedata",
        retrieved_at=datetime(2026, 1, 6, tzinfo=UTC),
        requested_start=BASE_TIME,
        requested_end=RANGE_END,
        csv_filename="x.csv",
    )
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_manifest(first, manifest)
    write_manifest(second, manifest)
    assert first.read_bytes() == second.read_bytes()


def test_a_manifest_that_is_not_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(DatasetError, match="not valid JSON"):
        read_manifest(path)


def test_a_header_only_csv_fails_closed_against_its_manifest(tmp_path: Path) -> None:
    # An interrupted or truncated write leaves the header and no rows. The
    # manifest still claims eight bars, so the dataset is refused rather than
    # loaded as an empty one.
    csv_path, manifest_path = _build(tmp_path)
    csv_path.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="holds no bars"):
        load_dataset(csv_path=csv_path, manifest_path=manifest_path)


def test_naive_provenance_timestamps_are_rejected(tmp_path: Path) -> None:
    # Provenance is not hashed, but it is still recorded honestly: a timestamp
    # without a timezone does not say when anything happened.
    with pytest.raises(ValidationError, match="timezone-aware"):
        manifest_for(
            identity_for(SYMBOL, TIMEFRAME, make_bars()),
            provider="twelvedata",
            retrieved_at=BASE_TIME.replace(tzinfo=None),
            requested_start=BASE_TIME,
            requested_end=RANGE_END,
            csv_filename="x.csv",
        )


def test_a_truncated_csv_row_fails_closed(tmp_path: Path) -> None:
    # A row with fewer fields than the header: the missing values arrive as
    # None and must abort the read, never become a zero-priced bar.
    path = tmp_path / "bars.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-05T00:00:00+00:00,2400.0\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match="row 0"):
        read_bars_csv(path)
