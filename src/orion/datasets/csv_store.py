"""Reading and writing the CSV file a dataset's bars live in.

THE FORMAT IS NOT THIS MODULE'S TO CHOOSE. `orion.backtest.data.
CsvHistoricalBarSource` already reads a specific shape — a header row, the
columns `timestamp,open,high,low,close` with `volume` optional, and
timezone-aware ISO-8601 timestamps — and it is the consumer this file exists
for. What is written here is written to be read by that class.

WHY THE READER IS DUPLICATED RATHER THAN IMPORTED. `orion.backtest.data`'s own
parser is `_load_bars_from_csv`, private by the leading underscore, and this
codebase does not reach across a package's privacy boundary — the same
reasoning `orion.backtest.execution` records for its deliberate duplication of
`orion.paper.execution`'s fill-price helpers. More decisively, `orion.datasets`
must not import `orion.backtest` at all: a hygiene test asserts it, and the
whole point of the file boundary is that acquisition and replay never meet in a
process. So the read is duplicated, consciously and minimally — and a test
loads one file through both readers and asserts the bars are identical, so the
duplication cannot drift in silence.

FLOAT ROUND-TRIPPING IS A CORRECTNESS REQUIREMENT, NOT A STYLE CHOICE. A
dataset's fingerprint is computed from its bars, so a price that does not
survive the write/read cycle exactly would change a dataset's identity merely
by being saved. `repr` of a Python float has round-tripped exactly since 3.1;
a formatted string such as `f"{price:.2f}"` would not, and would silently
corrupt the identity of every dataset written with it. Nothing here formats a
float.

Nothing in this module repairs, reorders, deduplicates, interpolates or
back-fills a bar. A file holds the bars it was given, in the order it was given
them.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from orion.data.models import OHLCVBar
from orion.datasets.manifest import DatasetError

#: The header written, in this order. `volume` is always present as a column;
#: a bar without one writes an empty field, which `CsvHistoricalBarSource`
#: already reads back as `None`.
CSV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")

_REQUIRED_CSV_COLUMNS = frozenset({"timestamp", "open", "high", "low", "close"})


def write_bars_csv(path: Path, bars: Sequence[OHLCVBar]) -> None:
    """Write `bars` to `path`, oldest first, in the shape
    `orion.backtest.data.CsvHistoricalBarSource` reads.

    `newline=""` is required by the `csv` module on Windows; without it every
    row gains a stray blank line, which would not change the parsed bars but
    would make the file needlessly different from itself between platforms.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for bar in bars:
            writer.writerow(
                (
                    bar.timestamp.isoformat(),
                    repr(bar.open),
                    repr(bar.high),
                    repr(bar.low),
                    repr(bar.close),
                    "" if bar.volume is None else repr(bar.volume),
                )
            )


def read_bars_csv(path: Path) -> list[OHLCVBar]:
    """Read every bar from `path`, in file order.

    Raises `DatasetError` for a missing file, a missing required column, or any
    row that is not a valid `OHLCVBar` — eagerly, naming the row, rather than
    letting a malformed row surface later as a confusing failure inside
    whatever was going to use the bars.
    """
    if not path.is_file():
        raise DatasetError(f"dataset CSV not found: {path}")

    bars: list[OHLCVBar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = _REQUIRED_CSV_COLUMNS - fieldnames
        if missing:
            raise DatasetError(
                f"{path} is missing required columns {sorted(missing)}; found {sorted(fieldnames)}"
            )
        for row_index, row in enumerate(reader):
            bars.append(_row_to_bar(path, row_index, row))
    return bars


def _row_to_bar(path: Path, row_index: int, row: dict[str, str | None]) -> OHLCVBar:
    try:
        raw_timestamp = row["timestamp"]
        if raw_timestamp is None:
            raise DatasetError(f"{path} row {row_index}: missing timestamp")
        timestamp = datetime.fromisoformat(raw_timestamp)
        if timestamp.tzinfo is None:
            raise DatasetError(
                f"{path} row {row_index}: timestamp {raw_timestamp!r} has no timezone; "
                "historical timestamps must be timezone-aware"
            )
        raw_volume = row.get("volume")
        return OHLCVBar(
            timestamp=timestamp,
            open=float(row["open"]),  # type: ignore[arg-type]
            high=float(row["high"]),  # type: ignore[arg-type]
            low=float(row["low"]),  # type: ignore[arg-type]
            close=float(row["close"]),  # type: ignore[arg-type]
            volume=float(raw_volume) if raw_volume else None,
        )
    except DatasetError:
        raise
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise DatasetError(
            f"{path} row {row_index}: could not parse a valid OHLCVBar from {row!r}: {exc}"
        ) from exc
