"""One forward paper-trading cycle: the production writer Mission 2 never had.

WHAT THIS CLOSES. Mission 4 built `execute`, `resolve_stop_take_profit` and the
account they act on; Mission 2 built an append-only journal with six `record_*`
methods. Until now `orion.paper` had exactly one consumer anywhere in `src/` —
`orion.backtest.engine`, which replays history — so no `Fill` and no
`ClosedPosition` had ever been produced by a system running forward in time, and
the journal had no production writer at all. This module is that writer, and it
is deliberately the only one.

IT DOES NOT IMPORT THE ORCHESTRATOR, AND IT MUST NOT.
`tests/orchestrator/test_import_hygiene.py::test_no_engine_package_imports_the_orchestrator`
lists `paper` among the engine packages forbidden from importing
`orion.orchestrator`, because an import in that direction inverts the layering
every other hygiene test protects. So `run_cycle` takes the three domain objects
an analysis produced — `ProposedDecision`, `Evidence`, `RiskVerdict` — rather
than the `AnalysisReport` that happens to carry them. Unpacking that report is
the composition root's job, in `orion.cli`.

IT CARRIES A `MarketThesis` AND MUST NEVER READ ONE.
`orion.thesis.schemas` is a schema-only package with no analyst, engine, store
or provider in its import list, which is why this module may name a
`MarketThesis` while still being forbidden to import `orion.analysts` at all.
The thesis is advisory evidence: it arrives, it goes into the journal, and
nothing in this file inspects a single field of it. That is not a convention —
`tests/paper/test_session.py::test_the_thesis_is_carried_and_never_read` walks
this file's AST and fails on any attribute access into it. A specialist reading
must not be one edit away from influencing an execution, and the way to
guarantee that while still storing it is to make the carrying provably blind.

IT DOES NOT IMPORT `orion.memory` EITHER, FOR A DIFFERENT REASON.
`orion.memory` already depends on `orion.paper` in three places
(`journal.py` and `sqlite_journal.py` import `Fill`/`ClosedPosition`,
`portfolio_store.py` imports `PortfolioStore`). Importing `DecisionJournal` back
would turn a clean one-way dependency into a cycle. `JournalWriter` below is the
inversion of that dependency, structurally satisfied by the existing
`DecisionJournal` and `SqliteDecisionJournal` with no change to either. It
restates an interface, never an implementation, and a test asserts both real
journals still satisfy it so the two cannot drift apart.

IT WRITES THE CHAIN THAT AUTHORISED THE FILL, NOT ONLY THE FILL.
Phase 2b journaled a `Fill` and a `ClosedPosition` and nothing else, so the
records they reference -- the `RiskVerdict` that approved the trade and the
`ProposedDecision` it reviewed -- existed nowhere durable.
`orion.reflection.analysis.reflect_on_closed_position` walks exactly
`ClosedPosition -> RiskVerdict -> ProposedDecision` and fails closed on a missing
link, so Reflection could not run on a single trade this system had actually
made: it had only ever been exercised against hand-built test chains. This phase
writes the four records the chain needs, leaf-first (evidence, reasoning,
decision, verdict) and *before* the fill, so any prefix of an interrupted write
is internally consistent and no stored record ever references one that is
missing. That is why `JournalWriter` is no longer two methods: the narrowness in
2b was a true statement about what that phase produced, not a safety property,
and it stopped being true the moment a fill needed its authorisation on record.

A VETOED PROPOSAL IS STILL NOT JOURNALED, DELIBERATELY. The chain is written on
the executed path only. Journaling every refused proposal is a defensible
separate decision -- a cron entry running this command hourly would write a
chain per invocation, almost all of them identical WAITs, with no consumer -- and
the tests asserting that a veto performs zero saves and zero journal calls are
left standing rather than narrowed.

THE RISK GUARDIAN HAS ABSOLUTE AUTHORITY, AND IT IS STRUCTURAL.
`execute()` itself raises `PaperExecutionError` unless `verdict.outcome` is
APPROVED, so a veto cannot produce a `Fill` even if this module were wrong. It
is checked here as well, before anything is built, so the common case refuses
cheaply and reports honestly rather than by exception.

THE PORTFOLIO EXECUTED AGAINST IS THE ONE THAT WAS REVIEWED, PROVEN BY HASH.
The Guardian does not review the stored account: `orion.orchestrator.analysis`
marks it to market first — a pure, unsaved projection — and reviews *that*, so
`verdict.portfolio_ref` fingerprints the projection and not the record.
`execute()` verifies exactly that fingerprint. This module therefore reproduces
the projection with the same pure function and the same inputs, and refuses to
execute unless the fingerprint it computes equals the one the verdict carries.
Passing the raw stored state instead would fail `execute()`'s own check on every
approved trade; reproducing the projection *without* re-verifying would be
trusting an assumption that a future change to either side could silently break.

PERSISTENCE ORDER IS THE ACCOUNT FIRST, THE JOURNAL SECOND, ALWAYS.
Mission 5's Finding A settled this: `SqlitePortfolioStore` and
`SqliteDecisionJournal` commit independently, so a crash between them is
architecturally possible, and the recoverable ordering is the one where the
account is authoritative. If `save()` fails, nothing is journaled and no trade
happened. If the journal write fails after `save()` succeeded, the trade *did*
happen and is reported as persisted-but-unjournaled rather than rolled back —
the journal write is content-addressed and idempotent, so replaying it after a
restart can never duplicate the record or require re-executing the trade.

NOTHING HERE IS FABRICATED. Every number in the result comes from the engines:
the fill price and fee from `execute()` under the configured
`PaperExecutionPolicy`, the quantity from `compute_position_size`, the stop and
target from the proposal the Guardian approved, the resulting balances from
`execute()`'s own cash-conservation arithmetic. This module computes no price,
invents no quantity, and never writes a `ClosedPosition` that
`resolve_stop_take_profit` did not actually produce.

RESOLVING AND CONSIDERING ARE TWO CALLS NOW, NOT TWO BRANCHES (Mission 9).
`resolve_position` is the first half — close the open position if it reached a
level — and it is exported so a caller holding nothing but a price can run it.
`run_cycle` calls it first and returns whatever it returned, so resolve-before-
consider and "a closing cycle does not also open" hold because there is one
implementation, not because two agree. What the split removes is a dependency:
checking a stop no longer requires an `Evidence`, and therefore no longer
requires the market data, the indicators, the model call and the verdict that
building one costs. Nothing about the *outcome* of a resolution changed.

NO VENUE, NO SCHEDULE, NO LOOP. One cycle per call, returning a value. There is
no retry, no sleep, no thread, no background work, and nothing that reaches a
broker — a paper account is a local simulation of one. Mission 9 makes frequent
resolution cheap; it never runs one on a timer, and nothing here starts work the
caller did not ask for.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from orion.analysis.evidence import Evidence
from orion.data.models import MarketSnapshot, OHLCVBar
from orion.data.validation import TIMEFRAME_INTERVAL
from orion.decision.schemas import ProposedDecision
from orion.paper.execution import (
    ObservedPrice,
    eligible_bars,
    execute,
    resolve_stop_take_profit,
    resolve_stop_take_profit_bars,
)
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, Fill, PaperExecutionError
from orion.paper.store import PortfolioRecord, PortfolioStore
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.portfolio import (
    PortfolioState,
    Position,
    compute_position_size,
    mark_to_market,
    portfolio_state_fingerprint,
)
from orion.risk.schemas import RiskOutcome, RiskVerdict
from orion.thesis.schemas import MarketThesis


@runtime_checkable
class JournalWriter(Protocol):
    """The journal's write surface, restated so `orion.paper` need not import
    `orion.memory` to reach it.

    THIS DECLARES WRITES ONLY — never a read, never an update, never a delete.
    `orion.memory.journal.DecisionJournal` has no update or delete method to
    restate, and the reads are `orion.reflection`'s business, not this module's.
    A forward cycle appends what it did; it never revisits what it did before.

    Every method is content-addressed and idempotent in the real implementation,
    which is what makes the crash window in `run_cycle` recoverable by replay.
    See that function's `PERSISTENCE ORDER` note.
    """

    def record_evidence(self, evidence: Evidence, *, recorded_at: datetime) -> str:
        """Persist `evidence`, returning its content fingerprint. Idempotent."""
        ...

    def record_reasoning(self, reasoning: ReasoningOutput, *, recorded_at: datetime) -> str:
        """Persist `reasoning`, returning its content fingerprint. Idempotent."""
        ...

    def record_decision(self, decision: ProposedDecision, *, recorded_at: datetime) -> str:
        """Persist `decision`, returning its content fingerprint. Idempotent."""
        ...

    def record_verdict(self, verdict: RiskVerdict, *, recorded_at: datetime) -> str:
        """Persist `verdict`, returning its content fingerprint. Idempotent."""
        ...

    def record_thesis(self, thesis: MarketThesis, *, recorded_at: datetime) -> str:
        """Persist `thesis`, returning its content fingerprint. Idempotent."""
        ...

    def record_fill(self, fill: Fill, *, recorded_at: datetime) -> str:
        """Persist `fill`, returning its content fingerprint. Idempotent."""
        ...

    def record_closed_position(self, closed: ClosedPosition, *, recorded_at: datetime) -> str:
        """Persist `closed`, returning its content fingerprint. Idempotent."""
        ...


class AuditChain:
    """The records that authorised one fill, in the order they must be written:
    each one references only records already stored before it.

    `Evidence` and `ReasoningOutput` are not read by
    `orion.reflection.analysis.reflect_on_closed_position` — `ProposedDecision`
    already carries the `EvidenceRef`/`ReasoningRef` it needs — but they are the
    only durable copy of what ORION actually saw and actually said, including
    the rationale `ReasoningRef` deliberately excludes. Recording the reference
    without ever recording the referent would leave a chain whose links point at
    nothing.

    `thesis` is optional and goes in first, because it is the only member that
    references nothing at all: a `MarketThesis` carries the `analysis_id` the
    decision and verdict also carry, never a fingerprint of either, so it is a
    leaf in the strictest sense and writing it first keeps "no stored record
    ever points at a missing one" true at every prefix. It is `None` for a
    caller that formed no thesis — a backtest, or an analysis where neither
    specialist had anything to say — and its absence is not a failure.

    A plain container, like `CycleResult`: it is never journaled or hashed as a
    unit — its members each are, by their own fingerprint functions.
    """

    __slots__ = ("evidence", "reasoning", "decision", "verdict", "thesis")

    def __init__(
        self,
        *,
        evidence: Evidence,
        reasoning: ReasoningOutput,
        decision: ProposedDecision,
        verdict: RiskVerdict,
        thesis: MarketThesis | None = None,
    ) -> None:
        self.evidence = evidence
        self.reasoning = reasoning
        self.decision = decision
        self.verdict = verdict
        self.thesis = thesis


class CycleOutcome(StrEnum):
    """What one forward cycle actually did. A closed vocabulary, so a caller
    branches on a value rather than inferring from which fields are set."""

    #: The Guardian approved and a position was opened.
    EXECUTED = "executed"
    #: An open position reached its stop or target and was closed. No new
    #: position is opened in the same cycle.
    CLOSED = "closed"
    #: A position is open and neither level was reached. Nothing to do.
    HOLDING = "holding"
    #: The Guardian returned a verdict, and it was not APPROVED.
    VETOED = "vetoed"
    #: No verdict was available at all, so there was nothing to act on.
    NO_VERDICT = "no_verdict"
    #: The Guardian approved, but the size the policy entitles this proposal
    #: to is not a tradeable quantity. Refused rather than rounded up.
    NOT_TRADEABLE = "not_tradeable"
    #: The account that would be executed against is not the one the Guardian
    #: reviewed. Fails closed; see `run_cycle`.
    PORTFOLIO_MISMATCH = "portfolio_mismatch"
    #: The simulated execution itself refused. No account change, no journal.
    EXECUTION_REFUSED = "execution_refused"
    #: The account could not be read, or could not be written. If this is
    #: reported, no journal write was attempted.
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    #: The account was persisted and the journal write then failed. The trade
    #: happened; only its audit record is missing, and replay is safe.
    JOURNAL_UNAVAILABLE = "journal_unavailable"


#: Outcomes in which the account was durably written by this cycle.
_PERSISTED = frozenset(
    {CycleOutcome.EXECUTED, CycleOutcome.CLOSED, CycleOutcome.JOURNAL_UNAVAILABLE}
)


class ResolutionCoverage(StrEnum):
    """How much of the window since the position opened a resolution actually saw.

    WHY THIS IS NOT A `CycleOutcome`. What happened is one question — closed,
    holding, refused — and how much was examined is another. A bar-aware
    resolution that finds no hit reports `HOLDING`, and `HOLDING` says "neither
    stop nor target was reached". That sentence is only true if every bar since
    the entry was looked at. Folding the two into one enum would either
    multiply its members or, worse, let an unexamined window masquerade as a
    clean hold — so coverage rides alongside the outcome instead of inside it,
    and `CycleOutcome` keeps exactly the members it has always had.

    THREE STATES, NOT A BOOL, for the reason `orion.risk.schemas.ControlStatus`
    already gives for its own three: a reader who is told "incomplete" will ask
    why, and the two reasons call for different responses — a truncated window
    is fixed by asking for more bars, a gapped one is the vendor's answer being
    short and cannot be. Collapsing them would throw that away at the exact
    moment it matters.

    NONE OF THESE CLAIMS THE NEWEST BAR IS FINISHED. The market-data provider
    exposes no completion flag, so the most recent bar may still be forming and
    its range may still grow. `COMPLETE` therefore means "every bar the window
    should contain was present and examined", never "nothing further can
    happen" — a distinction stated here because it is the one a reader is most
    likely to over-read.
    """

    #: Every bar from the entry to the newest fetched one was present and
    #: examined. The newest of them may still be forming; see above.
    COMPLETE = "complete"
    #: The window did not reach back to `Position.opened_at`: bars between the
    #: entry and the oldest bar fetched were never seen. A level could have
    #: been crossed in them.
    TRUNCATED = "truncated"
    #: The window reached back far enough but is missing bars inside itself.
    #: Reported in preference to nothing at all; `TRUNCATED` wins over this
    #: when both are true, being the larger and earlier blind spot.
    GAPPED = "gapped"


#: Coverage states in which a `HOLDING` result must not be read as "nothing
#: happened" -- exported so a renderer cannot invent its own list.
INCOMPLETE_COVERAGE = frozenset({ResolutionCoverage.TRUNCATED, ResolutionCoverage.GAPPED})

#: How far apart two consecutive bars may sit before the window is called
#: gapped. The same figure `orion.data.validation.validate_snapshot` uses for
#: its own GAP finding, named here rather than imported because this scan
#: answers a different question over a different set of bars -- see
#: `window_coverage`.
_GAP_TOLERANCE_BARS = 1.5


def window_coverage(snapshot: MarketSnapshot, position: Position | None) -> ResolutionCoverage:
    """How much of the since-entry window `snapshot` actually establishes.

    NOT A SECOND `validate_snapshot`, AND THE DIFFERENCE IS THE POINT. That
    function asks "is this data fit to reason over?" and its GAP finding makes a
    snapshot invalid. This asks "does this data prove nothing was missed since
    the entry?" over a different set of bars — only those after `opened_at` —
    and its answer is never a refusal. A missing bar must not stop a real stop
    from being honoured; it must stop the *result* from claiming a clean hold.
    Fetching a window wide enough to span a market weekend can legitimately
    contain a gap, so refusing on one would leave positions unresolved every
    Monday, which is the failure mode this exit path exists to remove.

    The bars acted on are `orion.paper.execution.eligible_bars` — the same set
    the resolver acts on, obtained from the same function, so what was examined
    and what is reported can never diverge.

    THE SCAN STARTS ONE BAR BEFORE THE FIRST EXAMINED ONE, AND THAT BAR IS WHY
    THIS IS CORRECT. Checking gaps across the eligible bars alone leaves the
    entry boundary itself unchecked: a window holding the last bar before the
    entry and then nothing until hours later is contiguous *within* its eligible
    set, and would have claimed COMPLETE while never seeing the hours in
    between. That is not a corner case — it is the ordinary shape of a position
    opened just after the last bar before a market weekend, where the first
    eligible bar is the Monday one and the whole weekend is the hole. So the
    boundary bar is prepended to the scan: it is never examined for a trigger
    (the resolver still ignores it, correctly — its range predates the entry),
    it exists here solely to make the interval across the entry measurable.
    """
    if position is None:
        return ResolutionCoverage.COMPLETE

    bars = snapshot.bars
    # Reaching back means holding at least one bar at or before the entry:
    # that is what proves the bar containing `opened_at` is not itself missing.
    if not bars or bars[0].timestamp > position.opened_at:
        return ResolutionCoverage.TRUNCATED

    examined = eligible_bars(snapshot, position)
    if not examined:
        # The window ends at or before the entry: nothing after it was seen.
        return ResolutionCoverage.TRUNCATED

    # The newest bar at or before the entry. Non-empty by the reach-back check
    # above, and the last such bar because `bars` is ordered oldest-first --
    # the same ordering that check already relies on.
    boundary = [bar for bar in bars if bar.timestamp <= position.opened_at][-1]
    scanned = (boundary, *examined)

    interval = TIMEFRAME_INTERVAL[snapshot.source.timeframe]
    limit = interval * _GAP_TOLERANCE_BARS
    for earlier, later in zip(scanned, scanned[1:], strict=False):
        if later.timestamp - earlier.timestamp > limit:
            return ResolutionCoverage.GAPPED
    return ResolutionCoverage.COMPLETE


class CycleResult:
    """What one cycle did, with the real objects it produced.

    A plain container rather than a Pydantic model, matching
    `orion.paper.execution.ExecutionResult`'s style for the same reason: it is
    never journaled or hashed as a unit — its parts are.

    `fill` and `closed_position` are the engines' own objects, present only when
    the corresponding thing genuinely happened. `portfolio` is the state that was
    actually saved, or the unchanged state when nothing was — and `None` only
    when the account could not be read at all, which is the one case where there
    is no state to report and inventing a flat one would be a lie.

    `audit_chain_fingerprints` is the fingerprints of the records this cycle
    journaled, in write order — the thesis when there was one, then the
    evidence, reasoning, decision and verdict — empty
    when there was no chain to write (a hold, a veto, a close) *and* when the
    write was attempted and failed, which `JOURNAL_UNAVAILABLE` reports
    separately. It is never partially populated: a chain either went in whole or
    is reported as absent.

    `coverage` and `triggering_bar_at` are Mission 9 Phase 2's, and both are
    `None` on every path that did not resolve against bars — every `run_cycle`
    result, and every last-price resolution. Neither is journaled: this
    container is not a record, and `ClosedPosition` was deliberately left
    unchanged by that phase so no stored fingerprint moved. `coverage` is what
    stops a `HOLDING` from being read as "nothing happened" when the window
    could not establish that; `triggering_bar_at` names the bar that caused a
    close, for rendering and audit by eye — `ClosedPosition.closed_at` remains
    the caller-supplied `now`, never a bar timestamp.
    """

    __slots__ = (
        "outcome",
        "portfolio",
        "fill",
        "closed_position",
        "journal_fingerprint",
        "audit_chain_fingerprints",
        "coverage",
        "triggering_bar_at",
    )

    def __init__(
        self,
        *,
        outcome: CycleOutcome,
        portfolio: PortfolioState | None,
        fill: Fill | None = None,
        closed_position: ClosedPosition | None = None,
        journal_fingerprint: str | None = None,
        audit_chain_fingerprints: tuple[str, ...] = (),
        coverage: ResolutionCoverage | None = None,
        triggering_bar_at: datetime | None = None,
    ) -> None:
        self.outcome = outcome
        self.portfolio = portfolio
        self.fill = fill
        self.closed_position = closed_position
        self.journal_fingerprint = journal_fingerprint
        self.audit_chain_fingerprints = audit_chain_fingerprints
        self.coverage = coverage
        self.triggering_bar_at = triggering_bar_at

    @property
    def account_was_written(self) -> bool:
        """Whether this cycle durably changed the account."""
        return self.outcome in _PERSISTED

    @property
    def executed(self) -> bool:
        """Whether a position was opened. Exactly when there is a `Fill`."""
        return self.fill is not None

    @property
    def coverage_is_incomplete(self) -> bool:
        """Whether this result's window failed to establish that nothing was
        missed. `False` for a resolution that saw everything *and* for every
        path where coverage does not apply — a caller asking this is asking
        "should I distrust a hold?", and the answer for `run_cycle` is no."""
        return self.coverage in INCOMPLETE_COVERAGE


def resolve_position(
    *,
    record: PortfolioRecord,
    price: ObservedPrice,
    store: PortfolioStore,
    journal: JournalWriter,
    policy: PaperExecutionPolicy,
    now: datetime,
    bars: MarketSnapshot | None = None,
) -> CycleResult | None:
    """Resolve `record`'s open position against `price`. Never raises.

    MISSION 9: THIS IS THE HALF OF A CYCLE THAT NEEDS NO ANALYSIS. It was the
    first branch of `run_cycle`, reachable only by running a full analysis
    first — market data, indicators, structure, a model call, a decision and a
    verdict — none of which it read. A stop-loss that can only be checked by
    asking Claude is a stop-loss that stops working when a credential expires,
    a rate limit is hit, or nobody types the command. Extracting it changes
    nothing about *what* resolution does; it changes what has to happen before
    it can. `orion.cli` now has a path that fetches a price and calls exactly
    this.

    IT IS STILL THE SAME FUNCTION `run_cycle` RUNS. `run_cycle` calls it first
    and returns its result whenever there was one, so resolve-before-consider
    and "a closing cycle does not also open" are preserved by construction
    rather than by two implementations agreeing. There is no second resolver.

    `None` MEANS "NOTHING TO RESOLVE", NOT "NOTHING HAPPENED". A `CycleOutcome`
    is not invented for the flat-account case: `run_cycle` needs to fall
    through to its entry branch, and a caller that only monitors needs to say
    "there is no position here" — which is a different sentence from every
    outcome in that enum, and inventing a member for it would make `HOLDING`
    ambiguous between "a position is open and waiting" and "there is nothing".
    `None` is the same not-applicable idiom `orion.decision.rules` and
    `orion.risk.controls` already use for a check that did not apply.

    IT TAKES A `PortfolioRecord`, NOT A `PortfolioStore`, TO READ FROM. The
    record carries the revision the compare-and-swap needs, and taking it as an
    argument means `run_cycle` still loads the account exactly once — the
    extraction adds no second read, and the caller keeps the load-failure
    branch it already had.

    EXIT NEEDS NO NEW AUTHORITY, AND NONE IS ADDED. The stop and target being
    honoured here were proposed by the model and approved by the Risk Guardian
    at the moment the position was opened; reaching one is the execution of a
    permission already granted, not a request for a new one. So there is no
    control, no policy and no verdict on this path, `orion.risk` is not
    consulted, and `CloseReason` gains no member that could express a close
    nobody approved.

    PERSISTENCE IS THE EXISTING ORDER, THROUGH THE EXISTING WRITER: the account
    first, the journal second, and no `AuditChain` — the records that authorised
    this position were journaled by the cycle that opened it. A `HOLDING` result
    writes nothing at all, so monitoring at any frequency adds no revision to
    the account and no row to the journal.

    `bars` SELECTS THE RESOLVER, AND DEFAULTS TO THE ONE THAT WAS ALWAYS HERE
    (Mission 9 Phase 2). Without it this behaves exactly as Phase 1 did:
    last-price detection, no coverage, `run_cycle` unchanged. With it, the
    intrabar resolver scans every bar since the entry and catches a crossing
    that happened between two observations — the case a single last price
    cannot represent, because a price that touched a stop and came back leaves
    no trace in any close. Both paths share one persistence branch below, so
    there is no second way to write a close.

    COVERAGE IS REPORTED, NEVER ASSUMED. On the bar path the result carries a
    `ResolutionCoverage`, computed over exactly the bars the resolver acted on.
    A window that did not reach the entry, or that is missing bars inside
    itself, still resolves — a real stop must be honoured even when the data
    around it is imperfect — but it can never be presented as a clean hold.
    """
    stored = record.state
    position = stored.open_position
    if position is None:
        return None

    coverage: ResolutionCoverage | None = None
    triggering_bar: OHLCVBar | None = None
    try:
        if bars is None:
            resolved, closed = resolve_stop_take_profit(stored, price, policy=policy, now=now)
        else:
            coverage = window_coverage(bars, position)
            resolved, closed, triggering_bar = resolve_stop_take_profit_bars(
                stored, bars, policy=policy, now=now
            )
    except PaperExecutionError:
        return CycleResult(
            outcome=CycleOutcome.EXECUTION_REFUSED, portfolio=stored, coverage=coverage
        )

    if closed is None:
        # Marked to market but not closed. The projection is deliberately
        # not saved: it is derived, and writing a revision per cycle would
        # grow the account with rows that record no decision.
        #
        # `coverage` rides along so this HOLDING can be read correctly: on the
        # bar-aware path it is the difference between "nothing happened" and
        # "nothing that could be seen happened", and only the first of those is
        # what HOLDING's own docstring claims.
        return CycleResult(outcome=CycleOutcome.HOLDING, portfolio=resolved, coverage=coverage)

    # No chain: the records that authorised this position were journaled by
    # the cycle that *opened* it. A resolving caller's own analysis, if it ran
    # one at all, produced a different decision and a different verdict, and
    # neither of them closed anything -- the resolver did, from the price.
    return _persist(
        store=store,
        journal=journal,
        portfolio=resolved,
        expected_revision=record.revision,
        fill=None,
        closed_position=closed,
        outcome=CycleOutcome.CLOSED,
        chain=None,
        now=now,
        coverage=coverage,
        triggering_bar_at=None if triggering_bar is None else triggering_bar.timestamp,
    )


def run_cycle(
    *,
    decision: ProposedDecision,
    evidence: Evidence,
    reasoning: ReasoningOutput,
    thesis: MarketThesis | None = None,
    verdict: RiskVerdict | None,
    store: PortfolioStore,
    journal: JournalWriter,
    policy: PaperExecutionPolicy,
    risk_per_trade_fraction: float,
    now: datetime,
) -> CycleResult:
    """Run one forward paper cycle against `store`. Never raises.

    `verdict` is `None` when the Risk Guardian produced no verdict at all — an
    unreadable account, a symbol mismatch, no store wired. That is not a veto
    and is not treated as one; it is `NO_VERDICT`, and nothing is executed
    either way.

    `reasoning` is the `ReasoningOutput` `decision` was derived from. It is
    journaled, never re-read: nothing in this function branches on it, so a
    caller cannot change what this cycle does by passing a different one. The
    linkage between the two is `decision.reasoning_ref.content_hash`, checked
    once by `orion.decision.engine.decide` when the decision was built, and
    re-checkable by anyone holding the journal afterwards — which is exactly
    what recording both makes possible.

    `thesis` is the specialist evidence formed for the same analysis, or `None`
    when none was. It is journaled alongside the chain and **never read**: no
    branch in this function or in `_persist` looks at a field of it, so it
    cannot change what a cycle does. See this module's docstring.

    THE ORDER OF THE THREE BRANCHES IS THE CONTRACT. An open position is
    resolved before a new one is considered, exactly as `orion.backtest.engine`
    sequences the same two operations: a cycle that closed a position does not
    also open one, because the evidence that closed it is the same evidence the
    proposal was reasoned from, and acting twice on it would double-count.
    """
    try:
        record = store.load()
    except Exception:
        # Deliberately broad and deliberately message-free. `PortfolioStoreError`
        # embeds SQLAlchemy text that can name the database and its path, and
        # nothing here needs it to know the honest answer is "this cycle did
        # nothing". `portfolio` is `None` because there genuinely is no account
        # state to report -- never a fabricated flat one.
        return CycleResult(outcome=CycleOutcome.ACCOUNT_UNAVAILABLE, portfolio=None)

    stored = record.state

    # ------------------------------------------------ an open position first
    resolution = resolve_position(
        record=record,
        price=evidence,
        store=store,
        journal=journal,
        policy=policy,
        now=now,
    )
    if resolution is not None:
        return resolution

    # ------------------------------------------------------- a new position
    if verdict is None:
        return CycleResult(outcome=CycleOutcome.NO_VERDICT, portfolio=stored)

    if verdict.outcome is not RiskOutcome.APPROVED:
        # The Guardian's authority, honoured before anything is built.
        # `execute()` would refuse this too; refusing here means a veto costs
        # no object construction and reports as a verdict rather than a fault.
        return CycleResult(outcome=CycleOutcome.VETOED, portfolio=stored)

    # The account the Guardian actually reviewed is a marked-to-market
    # projection of the stored record, never the record itself. Reproduced with
    # the same pure function and the same inputs.
    try:
        marked = mark_to_market(stored, now=now, last_price=evidence.last_price)
    except ValueError:
        # A stored state timestamped after `now`. Refused rather than moved
        # backwards, matching the analysis layer's own handling.
        return CycleResult(outcome=CycleOutcome.PORTFOLIO_MISMATCH, portfolio=stored)

    if portfolio_state_fingerprint(marked) != verdict.portfolio_ref.content_hash:
        # Fails closed. `execute()` performs this check itself and would raise,
        # so this is not the only line of defence — it is the one that makes the
        # failure legible instead of an exception, and it must never be relaxed
        # into "execute anyway": the Guardian approved a specific account state,
        # and this is not it.
        return CycleResult(outcome=CycleOutcome.PORTFOLIO_MISMATCH, portfolio=stored)

    stop_loss = decision.reasoning_ref.stop_loss
    if stop_loss is None:
        # `RiskVerdict`'s own invariants make this unreachable for an APPROVED
        # verdict. Checked rather than asserted because the alternative is
        # computing a size from `None`.
        return CycleResult(outcome=CycleOutcome.NOT_TRADEABLE, portfolio=stored)

    # The same deterministic sizing function the Guardian's own
    # `position_size_within_limits` control used to approve this proposal.
    # Never AI-proposed, never re-derived from a different fraction.
    quantity = compute_position_size(
        marked.equity,
        evidence.last_price,
        stop_loss,
        risk_per_trade_fraction=risk_per_trade_fraction,
    )
    if quantity <= 0:
        return CycleResult(outcome=CycleOutcome.NOT_TRADEABLE, portfolio=marked)

    try:
        result = execute(
            decision,
            verdict,
            evidence,
            marked,
            quantity=quantity,
            policy=policy,
            now=now,
        )
    except PaperExecutionError:
        # Every linkage and geometry check `execute()` performs. Nothing was
        # written, because nothing has been saved yet.
        return CycleResult(outcome=CycleOutcome.EXECUTION_REFUSED, portfolio=marked)

    return _persist(
        store=store,
        journal=journal,
        portfolio=result.portfolio,
        expected_revision=record.revision,
        fill=result.fill,
        closed_position=None,
        outcome=CycleOutcome.EXECUTED,
        chain=AuditChain(
            evidence=evidence,
            reasoning=reasoning,
            decision=decision,
            verdict=verdict,
            thesis=thesis,
        ),
        now=now,
    )


def _persist(
    *,
    store: PortfolioStore,
    journal: JournalWriter,
    portfolio: PortfolioState,
    expected_revision: int,
    fill: Fill | None,
    closed_position: ClosedPosition | None,
    outcome: CycleOutcome,
    chain: AuditChain | None,
    now: datetime,
    coverage: ResolutionCoverage | None = None,
    triggering_bar_at: datetime | None = None,
) -> CycleResult:
    """Save the account, then journal what happened. Never the other way round.

    FINDING A, IMPLEMENTED. Mission 5 recorded that the store and the journal
    are independently committed transactions, and instructed a future
    orchestrator to treat the store as authoritative and persist it first. A
    crash after the save and before the journal write leaves a real account
    change with no audit row — recoverable, because the journal write is
    content-addressed and replaying it creates no duplicate. The reverse order
    would leave an audit row for a trade that never happened, which is not
    recoverable by anything.

    `chain` is the four records that authorised `fill`, or `None` when there is
    nothing to authorise. It is written inside the same `try` as the fill, so a
    journal that fails anywhere in the sequence reports `JOURNAL_UNAVAILABLE`
    once, for the whole write, rather than leaving the caller to work out how
    far it got.
    """
    try:
        store.save(portfolio, expected_revision=expected_revision)
    except Exception:
        # Includes `PortfolioStoreConcurrencyError` — another writer moved the
        # account under this cycle. Nothing is journaled: as far as any record
        # is concerned, this trade did not happen, which is the truth.
        return CycleResult(
            outcome=CycleOutcome.ACCOUNT_UNAVAILABLE,
            portfolio=portfolio,
            coverage=coverage,
        )

    try:
        # LEAF-FIRST, AND THE FILL LAST. Each record is written only after
        # everything it references is already stored, so an interrupted write
        # leaves a shorter chain, never a broken one: there is no point at which
        # a journaled record refers to a fingerprint the journal does not hold.
        # A `ClosedPosition` needs no chain here -- see `run_cycle`.
        chain_fingerprints: tuple[str, ...] = ()
        if chain is not None:
            thesis = chain.thesis
            chain_fingerprints = (
                # The thesis first: it references nothing, so no prefix of an
                # interrupted write can leave it pointing at a missing record.
                *((journal.record_thesis(thesis, recorded_at=now),) if thesis is not None else ()),
                journal.record_evidence(chain.evidence, recorded_at=now),
                journal.record_reasoning(chain.reasoning, recorded_at=now),
                journal.record_decision(chain.decision, recorded_at=now),
                journal.record_verdict(chain.verdict, recorded_at=now),
            )

        if fill is not None:
            fingerprint = journal.record_fill(fill, recorded_at=now)
        else:
            assert closed_position is not None  # one of the two, by construction
            fingerprint = journal.record_closed_position(closed_position, recorded_at=now)
    except Exception:
        # The account is already durable and correct. Reported honestly rather
        # than rolled back: there is no transaction spanning both stores, and
        # inventing one by re-saving the previous state would be a second write
        # that could itself fail. The trade stands; its audit row can be
        # replayed.
        # `audit_chain_fingerprints` stays empty even when part of the chain
        # went in: the audit record this cycle owed is incomplete either way,
        # and reporting a partial one as written would overstate what happened.
        return CycleResult(
            outcome=CycleOutcome.JOURNAL_UNAVAILABLE,
            portfolio=portfolio,
            fill=fill,
            closed_position=closed_position,
            coverage=coverage,
            triggering_bar_at=triggering_bar_at,
        )

    return CycleResult(
        outcome=outcome,
        portfolio=portfolio,
        fill=fill,
        closed_position=closed_position,
        journal_fingerprint=fingerprint,
        audit_chain_fingerprints=chain_fingerprints,
        coverage=coverage,
        triggering_bar_at=triggering_bar_at,
    )
