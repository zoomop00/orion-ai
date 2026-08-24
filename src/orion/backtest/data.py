"""Historical OHLCV data sources for Mission 6 backtesting.

Deliberately not an extension of `orion.data.provider_base.MarketDataProvider`:
that interface's `fetch_ohlcv(symbol, timeframe, output_size)` answers "give
me the most recent N bars" — the live-fetch access pattern. Backtesting needs
"give me every bar between two dates," a structurally different query, so
extending the live interface would blur a simple, already-tested contract for
a use case it never needs. `HistoricalBarSource` is a new, small, Mission-6-
owned interface instead — `orion.data.provider_base`/`MarketDataProvider` are
untouched.

The data *shapes* are fully reused, unchanged: `MarketSnapshot`, `OHLCVBar`,
`DataSourceMeta`, `Timeframe` (from `orion.data.models`). A historical bar and
a live bar are the same kind of fact; only how it is fetched differs.

Per M6-Q1 (approved): exactly two implementations in v1 —
`InMemoryHistoricalBarSource` (bars supplied directly, e.g. by a test) and
`CsvHistoricalBarSource` (bars loaded from a local file). No network fetch,
no new data vendor, no Twelve Data range integration.

Neither implementation ever repairs, reorders, or interpolates data. Bars are
returned in the order they were supplied/read, filtered to the requested
range — if the underlying dataset is unsorted, gapped, or has duplicate
timestamps, the returned slice will faithfully reflect that, and `orion.data.
validation.validate_snapshot` (called once, up front, by `orion.backtest.
engine.run_backtest`) is what classifies and fails closed on it. Silently
sorting or filling gaps here would hide a bad dataset instead of surfacing it.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe


class HistoricalDataError(RuntimeError):
    """Raised when a `HistoricalBarSource` cannot fulfil a request at all —
    a missing/unreadable file, a malformed row, a symbol/timeframe this
    source was never built for, or an invalid `[start, end]` range.

    Never caught and papered over with fabricated or interpolated bars —
    mirrors `orion.data.provider_base.MarketDataProviderError`'s identical
    "no data available" contract for the live-fetch interface. A range that
    is well-formed but simply has zero bars in it (e.g. a query strictly
    outside the loaded dataset) is *not* an error — `fetch_range` returns an
    empty `MarketSnapshot`, and `orion.data.validation.validate_snapshot`
    is what classifies "empty" as untrustworthy for a trading decision,
    the same fail-closed path live data already goes through.
    """


class HistoricalBarSource(ABC):
    """A source of historical OHLCV bars for a single symbol/timeframe,
    queryable by date range."""

    @abstractmethod
    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        """Return every bar with `start <= timestamp <= end`, in the order
        the underlying dataset holds them (never re-sorted here).

        Must raise `HistoricalDataError` if this source cannot serve
        `symbol`/`timeframe` at all, or if `end < start`. Must return an
        empty (not missing) `MarketSnapshot` if the range is well-formed
        but contains no bars — never fabricate or interpolate one.
        """
        raise NotImplementedError


class InMemoryHistoricalBarSource(HistoricalBarSource):
    """Bars supplied directly by the caller — a real dataset already loaded
    into memory (by a test, or by any other loader before handing bars to
    this class). Stored exactly as given; `fetch_range` filters by
    timestamp without re-sorting, so a caller who deliberately constructs
    this with out-of-order or duplicate-timestamp bars (e.g. to test
    `validate_snapshot`'s fail-closed behavior) gets exactly that back.
    """

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: Timeframe,
        bars: list[OHLCVBar],
        provider_name: str = "in-memory",
    ) -> None:
        self._symbol = symbol
        self._timeframe = timeframe
        self._bars = tuple(bars)
        self._provider_name = provider_name

    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        if symbol != self._symbol or timeframe != self._timeframe:
            raise HistoricalDataError(
                f"this source only serves {self._symbol!r}/{self._timeframe.value!r}, "
                f"got {symbol!r}/{timeframe.value!r}"
            )
        if end < start:
            raise HistoricalDataError(
                f"end ({end.isoformat()}) is before start ({start.isoformat()})"
            )

        sliced = [bar for bar in self._bars if start <= bar.timestamp <= end]
        return MarketSnapshot(
            source=DataSourceMeta(
                provider=self._provider_name, symbol=symbol, timeframe=timeframe, retrieved_at=end
            ),
            bars=sliced,
        )


class CsvHistoricalBarSource(HistoricalBarSource):
    """Bars loaded once, eagerly, from a local CSV file at construction —
    columns `timestamp,open,high,low,close[,volume]`, one row per bar,
    ISO-8601 timezone-aware timestamps. Delegates the actual range-slicing
    to an internal `InMemoryHistoricalBarSource` rather than duplicating
    that logic — CSV loading is only a different way of *obtaining* the
    bars list, not a different slicing algorithm.

    Every row is parsed into a real `OHLCVBar` at construction time
    (`OHLCVBar`'s own validator already rejects an incoherent high/low/
    open/close relationship or a non-positive price) — a malformed row
    fails the whole load immediately with `HistoricalDataError`, rather
    than surfacing as a confusing error deep inside a later backtest run.
    """

    def __init__(self, path: str | Path, *, symbol: str, timeframe: Timeframe) -> None:
        bars = _load_bars_from_csv(path)
        self._delegate = InMemoryHistoricalBarSource(
            symbol=symbol, timeframe=timeframe, bars=bars, provider_name=f"csv:{Path(path).name}"
        )

    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        return self._delegate.fetch_range(symbol, timeframe, start=start, end=end)


_REQUIRED_CSV_COLUMNS = {"timestamp", "open", "high", "low", "close"}


def _load_bars_from_csv(path: str | Path) -> list[OHLCVBar]:
    p = Path(path)
    if not p.is_file():
        raise HistoricalDataError(f"historical data file not found: {p}")

    bars: list[OHLCVBar] = []
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        if not _REQUIRED_CSV_COLUMNS.issubset(fieldnames):
            raise HistoricalDataError(
                f"{p} is missing required columns {sorted(_REQUIRED_CSV_COLUMNS)}; "
                f"found {sorted(fieldnames)}"
            )
        for row_index, row in enumerate(reader):
            bars.append(_row_to_bar(p, row_index, row))
    return bars


def _row_to_bar(path: Path, row_index: int, row: dict[str, str | None]) -> OHLCVBar:
    try:
        raw_timestamp = row["timestamp"]
        assert raw_timestamp is not None
        timestamp = datetime.fromisoformat(raw_timestamp)
        if timestamp.tzinfo is None:
            raise HistoricalDataError(
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
    except HistoricalDataError:
        raise
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise HistoricalDataError(
            f"{path} row {row_index}: could not parse a valid OHLCVBar from {row!r}: {exc}"
        ) from exc
