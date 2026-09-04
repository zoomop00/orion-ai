"""Shared builders for `orion.calibration` tests.

Bars are generated from a seeded random walk rather than a linear ramp: a ramp
has a nearly constant true range, which would make every ATR% reading identical
and hide any error in a percentile, a standard deviation or a warm-up boundary.
The seed is fixed inside the builder, so every test sees the same bars without
any test needing to know that.

`build_loaded_dataset` assembles a real `LoadedDataset` -- a real
`DatasetIdentity`, a real fingerprint -- without touching the filesystem, so the
study can be exercised on the exact type `orion.datasets.loader.load_dataset`
returns.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orion.data.models import OHLCVBar, Timeframe
from orion.datasets.loader import LoadedDataset
from orion.datasets.manifest import identity_for, manifest_for

SYMBOL = "XAUUSD"
TIMEFRAME = Timeframe.H1
H1 = timedelta(hours=1)

#: A Monday 00:00 UTC, so a weekly closure lands where `XAUUSD_WEEKLY` expects
#: it in any test that builds a gapped series.
BASE_TIME = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def make_walk_bars(
    n: int,
    *,
    start: datetime = BASE_TIME,
    interval: timedelta = H1,
    start_price: float = 2400.0,
    seed: int = 11,
) -> list[OHLCVBar]:
    """`n` contiguous bars from a seeded random walk with varying ranges."""
    rng = random.Random(seed)
    bars: list[OHLCVBar] = []
    price = start_price
    for index in range(n):
        price *= 1.0 + rng.gauss(0.0, 0.0035)
        half_range = abs(rng.gauss(0.0, 0.004)) * price + 0.05
        bars.append(
            OHLCVBar(
                timestamp=start + index * interval,
                open=price - half_range * 0.3,
                high=price + half_range,
                low=price - half_range,
                close=price,
                volume=1000.0,
            )
        )
    return bars


def make_weekend_bars(weeks: int = 6, *, seed: int = 11) -> list[OHLCVBar]:
    """Real-shaped H1 gold weeks: 120 bars from Sunday 22:00 UTC, then the
    weekly closure, repeated. Every gap is one `XAUUSD_WEEKLY` forgives."""
    bars: list[OHLCVBar] = []
    week_open = datetime(2026, 1, 4, 22, 0, tzinfo=UTC)  # a Sunday, 22:00 UTC
    price = 2400.0
    for week in range(weeks):
        chunk = make_walk_bars(
            120, start=week_open + timedelta(days=7 * week), start_price=price, seed=seed + week
        )
        bars.extend(chunk)
        price = chunk[-1].close
    return bars


def build_loaded_dataset(
    bars: list[OHLCVBar],
    *,
    symbol: str = SYMBOL,
    timeframe: Timeframe = TIMEFRAME,
    provider: str = "twelvedata",
    csv_path: Path | None = None,
) -> LoadedDataset:
    """A `LoadedDataset` over `bars`, with a genuine identity and fingerprint."""
    identity = identity_for(symbol, timeframe, bars)
    manifest = manifest_for(
        identity,
        provider=provider,
        retrieved_at=bars[-1].timestamp,
        requested_start=bars[0].timestamp,
        requested_end=bars[-1].timestamp,
        csv_filename="test.csv",
    )
    return LoadedDataset(
        manifest=manifest,
        bars=tuple(bars),
        csv_path=csv_path if csv_path is not None else Path("test.csv"),
    )
