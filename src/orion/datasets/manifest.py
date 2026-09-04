"""What a historical dataset *is*, independently of where it came from.

THE PROBLEM THIS SOLVES. A backtest result is only meaningful next to the data
it ran on. Naming that data by filename is not enough — files are renamed,
re-downloaded, edited and moved — so a dataset needs an identity computed from
its own content, the way `orion.analysis.evidence.evidence_fingerprint` names
an Evidence and `orion.risk.portfolio.portfolio_state_fingerprint` names an
account.

IDENTITY IS A SEPARATE TYPE FROM THE MANIFEST, AND THAT IS THE WHOLE DESIGN.
`DatasetIdentity` holds exactly the facts that, if any of them changed, would
change a backtest result: the instrument, the interval, the range actually
delivered, how many bars arrived, and a hash over every bar's full OHLCV
content in order. `DatasetManifest` wraps that identity together with
provenance — which vendor answered, when it was retrieved, what range was
requested, which file holds the bars.

Only the identity is hashed. That is not a rule someone has to remember; it is
structural, because `dataset_fingerprint` takes a `DatasetIdentity` and there
is no way to hand it a manifest. **Two independent downloads of the same true
history must fingerprint identically** — if `retrieved_at` or the provider name
entered the hash, re-downloading the same bars would produce a "different"
dataset and the fingerprint would stop meaning *this data*, which is the one
thing it exists to mean.

WHAT IS DELIBERATELY OUTSIDE THE IDENTITY, BEYOND PROVENANCE. A
`orion.data.sessions.TradingSession` is a property of a *run*, not of the bars:
the same dataset is legitimately replayed with and without one. It belongs in a
run record beside the fingerprint, never inside it.

ONE HASHING MECHANISM. Both the bars hash and the fingerprint go through
`orion.provenance.canonical_hash`, for the reason that module's own docstring
gives: two implementations that merely agree today can drift apart tomorrow,
and this codebase keeps exactly one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.data.models import OHLCVBar, Timeframe
from orion.provenance import canonical_hash


class DatasetError(RuntimeError):
    """Raised when a dataset cannot be built, read, or trusted.

    Own top-level hierarchy, matching this codebase's "every layer owns its own
    RuntimeError" rule. Never caught and turned into a partial dataset: a
    manifest that disagrees with its bars is not a dataset with a small
    problem, it is not a dataset.
    """


class _BarSequence(BaseModel):
    """A private carrier so an ordered run of bars can go through
    `canonical_hash` unchanged.

    It exists only because `canonical_hash` takes a `BaseModel`, and inventing
    a second hashing path for "a list of bars" is exactly what
    `orion.provenance` says not to do. Order is significant and preserved: this
    is a tuple, and two datasets holding the same bars in a different order are
    correctly different datasets.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bars: tuple[OHLCVBar, ...]


def bars_hash(bars: Sequence[OHLCVBar]) -> str:
    """A deterministic hash over every bar's full OHLCV content, in order.

    Every field of every bar participates, `volume` included — a dataset whose
    volumes differ is a different dataset even when its prices match, and a
    reader cannot know in advance which field a future strategy will read.
    """
    return canonical_hash(_BarSequence(bars=tuple(bars)))


class DatasetIdentity(BaseModel):
    """The content-derived identity of one historical dataset.

    Every field here changes a backtest result if it changes. Nothing here
    describes where the bars came from — see `DatasetManifest` for that.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    timeframe: Timeframe
    first_bar_timestamp: datetime
    last_bar_timestamp: datetime
    bar_count: int = Field(gt=0)
    bars_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_identity_invariants(self) -> DatasetIdentity:
        for name, value in (
            ("first_bar_timestamp", self.first_bar_timestamp),
            ("last_bar_timestamp", self.last_bar_timestamp),
        ):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        if self.last_bar_timestamp < self.first_bar_timestamp:
            raise ValueError(
                f"last_bar_timestamp ({self.last_bar_timestamp.isoformat()}) is before "
                f"first_bar_timestamp ({self.first_bar_timestamp.isoformat()})"
            )
        return self


def identity_for(symbol: str, timeframe: Timeframe, bars: Sequence[OHLCVBar]) -> DatasetIdentity:
    """Derive the identity of `bars` as delivered.

    The range recorded is the range the data actually covers — the first and
    last bar's own timestamps — never the range someone requested. A request is
    an intention; the delivered bars are the fact, and only the fact belongs in
    an identity.

    Raises `DatasetError` for an empty sequence: a dataset with no bars has no
    first bar, no last bar and nothing to replay, so it is refused at
    construction rather than represented as a degenerate one.
    """
    if not bars:
        raise DatasetError(
            "cannot derive a dataset identity from zero bars; an empty range is not a dataset"
        )
    return DatasetIdentity(
        symbol=symbol,
        timeframe=timeframe,
        first_bar_timestamp=bars[0].timestamp,
        last_bar_timestamp=bars[-1].timestamp,
        bar_count=len(bars),
        bars_hash=bars_hash(bars),
    )


def dataset_fingerprint(identity: DatasetIdentity) -> str:
    """The deterministic fingerprint of a dataset's content.

    Takes an identity and not a manifest, on purpose: provenance can never
    reach this function, so it can never alter what a dataset is called.
    """
    return canonical_hash(identity)


class DatasetManifest(BaseModel):
    """A dataset's identity, its fingerprint, and where it came from.

    THIS IS THE FILE THAT GOES INTO GIT. The bars themselves do not — they live
    under the gitignored `/data/`, are re-downloadable, and are far too large to
    version. The manifest is small, and it is enough: anyone can re-acquire the
    range, rebuild the identity and compare fingerprints to prove they hold the
    same history a recorded result was produced from.

    `fingerprint` is stored rather than only computed so the file is readable on
    its own, and a validator recomputes it on every construction — a manifest
    edited by hand to claim a fingerprint its identity does not produce is
    rejected, not believed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: DatasetIdentity
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    # --- provenance: recorded, never hashed ---
    provider: str = Field(min_length=1)
    retrieved_at: datetime
    requested_start: datetime
    requested_end: datetime
    csv_filename: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_fingerprint_matches_identity(self) -> DatasetManifest:
        expected = dataset_fingerprint(self.identity)
        if self.fingerprint != expected:
            raise ValueError(
                f"fingerprint {self.fingerprint!r} does not match this manifest's own identity "
                f"(which hashes to {expected!r}); a manifest may not claim an identity it does "
                "not have"
            )
        for name, value in (
            ("retrieved_at", self.retrieved_at),
            ("requested_start", self.requested_start),
            ("requested_end", self.requested_end),
        ):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        return self


def manifest_for(
    identity: DatasetIdentity,
    *,
    provider: str,
    retrieved_at: datetime,
    requested_start: datetime,
    requested_end: datetime,
    csv_filename: str,
) -> DatasetManifest:
    """Build a manifest around `identity`, stamping its fingerprint."""
    return DatasetManifest(
        identity=identity,
        fingerprint=dataset_fingerprint(identity),
        provider=provider,
        retrieved_at=retrieved_at,
        requested_start=requested_start,
        requested_end=requested_end,
        csv_filename=csv_filename,
    )
