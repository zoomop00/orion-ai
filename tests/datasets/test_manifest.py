"""Dataset identity: what changes a fingerprint, and what must never.

The four load-bearing properties, each with its own test:
  1. identical bars      -> identical fingerprint
  2. any OHLCV/timestamp change -> different fingerprint
  3. provenance change   -> *same* fingerprint
  4. a manifest cannot claim a fingerprint its identity does not produce
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from orion.data.models import OHLCVBar, Timeframe
from orion.datasets.manifest import (
    DatasetError,
    DatasetIdentity,
    DatasetManifest,
    bars_hash,
    dataset_fingerprint,
    identity_for,
    manifest_for,
)
from tests.datasets.conftest import BASE_TIME, SYMBOL, TIMEFRAME, make_bars


def _identity(bars: list[OHLCVBar]) -> DatasetIdentity:
    return identity_for(SYMBOL, TIMEFRAME, bars)


def _fingerprint(bars: list[OHLCVBar]) -> str:
    return dataset_fingerprint(_identity(bars))


# --- 1. identical bars, identical fingerprint ---


def test_independently_built_identical_bars_fingerprint_identically() -> None:
    # Two separately constructed lists -- not the same objects -- because this
    # proves content equality, which is what a fingerprint must mean.
    assert _fingerprint(make_bars()) == _fingerprint(make_bars())


def test_fingerprint_is_stable_across_calls() -> None:
    bars = make_bars()
    assert _fingerprint(bars) == _fingerprint(bars)


def test_identity_records_the_delivered_range_not_a_requested_one() -> None:
    bars = make_bars(5)
    identity = _identity(bars)
    assert identity.first_bar_timestamp == bars[0].timestamp
    assert identity.last_bar_timestamp == bars[-1].timestamp
    assert identity.bar_count == 5


# --- 2. any content change, different fingerprint ---


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_changing_any_ohlcv_field_changes_the_fingerprint(field: str) -> None:
    bars = make_bars()
    original = _fingerprint(bars)

    edited = list(bars)
    bar = edited[3]
    # Nudge the field while keeping the bar internally coherent: widen the
    # range for high/low, shift open/close inside it.
    replacements = {
        "open": {"open": bar.open + 0.01},
        "high": {"high": bar.high + 0.01},
        "low": {"low": bar.low - 0.01},
        "close": {"close": bar.close - 0.01},
        "volume": {"volume": (bar.volume or 0.0) + 1.0},
    }
    edited[3] = bar.model_copy(update=replacements[field])
    assert _fingerprint(edited) != original, f"changing {field} did not change the fingerprint"


def test_changing_a_timestamp_changes_the_fingerprint() -> None:
    bars = make_bars()
    edited = list(bars)
    edited[2] = bars[2].model_copy(update={"timestamp": bars[2].timestamp + timedelta(seconds=1)})
    assert _fingerprint(edited) != _fingerprint(bars)


def test_reordering_bars_changes_the_fingerprint() -> None:
    # Order is part of the identity: the same bars in a different sequence are
    # a different dataset, and validate_snapshot would classify one of them
    # NON_MONOTONIC.
    bars = make_bars()
    swapped = list(bars)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    assert _fingerprint(swapped) != _fingerprint(bars)


def test_dropping_a_bar_changes_the_fingerprint() -> None:
    bars = make_bars()
    assert _fingerprint(bars[:-1]) != _fingerprint(bars)


def test_a_different_symbol_or_timeframe_changes_the_fingerprint() -> None:
    bars = make_bars()
    base = _fingerprint(bars)
    assert dataset_fingerprint(identity_for("EURUSD", TIMEFRAME, bars)) != base
    assert dataset_fingerprint(identity_for(SYMBOL, Timeframe.H4, bars)) != base


def test_bars_hash_alone_distinguishes_content() -> None:
    bars = make_bars()
    edited = list(bars)
    edited[0] = bars[0].model_copy(update={"close": bars[0].close + 0.5})
    assert bars_hash(edited) != bars_hash(bars)


# --- 3. provenance must not touch identity ---


def test_provenance_does_not_alter_the_fingerprint() -> None:
    bars = make_bars()
    identity = _identity(bars)

    first = manifest_for(
        identity,
        provider="twelvedata",
        retrieved_at=datetime(2026, 1, 6, 9, 0, tzinfo=UTC),
        requested_start=BASE_TIME,
        requested_end=BASE_TIME + timedelta(days=1),
        csv_filename="xauusd-1h.csv",
    )
    # A second acquisition of the same true history: different vendor name,
    # different retrieval time, different requested window, different filename.
    second = manifest_for(
        identity,
        provider="some-other-vendor",
        retrieved_at=datetime(2027, 5, 1, 17, 30, tzinfo=UTC),
        requested_start=BASE_TIME - timedelta(days=30),
        requested_end=BASE_TIME + timedelta(days=30),
        csv_filename="totally-different-name.csv",
    )

    assert first.fingerprint == second.fingerprint
    assert first.identity == second.identity
    assert first != second  # the manifests differ; the datasets do not


def test_the_fingerprint_function_cannot_be_handed_provenance() -> None:
    # Structural, not a convention: dataset_fingerprint takes a
    # DatasetIdentity, and a DatasetIdentity has no provenance fields to give
    # it. A manifest is not accepted at all.
    assert set(DatasetIdentity.model_fields) == {
        "symbol",
        "timeframe",
        "first_bar_timestamp",
        "last_bar_timestamp",
        "bar_count",
        "bars_hash",
    }


# --- 4. a manifest may not lie ---


def test_a_manifest_whose_fingerprint_disagrees_with_its_identity_is_rejected() -> None:
    # Constructed directly rather than via `model_copy`, which deliberately
    # does not re-run validators -- the check must fire on the path a manifest
    # actually arrives by: parsing a file, or being built.
    identity = _identity(make_bars())
    with pytest.raises(ValidationError, match="does not match this manifest's own identity"):
        DatasetManifest(
            identity=identity,
            fingerprint="0" * 64,
            provider="twelvedata",
            retrieved_at=BASE_TIME,
            requested_start=BASE_TIME,
            requested_end=BASE_TIME + timedelta(days=1),
            csv_filename="x.csv",
        )


def test_a_manifest_round_trips_through_json_with_its_fingerprint_rechecked() -> None:
    manifest = manifest_for(
        _identity(make_bars()),
        provider="twelvedata",
        retrieved_at=BASE_TIME,
        requested_start=BASE_TIME,
        requested_end=BASE_TIME + timedelta(days=1),
        csv_filename="x.csv",
    )
    assert DatasetManifest.model_validate(manifest.model_dump(mode="json")) == manifest


# --- degenerate inputs fail closed ---


def test_zero_bars_is_not_a_dataset() -> None:
    with pytest.raises(DatasetError, match="zero bars"):
        identity_for(SYMBOL, TIMEFRAME, [])


def test_identity_rejects_a_reversed_range() -> None:
    with pytest.raises(ValidationError, match="is before"):
        DatasetIdentity(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            first_bar_timestamp=BASE_TIME + timedelta(days=1),
            last_bar_timestamp=BASE_TIME,
            bar_count=2,
            bars_hash="a" * 64,
        )


def test_identity_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        DatasetIdentity(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            first_bar_timestamp=BASE_TIME.replace(tzinfo=None),
            last_bar_timestamp=BASE_TIME.replace(tzinfo=None),
            bar_count=1,
            bars_hash="a" * 64,
        )


def test_identity_and_manifest_are_frozen_and_closed() -> None:
    identity = _identity(make_bars())
    with pytest.raises(ValidationError):
        identity.bar_count = 99  # type: ignore[misc]
    with pytest.raises(ValidationError):
        DatasetIdentity(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            first_bar_timestamp=BASE_TIME,
            last_bar_timestamp=BASE_TIME,
            bar_count=1,
            bars_hash="a" * 64,
            surprise="x",  # type: ignore[call-arg]
        )
