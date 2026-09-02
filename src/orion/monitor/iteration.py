"""One stateless monitoring iteration: market data → resolution → persistence.

WHAT THIS IS, AND WHY IT IS NOT IN `orion.cli`. Mission 9 Phase 2 put this
sequence — read the account, derive a window, fetch it, validate it, resolve —
inside `orion.cli._run_resolve`, welded to argument parsing, `print` and exit
codes. Phase 3 needs to run the identical sequence on a timer, and the way that
goes wrong is a second copy that drifts from the first. So the sequence moved
here, `orion resolve` calls it, the supervisor calls it, and there is exactly
one implementation of what a monitoring pass does.

IT READS NO CONFIGURATION AND CONSTRUCTS NO VENDOR. Every collaborator arrives
as an argument: the store, the journal, a factory for the market-data provider,
the execution policy, the staleness threshold. That is what keeps this module
free of `orion.config` and `orion.orchestrator` — a hygiene test asserts it —
and it is also what lets the caller re-read settings on every single iteration,
which `orion.risk.policy.RiskPolicy.from_settings` explicitly requires so that
engaging the kill switch never needs a restart. A long-running monitor is the
place that requirement matters most, not least.

IT IS STATELESS, AND THAT IS THE WHOLE SAFETY ARGUMENT. Nothing is remembered
between calls. The `PortfolioStore` is the only authority on what is open, it is
re-read every time, and the revision that guards the write is the one read in
the same call. A crash between two iterations costs nothing but the iteration
that was in flight; a restart simply performs the next one.

WHAT IT MUST NEVER DO. No reasoning provider, no analyst, no `Evidence`, no
`orion.risk.guardian.review`, no `execute()`, no broker. This path can close a
position that reached a level the Risk Guardian already approved; it can never
open one, size one, or ask a model anything. The import hygiene test for this
package is the structural half of that promise.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from math import ceil

from orion.data.models import PriceObservation, Timeframe
from orion.data.provider_base import MarketDataProvider
from orion.data.validation import TIMEFRAME_INTERVAL, DataQualityIssue, validate_snapshot
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.session import CycleResult, JournalWriter, resolve_position
from orion.paper.store import PortfolioStore

#: How many bars one resolution may ever request. A ceiling, not a target: the
#: window is derived from how long the position has been open, and this only
#: stops a position left open for months from turning one pass into an unbounded
#: request. A window that hits it is reported `TRUNCATED` rather than passed off
#: as complete -- see `orion.paper.session.ResolutionCoverage`.
MAX_RESOLUTION_BARS = 500

#: Bars added to the derived window so at least one sits at or before the entry.
#: `window_coverage` needs that bar to *prove* the window reached back; without
#: it, a window that happens to start exactly at the entry is indistinguishable
#: from one that started after it, and the honest reading of the ambiguous case
#: would be `TRUNCATED` on every single call.
_RESOLUTION_WINDOW_MARGIN_BARS = 2

#: Data-quality findings that make a resolution window *incomplete* rather than
#: unusable. Exactly one: a gap means bars are missing, which is what
#: `ResolutionCoverage.GAPPED` reports. Every other finding -- empty, stale, out
#: of order, duplicated -- means the data cannot be trusted at all and the
#: iteration refuses. Widening this set would start resolving positions against
#: data the rest of ORION calls unusable.
COVERAGE_ONLY_DATA_ISSUES = frozenset({DataQualityIssue.GAP})

#: How the iteration obtains a price source, if it turns out to need one.
#: A factory rather than a provider because a flat account must cost nothing:
#: this is not called at all when there is no open position, so neither an HTTP
#: client nor a request is created. `None` means the deployment cannot fetch
#: prices at all.
MarketDataFactory = Callable[[], MarketDataProvider | None]


class IterationStatus(StrEnum):
    """What one monitoring pass was able to do.

    A status, deliberately not a `CycleOutcome`. `CycleOutcome` says what
    happened to a *position*; this says how far the pass got before it could
    say anything about one at all. Folding these into that enum would put "the
    vendor was unreachable" beside "the position was closed", which are answers
    to different questions.
    """

    #: A resolution ran. `IterationResult.cycle` carries what it did.
    RESOLVED = "resolved"
    #: The account holds no open position. Not a failure, and reached without
    #: spending a vendor request.
    NO_POSITION = "no_position"
    #: The account could not be read at all.
    ACCOUNT_UNREADABLE = "account_unreadable"
    #: No market-data credential is configured in this deployment.
    NOT_CONFIGURED = "not_configured"
    #: The price window could not be fetched, or was not usable.
    PRICE_UNAVAILABLE = "price_unavailable"


class IterationResult:
    """What one pass did, with the objects it produced.

    A plain container, matching `orion.paper.session.CycleResult`'s style for
    the same reason: it is never journaled or hashed, and its parts are the
    things that carry meaning.

    `cycle` and `price` are set only for `RESOLVED`; every other status means
    the pass stopped before a resolution could be attempted, and inventing a
    cycle for it would be reporting something that did not happen.
    """

    __slots__ = ("status", "cycle", "price")

    def __init__(
        self,
        *,
        status: IterationStatus,
        cycle: CycleResult | None = None,
        price: PriceObservation | None = None,
    ) -> None:
        self.status = status
        self.cycle = cycle
        self.price = price


def resolution_window_bars(opened_at: datetime, now: datetime, *, timeframe: Timeframe) -> int:
    """How many bars to request to cover `opened_at` through `now`.

    One per elapsed interval, plus `_RESOLUTION_WINDOW_MARGIN_BARS`, capped at
    `MAX_RESOLUTION_BARS`. Never fewer than the margin, so a position opened
    moments ago still asks for enough bars to prove the window reached back.

    A clock that has gone backwards (`now < opened_at`) yields the minimum
    rather than a negative count -- this function never raises, and the honest
    consequence of a short window is reported downstream as coverage rather
    than guessed at here.
    """
    interval = TIMEFRAME_INTERVAL[timeframe]
    elapsed_bars = max(0, ceil((now - opened_at) / interval))
    return min(elapsed_bars + _RESOLUTION_WINDOW_MARGIN_BARS, MAX_RESOLUTION_BARS)


def resolve_once(
    *,
    symbol: str,
    timeframe: Timeframe,
    store: PortfolioStore,
    journal: JournalWriter,
    market_data: MarketDataFactory,
    policy: PaperExecutionPolicy,
    max_staleness_bars: float,
    now: datetime,
) -> IterationResult:
    """Run one monitoring pass against `store`. Never raises for an expected
    operational failure; unexpected ones propagate.

    THE ACCOUNT IS READ BEFORE THE VENDOR IS ASKED, for two reasons. The window
    to request is derived from the open position's `opened_at`, so it cannot be
    known earlier; and an account with nothing open costs zero vendor requests
    instead of one, which is what makes running this on a short interval
    reasonable against a rate-limited free tier.

    THE WINDOW IS DERIVED, THEN CAPPED -- see `resolution_window_bars`.

    VALIDATION REFUSES ON EVERYTHING EXCEPT A GAP, AND THAT ASYMMETRY IS
    DELIBERATE. Empty, stale, out-of-order and duplicated data all mean the
    answer cannot be trusted at all, so this refuses rather than resolving. A
    *gap* means something narrower and survivable: some bars are missing, so
    coverage is incomplete -- which `ResolutionCoverage.GAPPED` says out loud.
    Refusing on a gap instead would leave a real stop unhonoured every time the
    window spans a market weekend, which is exactly the failure this path
    exists to remove. See `orion.paper.session.window_coverage`.

    NO RETRY, EVER. A pass that could not fetch a price returns saying so. It
    does not sleep, back off or try again: the next scheduled pass starts fresh
    from the store, which is a stronger position than any retry could reach,
    because by then the account may have been changed by something else.
    """
    try:
        record = store.load()
    except Exception:
        # `PortfolioStoreError` embeds SQLAlchemy text naming the database and
        # its path. Never bound, never formatted, never re-raised.
        return IterationResult(status=IterationStatus.ACCOUNT_UNREADABLE)

    held = record.state.open_position
    if held is None:
        return IterationResult(status=IterationStatus.NO_POSITION)

    provider = market_data()
    if provider is None:
        return IterationResult(status=IterationStatus.NOT_CONFIGURED)

    try:
        snapshot = provider.fetch_ohlcv(
            symbol, timeframe, resolution_window_bars(held.opened_at, now, timeframe=timeframe)
        )
    except Exception:
        # Message-free for the same reason every other failure here is: the
        # provider's own text names the vendor and can carry response bodies.
        return IterationResult(status=IterationStatus.PRICE_UNAVAILABLE)

    validation = validate_snapshot(snapshot, now=now, max_staleness_bars=max_staleness_bars)
    latest = snapshot.latest_bar
    fatal = {finding.issue for finding in validation.findings} - COVERAGE_ONLY_DATA_ISSUES
    if fatal or latest is None:
        return IterationResult(status=IterationStatus.PRICE_UNAVAILABLE)

    price = PriceObservation(
        symbol=snapshot.source.symbol, last_price=latest.close, as_of=latest.timestamp
    )

    cycle = resolve_position(
        record=record,
        price=price,
        store=store,
        journal=journal,
        policy=policy,
        now=now,
        # The bar-aware path. Without this a pass would still work and would
        # still miss every crossing that happened between two observations,
        # which is the whole of Phase 2.
        bars=snapshot,
    )
    if cycle is None:
        # The position was read above and nothing here can have closed it, so
        # this is unreachable in practice. Checked rather than asserted,
        # because the alternative is a caller rendering from `None`.
        return IterationResult(status=IterationStatus.NO_POSITION)
    return IterationResult(status=IterationStatus.RESOLVED, cycle=cycle, price=price)
