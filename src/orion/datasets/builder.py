"""Turning a vendor range fetch into a reproducible dataset on disk.

WHAT A BUILD PRODUCES: two files, deliberately in two places. The bars go to a
CSV under the gitignored `/data/`, because a multi-year series is large,
re-downloadable, and has no business in version control. The manifest goes
somewhere tracked, because it is small and it is the part that makes a result
checkable — anyone can re-acquire the range, rebuild the identity, and compare
fingerprints. Both paths are arguments; this module invents neither, reads no
configuration, and constructs no vendor.

IT CONSTRUCTS NO PROVIDER. The `HistoricalMarketDataProvider` arrives as an
argument, exactly as every collaborator does in `orion.monitor.iteration` and
for the same reason: a module that builds its own vendor client cannot be
tested without one, and cannot be pointed at a different source without being
edited.

IT DOES NOT VALIDATE, AND THAT IS DELIBERATE. `orion.data.validation.
validate_snapshot` classifies a snapshot as fit to reason over, and its GAP
finding cannot tell a legitimate market closure from missing bars without the
`orion.data.sessions.TradingSession` a *consumer* declares. Running it here
would reject every real gold dataset for containing weekends — the exact
failure Mission 6 Phase 2 exists to fix — or would require this module to guess
a session on the caller's behalf. So a build records what the vendor gave and
the manifest records the range it actually covers; judging that data is done at
replay time, by whoever knows which session applies.

NOTHING IS REPAIRED. No bar is interpolated, back-filled, reordered,
deduplicated or dropped between the vendor response and the file. If the vendor
returned something odd, the dataset faithfully contains something odd, its
fingerprint reflects it, and `validate_snapshot` will say so when it is
replayed. A builder that quietly tidied its input would produce a dataset whose
fingerprint names history that never happened.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from orion.data.historical import HistoricalMarketDataProvider
from orion.data.models import Timeframe
from orion.datasets.csv_store import write_bars_csv
from orion.datasets.manifest import DatasetError, DatasetManifest, identity_for, manifest_for


def build_dataset(
    *,
    provider: HistoricalMarketDataProvider,
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    csv_path: Path,
    manifest_path: Path,
) -> DatasetManifest:
    """Fetch `[start, end]`, write the bars and their manifest, return it.

    Propagates `orion.data.provider_base.MarketDataProviderError` from the
    provider uncaught — a failed or possibly-truncated fetch must abort the
    build, never produce a dataset whose fingerprint names an incomplete range
    as though it were whole.

    Raises `DatasetError` if the range came back empty: there is nothing to
    replay and nothing to identify, so no files are written.

    `csv_path` conventionally lives under the gitignored `/data/`;
    `manifest_path` conventionally lives in a tracked directory. Neither is
    enforced here — the convention belongs in a decision entry and a README,
    not in a runtime check that would stop someone building a dataset in a
    temporary directory.
    """
    snapshot = provider.fetch_range(symbol, timeframe, start=start, end=end)
    bars = snapshot.bars
    if not bars:
        raise DatasetError(
            f"{symbol}/{timeframe.value} returned zero bars between {start.isoformat()} and "
            f"{end.isoformat()}; there is nothing to build a dataset from, and no file is written"
        )

    identity = identity_for(symbol, timeframe, bars)
    manifest = manifest_for(
        identity,
        provider=snapshot.source.provider,
        retrieved_at=snapshot.source.retrieved_at,
        requested_start=start,
        requested_end=end,
        csv_filename=Path(csv_path).name,
    )

    # Bars first: a manifest on disk with no data beside it is a broken
    # dataset that looks complete, which is worse than data with no manifest.
    write_bars_csv(Path(csv_path), bars)
    write_manifest(Path(manifest_path), manifest)
    return manifest


def write_manifest(path: Path, manifest: DatasetManifest) -> None:
    """Write `manifest` as indented, key-sorted JSON.

    Sorted and indented so a committed manifest diffs meaningfully and two
    writes of the same manifest produce byte-identical files. This formatting
    has no bearing on the fingerprint, which comes from
    `orion.provenance.canonical_hash`'s own canonical form.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")
