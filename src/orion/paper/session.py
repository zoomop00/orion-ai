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

IT DOES NOT IMPORT `orion.memory` EITHER, FOR A DIFFERENT REASON.
`orion.memory` already depends on `orion.paper` in three places
(`journal.py` and `sqlite_journal.py` import `Fill`/`ClosedPosition`,
`portfolio_store.py` imports `PortfolioStore`). Importing `DecisionJournal` back
would turn a clean one-way dependency into a cycle. `JournalWriter` below is the
narrow inversion of that dependency: two methods, structurally satisfied by the
existing `DecisionJournal` and `SqliteDecisionJournal` with no change to either.
It restates an interface, never an implementation, and a test asserts both real
journals still satisfy it so the two cannot drift apart.

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

NO VENUE, NO SCHEDULE, NO LOOP. One cycle per call, returning a value. There is
no retry, no sleep, no thread, no background work, and nothing that reaches a
broker — a paper account is a local simulation of one.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from orion.analysis.evidence import Evidence
from orion.decision.schemas import ProposedDecision
from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, Fill, PaperExecutionError
from orion.paper.store import PortfolioStore
from orion.risk.portfolio import (
    PortfolioState,
    compute_position_size,
    mark_to_market,
    portfolio_state_fingerprint,
)
from orion.risk.schemas import RiskOutcome, RiskVerdict


@runtime_checkable
class JournalWriter(Protocol):
    """The two journal methods a forward cycle needs, and no others.

    Deliberately narrower than `orion.memory.journal.DecisionJournal`: this
    module produces `Fill`s and `ClosedPosition`s and nothing else, so it
    declares no ability to write evidence, reasoning, decisions or verdicts. A
    caller may pass a full journal — `SqliteDecisionJournal` satisfies this
    structurally — but this module cannot use the rest of it even by accident.

    Both methods are content-addressed and idempotent in the real
    implementation, which is what makes the crash window in `run_cycle`
    recoverable by replay. See that function's `PERSISTENCE ORDER` note.
    """

    def record_fill(self, fill: Fill, *, recorded_at: datetime) -> str:
        """Persist `fill`, returning its content fingerprint. Idempotent."""
        ...

    def record_closed_position(self, closed: ClosedPosition, *, recorded_at: datetime) -> str:
        """Persist `closed`, returning its content fingerprint. Idempotent."""
        ...


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
    """

    __slots__ = ("outcome", "portfolio", "fill", "closed_position", "journal_fingerprint")

    def __init__(
        self,
        *,
        outcome: CycleOutcome,
        portfolio: PortfolioState | None,
        fill: Fill | None = None,
        closed_position: ClosedPosition | None = None,
        journal_fingerprint: str | None = None,
    ) -> None:
        self.outcome = outcome
        self.portfolio = portfolio
        self.fill = fill
        self.closed_position = closed_position
        self.journal_fingerprint = journal_fingerprint

    @property
    def account_was_written(self) -> bool:
        """Whether this cycle durably changed the account."""
        return self.outcome in _PERSISTED

    @property
    def executed(self) -> bool:
        """Whether a position was opened. Exactly when there is a `Fill`."""
        return self.fill is not None


def run_cycle(
    *,
    decision: ProposedDecision,
    evidence: Evidence,
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
    if stored.open_position is not None:
        try:
            resolved, closed = resolve_stop_take_profit(stored, evidence, policy=policy, now=now)
        except PaperExecutionError:
            return CycleResult(outcome=CycleOutcome.EXECUTION_REFUSED, portfolio=stored)

        if closed is None:
            # Marked to market but not closed. The projection is deliberately
            # not saved: it is derived, and writing a revision per cycle would
            # grow the account with rows that record no decision.
            return CycleResult(outcome=CycleOutcome.HOLDING, portfolio=resolved)

        return _persist(
            store=store,
            journal=journal,
            portfolio=resolved,
            expected_revision=record.revision,
            fill=None,
            closed_position=closed,
            outcome=CycleOutcome.CLOSED,
            now=now,
        )

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
    now: datetime,
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
    """
    try:
        store.save(portfolio, expected_revision=expected_revision)
    except Exception:
        # Includes `PortfolioStoreConcurrencyError` — another writer moved the
        # account under this cycle. Nothing is journaled: as far as any record
        # is concerned, this trade did not happen, which is the truth.
        return CycleResult(outcome=CycleOutcome.ACCOUNT_UNAVAILABLE, portfolio=portfolio)

    try:
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
        return CycleResult(
            outcome=CycleOutcome.JOURNAL_UNAVAILABLE,
            portfolio=portfolio,
            fill=fill,
            closed_position=closed_position,
        )

    return CycleResult(
        outcome=outcome,
        portfolio=portfolio,
        fill=fill,
        closed_position=closed_position,
        journal_fingerprint=fingerprint,
    )
