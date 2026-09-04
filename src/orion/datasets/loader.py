"""Reading a dataset back, and refusing one that is not what it claims to be.

THE LOAD IS A VERIFICATION, NOT A READ. Bars are parsed, their identity is
recomputed from scratch, and the result is compared against the manifest's
fingerprint. A mismatch raises. This is the same posture `orion.memory.
sqlite_journal.SqliteDecisionJournal.get_evidence` already takes toward a
stored payload — re-hash what came back and refuse it if it no longer hashes to
the name it was filed under — applied to a file instead of a row.

WHY THAT MATTERS MORE HERE THAN IT LOOKS. The CSV is untracked and
re-downloadable, so the copy on any given machine may have been rebuilt from a
different range, edited by hand, half-written by an interrupted build, or
corrupted in transit. The manifest is the committed claim about what should be
there; verifying on load is what turns "the file is probably right" into "this
is provably the data that result was produced from."

IT RETURNS A PATH, NOT A BAR SOURCE. `orion.datasets` must not import
`orion.backtest` — a hygiene test asserts it — so this module cannot hand back
a `CsvHistoricalBarSource`. It returns the verified manifest, the bars, and the
CSV path, and the caller constructs whatever source it wants from that path.
That keeps acquisition and replay on opposite sides of the file boundary, which
is the property that makes a backtest reproducible in the first place.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from orion.data.models import OHLCVBar
from orion.datasets.csv_store import read_bars_csv
from orion.datasets.manifest import (
    DatasetError,
    DatasetIdentity,
    DatasetManifest,
    dataset_fingerprint,
    identity_for,
)


class LoadedDataset(BaseModel):
    """A verified dataset: its manifest, its bars, and where they live.

    `csv_path` is carried so a caller can construct a bar source over the same
    file this load verified, rather than re-deriving the path and risking
    pointing at a different one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: DatasetManifest
    bars: tuple[OHLCVBar, ...]
    csv_path: Path


def read_manifest(path: Path) -> DatasetManifest:
    """Parse a manifest file.

    Raises `DatasetError` if the file is missing, is not JSON, or does not
    satisfy `DatasetManifest` — which includes its own check that the stored
    fingerprint matches the identity it sits beside, so a hand-edited manifest
    is rejected here before any bar is read.
    """
    if not path.is_file():
        raise DatasetError(f"dataset manifest not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise DatasetError(f"{path} is not valid JSON: {exc}") from exc
    try:
        return DatasetManifest.model_validate(payload)
    except ValidationError as exc:
        raise DatasetError(f"{path} is not a valid dataset manifest: {exc}") from exc


def load_dataset(*, csv_path: Path, manifest_path: Path) -> LoadedDataset:
    """Read and verify the dataset described by `manifest_path`.

    Raises `DatasetError` if the manifest is unreadable, if the CSV is missing
    or malformed, or if the bars on disk do not reproduce the manifest's
    fingerprint. The error names which part disagreed, because "the data
    changed" and "the bar count changed" send a reader to different places.

    Nothing is repaired. A dataset that fails verification is not loaded with a
    warning — it is refused, exactly as a snapshot that fails
    `orion.data.validation.validate_snapshot` is refused rather than patched.

    WHAT THIS CANNOT CATCH, STATED PLAINLY. A CSV holds bars and nothing else —
    no symbol, no interval — so pairing one instrument's file with another's
    manifest is undetectable here whenever the bar contents happen to match.
    The identity is therefore derived using the manifest's own symbol and
    timeframe, and what is actually verified is that *these bars* are the bars
    the manifest names. Guarding the pairing itself would mean writing the
    symbol into every CSV row, which `orion.backtest.data.CsvHistoricalBarSource`
    does not read and this phase will not add unilaterally.
    """
    manifest = read_manifest(Path(manifest_path))
    bars = read_bars_csv(Path(csv_path))
    if not bars:
        raise DatasetError(
            f"{csv_path} holds no bars, but its manifest claims "
            f"{manifest.identity.bar_count}; the dataset is incomplete"
        )

    identity = identity_for(manifest.identity.symbol, manifest.identity.timeframe, bars)
    if identity == manifest.identity:
        return LoadedDataset(manifest=manifest, bars=tuple(bars), csv_path=Path(csv_path))

    raise DatasetError(
        f"{csv_path} does not match its manifest {manifest_path}: "
        f"{_describe_mismatch(manifest, identity)}. The dataset is refused rather than "
        "loaded -- a result computed from it could not honestly cite this fingerprint"
    )


def _describe_mismatch(manifest: DatasetManifest, actual: DatasetIdentity) -> str:
    """Name the first field that disagrees, for an error a reader can act on."""
    expected = manifest.identity
    if actual.bar_count != expected.bar_count:
        return f"bar count is {actual.bar_count}, manifest says {expected.bar_count}"
    if actual.first_bar_timestamp != expected.first_bar_timestamp:
        return (
            f"first bar is {actual.first_bar_timestamp.isoformat()}, manifest says "
            f"{expected.first_bar_timestamp.isoformat()}"
        )
    if actual.last_bar_timestamp != expected.last_bar_timestamp:
        return (
            f"last bar is {actual.last_bar_timestamp.isoformat()}, manifest says "
            f"{expected.last_bar_timestamp.isoformat()}"
        )
    # No symbol/timeframe branch: `load_dataset` derives the actual identity
    # using the manifest's own symbol and timeframe, so those two fields can
    # never disagree here. See that function's docstring for what that means --
    # it is a limitation of the CSV format, not an omission.
    return (
        f"bar contents differ (fingerprint {dataset_fingerprint(actual)[:16]}... vs "
        f"{manifest.fingerprint[:16]}...) while count and range match"
    )
