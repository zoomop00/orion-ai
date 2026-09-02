"""The forward paper cycle: Risk Guardian authority, and persistence ordering.

THE ASSERTIONS THAT MATTER MOST ARE THE NEGATIVE AND THE ORDERED ONES. A veto
must produce no `Fill`; a failed `save()` must produce no journal row; and the
account must be written before the journal, always, because Mission 5's Finding
A established that the two stores commit independently and only that ordering is
recoverable by replay. Those are checked by recording the real call sequence
rather than by inspecting the code.

NOTHING HERE FABRICATES A VALUE. The fills, quantities and balances below come
from `orion.paper.execution` acting on a real `RiskVerdict` produced by the real
`orion.risk.guardian.review`. Where a test needs a veto it changes the policy,
never the verdict object -- a hand-edited verdict would prove the session
respects a shape rather than an authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from orion.analysis.evidence import Evidence, build_evidence, evidence_fingerprint
from orion.decision.engine import decide
from orion.decision.schemas import ProposedDecision, decision_fingerprint
from orion.memory.engine import create_memory_engine
from orion.memory.journal import DecisionJournal
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, Fill
from orion.paper.session import (
    CycleOutcome,
    JournalWriter,
    run_cycle,
)
from orion.paper.store import (
    InMemoryPortfolioStore,
    PortfolioRecord,
    PortfolioStore,
    PortfolioStoreError,
)
from orion.provenance import canonical_hash
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.guardian import review
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import PortfolioState, initial_portfolio_state, mark_to_market
from orion.risk.schemas import RiskOutcome, RiskVerdict, risk_verdict_fingerprint
from orion.thesis.schemas import MarketThesis

from ..orchestrator.conftest import (
    LAST_BAR_TIME,
    NOW,
    FakeReasoningProvider,
    make_bars,
    make_snapshot,
)
from ..thesis.builders import build_thesis

SYMBOL = "XAUUSD"
#: The four chain writes `_persist` makes before a fill, in the order it makes
#: them: each record is written only after everything it references is stored.
_CHAIN_CALLS = [
    "record_evidence",
    "record_reasoning",
    "record_decision",
    "record_verdict",
]
START = LAST_BAR_TIME
POLICY = PaperExecutionPolicy()
RISK_FRACTION = 0.01


# ------------------------------------------------------------- the fixtures


def make_evidence(*, last_close: float | None = None) -> Evidence:
    """Real `Evidence` from the orchestrator suite's own proven bar series.

    Reused rather than rebuilt so the structure, momentum and timestamps here
    are the ones every other suite reasons about -- and so `NOW` lines up with
    the last bar, which the freshness controls require.
    """
    bars = make_bars()
    if last_close is not None:
        final = bars[-1]
        bars[-1] = final.model_copy(
            update={
                "close": last_close,
                "high": max(final.high, last_close),
                "low": min(final.low, last_close),
            }
        )
    return build_evidence(make_snapshot(bars=bars))


def make_reasoning(evidence: Evidence, **kwargs: object) -> ReasoningOutput:
    """A real, schema-valid proposal from the orchestrator suite's own fake.

    Deliberately not hand-built: `ReasoningVerdict` has cross-field invariants
    and a closed `reason_codes` enum, and a hand-written double would drift
    away from them. The provider stamps the real evidence fingerprint, so
    `decide()`'s linkage check stays live rather than bypassed.
    """
    return FakeReasoningProvider(**kwargs).reason(evidence)  # type: ignore[arg-type]


def make_decision(evidence: Evidence, reasoning: ReasoningOutput) -> ProposedDecision:
    return decide(evidence, reasoning, analysis_id="analysis-test", now=NOW)


def approving_policy() -> RiskPolicy:
    return RiskPolicy(kill_switch=KillSwitchState.DISENGAGED)


def make_verdict(
    decision: ProposedDecision,
    evidence: Evidence,
    portfolio: PortfolioState,
    *,
    policy: RiskPolicy | None = None,
) -> RiskVerdict:
    """A real verdict from the real Risk Guardian, against the marked
    projection the orchestrator would have reviewed."""
    marked = mark_to_market(portfolio, now=NOW, last_price=evidence.last_price)
    return review(
        decision, evidence, policy=policy or approving_policy(), portfolio=marked, now=NOW
    )


def fresh_account(balance: float = 100_000.0) -> PortfolioState:
    return initial_portfolio_state(symbol=SYMBOL, starting_balance=balance, now=START)


# ------------------------------------------------------------- the recorders


class RecordingJournal:
    """A `JournalWriter` that records the order of what it was asked to do.

    `fail_on` names the one method that raises, so a test can fail the journal
    part-way through the chain rather than only at the first call -- which is
    how the "a partial chain is never reported as written" guarantee is
    actually exercised. `fail=True` keeps its old meaning of "every write
    fails".
    """

    def __init__(self, *, calls: list[str], fail: bool = False, fail_on: str | None = None) -> None:
        self.calls = calls
        self.fail = fail
        self.fail_on = fail_on
        self.evidence: list[Evidence] = []
        self.reasoning: list[ReasoningOutput] = []
        self.decisions: list[ProposedDecision] = []
        self.verdicts: list[RiskVerdict] = []
        self.theses: list[MarketThesis] = []
        self.fills: list[Fill] = []
        self.closed: list[ClosedPosition] = []
        self.recorded_at: list[datetime] = []

    def _enter(self, method: str, recorded_at: datetime) -> None:
        self.calls.append(method)
        self.recorded_at.append(recorded_at)
        if self.fail or self.fail_on == method:
            raise RuntimeError("journal is down")

    def record_thesis(self, thesis: MarketThesis, *, recorded_at: datetime) -> str:
        self._enter("record_thesis", recorded_at)
        self.theses.append(thesis)
        return "thesis-fingerprint"

    def record_evidence(self, evidence: Evidence, *, recorded_at: datetime) -> str:
        self._enter("record_evidence", recorded_at)
        self.evidence.append(evidence)
        return "evidence-fingerprint"

    def record_reasoning(self, reasoning: ReasoningOutput, *, recorded_at: datetime) -> str:
        self._enter("record_reasoning", recorded_at)
        self.reasoning.append(reasoning)
        return "reasoning-fingerprint"

    def record_decision(self, decision: ProposedDecision, *, recorded_at: datetime) -> str:
        self._enter("record_decision", recorded_at)
        self.decisions.append(decision)
        return "decision-fingerprint"

    def record_verdict(self, verdict: RiskVerdict, *, recorded_at: datetime) -> str:
        self._enter("record_verdict", recorded_at)
        self.verdicts.append(verdict)
        return "verdict-fingerprint"

    def record_fill(self, fill: Fill, *, recorded_at: datetime) -> str:
        self._enter("record_fill", recorded_at)
        self.fills.append(fill)
        return "fill-fingerprint"

    def record_closed_position(self, closed: ClosedPosition, *, recorded_at: datetime) -> str:
        self._enter("record_closed_position", recorded_at)
        self.closed.append(closed)
        return "closed-fingerprint"


class RecordingStore(PortfolioStore):
    """An `InMemoryPortfolioStore` that records call order and can fail."""

    def __init__(self, state: PortfolioState, *, calls: list[str], fail_save: bool = False) -> None:
        self._inner = InMemoryPortfolioStore()
        self._inner.initialize(state)
        self.calls = calls
        self.fail_save = fail_save

    def load(self) -> PortfolioRecord:
        self.calls.append("load")
        return self._inner.load()

    def save(self, state: PortfolioState, *, expected_revision: int) -> PortfolioRecord:
        self.calls.append("save")
        if self.fail_save:
            raise PortfolioStoreError("disk is full at ./orion.db")
        return self._inner.save(state, expected_revision=expected_revision)

    def initialize(self, state: PortfolioState) -> PortfolioRecord:
        return self._inner.initialize(state)


def cycle(
    *,
    store: PortfolioStore,
    journal: JournalWriter,
    evidence: Evidence | None = None,
    policy: RiskPolicy | None = None,
    verdict: RiskVerdict | None = None,
    use_verdict: bool = True,
    thesis: MarketThesis | None = None,
):
    """Run one cycle against real engines, with only the policy varied.

    `thesis` defaults to `None` -- the shape every pre-Mission-8-v2.2 caller
    has, and the one a backtest or a credential-less deployment still has, so
    the existing assertions keep testing the case they were written for.
    """
    evidence = evidence or make_evidence()
    reasoning = make_reasoning(evidence)
    decision = make_decision(evidence, reasoning)
    if use_verdict and verdict is None:
        verdict = make_verdict(decision, evidence, store.load().state, policy=policy)
    return run_cycle(
        decision=decision,
        evidence=evidence,
        reasoning=reasoning,
        thesis=thesis,
        verdict=verdict if use_verdict else None,
        store=store,
        journal=journal,
        policy=POLICY,
        risk_per_trade_fraction=RISK_FRACTION,
        now=NOW,
    )


# --------------------------------------------------------- guardian authority


class TestTheRiskGuardianHasAuthority:
    def test_an_approved_verdict_opens_exactly_one_position(self) -> None:
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.EXECUTED
        assert result.executed
        assert len(journal.fills) == 1
        assert calls.count("save") == 1
        assert calls.count("record_fill") == 1

    def test_a_veto_executes_nothing_and_writes_nothing(self) -> None:
        """The default `RiskPolicy()` blocks every proposal -- the fail-safe."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal, policy=RiskPolicy())

        assert result.outcome is CycleOutcome.VETOED
        assert result.fill is None
        assert journal.fills == []
        assert "save" not in calls
        assert "record_fill" not in calls

    def test_an_absent_verdict_executes_nothing(self) -> None:
        """`None` is "the Guardian did not answer", which is not approval."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal, use_verdict=False)

        assert result.outcome is CycleOutcome.NO_VERDICT
        assert result.fill is None
        assert "save" not in calls
        assert "record_fill" not in calls

    @pytest.mark.parametrize("outcome", [RiskOutcome.VETOED, RiskOutcome.NO_TRADE_PROPOSED])
    def test_no_non_approved_outcome_can_ever_execute(self, outcome: RiskOutcome) -> None:
        calls: list[str] = []
        state = fresh_account()
        store = RecordingStore(state, calls=calls)
        journal = RecordingJournal(calls=calls)

        evidence = make_evidence()
        reasoning = make_reasoning(evidence)
        decision = make_decision(evidence, reasoning)
        approved = make_verdict(decision, evidence, state)
        # Only the outcome is changed; every linkage stays real.
        tampered = approved.model_copy(update={"outcome": outcome})

        result = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=tampered,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=RISK_FRACTION,
            now=NOW,
        )

        assert result.outcome is CycleOutcome.VETOED
        assert result.fill is None
        assert calls == ["load"]


# -------------------------------------------------------- persistence order


class TestPersistenceOrdering:
    def test_the_account_is_saved_before_the_journal_is_written(self) -> None:
        """Finding A's instruction, asserted as a sequence rather than trusted."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.EXECUTED
        assert calls == ["load", "load", "save", *_CHAIN_CALLS, "record_fill"]
        assert calls.index("save") < calls.index("record_fill")

    def test_a_failed_save_never_reaches_the_journal(self) -> None:
        """The most important negative in the file: no audit row may exist for
        a trade the account does not reflect."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls, fail_save=True)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.ACCOUNT_UNAVAILABLE
        assert "record_fill" not in calls
        assert journal.fills == []
        assert result.journal_fingerprint is None
        assert not result.account_was_written

    def test_a_failed_journal_reports_honestly_and_keeps_the_trade(self) -> None:
        """The account is already durable. Reported, never rolled back and
        never presented as a success."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls, fail=True)

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.JOURNAL_UNAVAILABLE
        assert result.fill is not None  # the trade genuinely happened
        assert result.journal_fingerprint is None
        assert result.account_was_written
        # A journal that fails on every write now fails on the *first* chain
        # record, so the fill is never even attempted. The guarantee under test
        # is unchanged: the account was saved before any of this, and a failed
        # journal is reported rather than rolled back.
        assert calls == ["load", "load", "save", "record_evidence"]
        assert result.audit_chain_fingerprints == ()

    def test_a_chain_that_fails_part_way_is_never_reported_as_written(self) -> None:
        """The honest half of `JOURNAL_UNAVAILABLE`: some of the chain went in.

        `record_evidence` and `record_reasoning` succeeded and are durable --
        that cannot be undone, and re-writing them would be a second write that
        could itself fail. What must not happen is claiming the audit record
        exists, so `audit_chain_fingerprints` stays empty rather than reporting
        the two that landed. Replaying the cycle re-writes all six
        content-addressed records and the two already stored are no-ops.
        """
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls, fail_on="record_decision")

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.JOURNAL_UNAVAILABLE
        assert result.account_was_written
        assert result.audit_chain_fingerprints == ()
        assert result.journal_fingerprint is None
        assert calls == [
            "load",
            "load",
            "save",
            "record_evidence",
            "record_reasoning",
            "record_decision",
        ]
        assert "record_fill" not in calls
        assert journal.fills == []

    def test_an_unreadable_account_does_nothing_at_all(self) -> None:
        class UnreadableStore(RecordingStore):
            def load(self) -> PortfolioRecord:
                self.calls.append("load")
                raise PortfolioStoreError("could not open ./orion.db")

        calls: list[str] = []
        store = UnreadableStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        evidence = make_evidence()
        reasoning = make_reasoning(evidence)
        decision = make_decision(evidence, reasoning)

        result = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=None,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=RISK_FRACTION,
            now=NOW,
        )

        assert result.outcome is CycleOutcome.ACCOUNT_UNAVAILABLE
        # No state is invented for an account that could not be read.
        assert result.portfolio is None
        assert "save" not in calls
        assert "record_fill" not in calls


# ------------------------------------------------------- marked-projection


class TestTheReviewedAccountIsTheExecutedAccount:
    def test_a_verdict_for_a_different_account_state_is_refused(self) -> None:
        """Fails closed. The Guardian approved a specific account; a store
        holding a different one must not be traded against."""
        calls: list[str] = []
        reviewed = fresh_account(100_000.0)
        store = RecordingStore(fresh_account(55_555.0), calls=calls)
        journal = RecordingJournal(calls=calls)

        evidence = make_evidence()
        reasoning = make_reasoning(evidence)
        decision = make_decision(evidence, reasoning)
        verdict = make_verdict(decision, evidence, reviewed)

        result = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=verdict,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=RISK_FRACTION,
            now=NOW,
        )

        assert result.outcome is CycleOutcome.PORTFOLIO_MISMATCH
        assert result.fill is None
        assert "save" not in calls
        assert "record_fill" not in calls

    def test_the_reconstructed_projection_matches_the_verdicts_reference(self) -> None:
        """The load-bearing reconstruction: the session marks the stored state
        exactly as the orchestrator did, so `execute()`'s own fingerprint check
        passes and a real trade is possible at all."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal)

        # If the reconstruction were wrong this would be PORTFOLIO_MISMATCH or
        # EXECUTION_REFUSED, never EXECUTED.
        assert result.outcome is CycleOutcome.EXECUTED


# ------------------------------------------------------------ closing a run


class TestClosingAPosition:
    def _open_one(self, calls: list[str]) -> RecordingStore:
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)
        opened = cycle(store=store, journal=journal)
        assert opened.outcome is CycleOutcome.EXECUTED
        return store

    def test_holding_writes_nothing_when_no_level_is_reached(self) -> None:
        calls: list[str] = []
        store = self._open_one(calls)
        journal = RecordingJournal(calls=calls)
        calls.clear()

        result = cycle(store=store, journal=journal, evidence=make_evidence())

        assert result.outcome is CycleOutcome.HOLDING
        assert result.closed_position is None
        assert "save" not in calls
        assert "record_closed_position" not in calls

    def test_a_genuine_take_profit_is_recorded_as_a_closed_position(self) -> None:
        """`record_closed_position` is called only for a close the engine
        actually produced -- never invented by this module."""
        calls: list[str] = []
        store = self._open_one(calls)
        journal = RecordingJournal(calls=calls)
        calls.clear()

        held = store.load().state.open_position
        assert held is not None
        # Price gaps through the take-profit: a real close, from the engine.
        far = make_evidence(last_close=held.take_profit + 25.0)

        result = cycle(store=store, journal=journal, evidence=far)

        assert result.outcome is CycleOutcome.CLOSED
        assert result.closed_position is not None
        assert result.closed_position.close_reason.value == "take_profit_hit"
        assert len(journal.closed) == 1
        assert journal.fills == []
        # Same ordering guarantee as a fill: account first, journal second.
        assert calls.index("save") < calls.index("record_closed_position")
        assert "record_fill" not in calls

    def test_a_closing_cycle_does_not_also_open_a_new_position(self) -> None:
        calls: list[str] = []
        store = self._open_one(calls)
        journal = RecordingJournal(calls=calls)

        held = store.load().state.open_position
        assert held is not None
        result = cycle(
            store=store, journal=journal, evidence=make_evidence(last_close=held.take_profit + 25.0)
        )

        assert result.outcome is CycleOutcome.CLOSED
        assert result.fill is None
        assert result.portfolio is not None
        assert result.portfolio.open_position is None


# -------------------------------------------------------------- idempotency


class TestJournalReplayIsIdempotent:
    def test_recording_the_same_fill_twice_creates_one_row(self, tmp_path) -> None:
        """The crash window between `save()` and `record_fill()` is only
        recoverable if replay is safe. It is, by content addressing."""
        engine = create_memory_engine(str(tmp_path / "j.db"))
        try:
            journal = SqliteDecisionJournal(engine)
            store = SqlitePortfolioStore(create_memory_engine(str(tmp_path / "p.db")))
            store.initialize(fresh_account())

            result = cycle(store=store, journal=journal)
            assert result.outcome is CycleOutcome.EXECUTED
            assert result.fill is not None
            first = result.journal_fingerprint

            # Replay, exactly as a restart after a crash would.
            second = journal.record_fill(result.fill, recorded_at=NOW)
            third = journal.record_fill(result.fill, recorded_at=NOW + timedelta(hours=1))

            assert first == second == third
            assert journal.get_fill(str(first)) == result.fill
        finally:
            engine.dispose()

    def test_the_same_fingerprint_with_different_content_still_fails_closed(self, tmp_path) -> None:
        """Idempotency must not have weakened the existing integrity check."""
        from sqlalchemy import update

        from orion.memory.journal import DecisionJournalIntegrityError
        from orion.memory.schema import fill_table

        engine = create_memory_engine(str(tmp_path / "j.db"))
        try:
            journal = SqliteDecisionJournal(engine)
            store = InMemoryPortfolioStore()
            store.initialize(fresh_account())
            result = cycle(store=store, journal=journal)
            assert result.fill is not None
            fingerprint = result.journal_fingerprint

            # Corrupt the stored payload behind the same fingerprint.
            with engine.begin() as conn:
                conn.execute(
                    update(fill_table)
                    .where(fill_table.c.fingerprint == fingerprint)
                    .values(payload='{"tampered": true}')
                )

            with pytest.raises(DecisionJournalIntegrityError):
                journal.record_fill(result.fill, recorded_at=NOW)
        finally:
            engine.dispose()


# ------------------------------------------------ the chain that authorised it


class TestTheAuditChainIsJournaled:
    """Phase 2b journaled a `Fill` and nothing that explained it.

    `orion.reflection.analysis.reflect_on_closed_position` walks `ClosedPosition
    -> RiskVerdict -> ProposedDecision` and fails closed on a missing link, so
    until this chain was written Reflection could not run on a single trade this
    system had actually made. These are the tests that keep it written.
    """

    def test_an_executed_cycle_journals_the_four_records_that_authorised_it(self) -> None:
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.EXECUTED
        assert len(journal.evidence) == 1
        assert len(journal.reasoning) == 1
        assert len(journal.decisions) == 1
        assert len(journal.verdicts) == 1
        assert len(result.audit_chain_fingerprints) == 4

    def test_the_chain_is_written_leaf_first_and_before_the_fill(self) -> None:
        """THE ORDER IS THE CONTRACT, not an implementation detail.

        Each record references only records already stored, so an interrupted
        write leaves a shorter chain rather than a broken one: at no point does
        a journaled record point at a fingerprint the journal does not hold.
        Asserted as a recorded call sequence, not inferred from reading the code.
        """
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        cycle(store=store, journal=journal)

        assert calls == ["load", "load", "save", *_CHAIN_CALLS, "record_fill"]

    def test_the_journaled_objects_are_the_ones_the_engines_produced(self) -> None:
        """By identity, not equality: nothing is rebuilt, copied or re-derived
        on its way to the journal."""
        calls: list[str] = []
        state = fresh_account()
        store = RecordingStore(state, calls=calls)
        journal = RecordingJournal(calls=calls)

        evidence = make_evidence()
        reasoning = make_reasoning(evidence)
        decision = make_decision(evidence, reasoning)
        verdict = make_verdict(decision, evidence, state)

        result = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=verdict,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=RISK_FRACTION,
            now=NOW,
        )

        assert journal.evidence[0] is evidence
        assert journal.reasoning[0] is reasoning
        assert journal.decisions[0] is decision
        assert journal.verdicts[0] is verdict
        assert result.fill is not None
        assert journal.fills[0] is result.fill

    def test_the_chain_links_up_by_fingerprint(self) -> None:
        """The reason the chain is worth writing: the four records reference
        each other, and `orion.reflection` walks exactly those references."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal)

        decision = journal.decisions[0]
        verdict = journal.verdicts[0]
        assert verdict.decision_ref.content_hash == decision_fingerprint(decision)
        assert decision.evidence_ref.content_hash == evidence_fingerprint(journal.evidence[0])
        assert decision.reasoning_ref.content_hash == canonical_hash(journal.reasoning[0])
        assert result.fill is not None
        assert result.fill.risk_verdict_fingerprint == risk_verdict_fingerprint(verdict)

    def test_every_record_carries_the_same_recorded_at(self) -> None:
        """One cycle is one instant. `recorded_at` is the caller's `now`,
        never `datetime.now()` taken afresh per write."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        cycle(store=store, journal=journal)

        assert journal.recorded_at == [NOW] * 5

    def test_a_veto_still_journals_absolutely_nothing(self) -> None:
        """Deliberate scope: the chain is written on the executed path only.

        A refused proposal is not journaled, so a scheduled `orion paper` does
        not accumulate a chain per invocation for trades that never happened.
        This is the assertion that stops that decision drifting silently.
        """
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(
            store=store,
            journal=journal,
            policy=RiskPolicy(kill_switch=KillSwitchState.ENGAGED),
        )

        assert result.outcome is CycleOutcome.VETOED

        # Two loads: the harness reads the account to build a real verdict
        # against it, then `run_cycle` reads it again. No write of any kind.
        assert calls == ["load", "load"]
        assert result.audit_chain_fingerprints == ()
        assert journal.evidence == []
        assert journal.reasoning == []
        assert journal.decisions == []
        assert journal.verdicts == []

    def test_closing_a_position_writes_no_second_chain(self) -> None:
        """The records that authorised a position were journaled when it was
        opened. The closing cycle's own decision and verdict did not close
        anything -- `resolve_stop_take_profit` did, from the price -- so
        journaling them here would attribute the close to a proposal that had
        no part in it."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)
        opened = cycle(store=store, journal=journal)
        assert opened.outcome is CycleOutcome.EXECUTED
        assert opened.portfolio is not None
        held = opened.portfolio.open_position
        assert held is not None
        calls.clear()

        result = cycle(
            store=store,
            journal=journal,
            evidence=make_evidence(last_close=held.take_profit + 1.0),
            use_verdict=False,
        )

        assert result.outcome is CycleOutcome.CLOSED
        assert calls == ["load", "save", "record_closed_position"]
        assert result.audit_chain_fingerprints == ()
        assert len(journal.verdicts) == 1  # still just the opening one

    def test_a_real_journal_stores_the_chain_and_reflection_can_walk_it(self, tmp_path) -> None:
        """END TO END, ON DISK. Not a recorder: a real `SqliteDecisionJournal`,
        a real open-then-close, and the real `orion.reflection` reading it back.

        This is the whole milestone in one test. Before this phase the same
        sequence produced a `ClosedPosition` whose verdict was in no journal, so
        `reflect_on_closed_position` raised on an incomplete chain.
        """
        from orion.reflection.analysis import reflect_on_closed_position

        engine = create_memory_engine(str(tmp_path / "acct.db"))
        try:
            store = SqlitePortfolioStore(engine)
            journal = SqliteDecisionJournal(engine)
            store.initialize(fresh_account())

            opened = cycle(store=store, journal=journal)
            assert opened.outcome is CycleOutcome.EXECUTED
            assert opened.portfolio is not None
            held = opened.portfolio.open_position
            assert held is not None

            closed = cycle(
                store=store,
                journal=journal,
                evidence=make_evidence(last_close=held.take_profit + 1.0),
                use_verdict=False,
            )
            assert closed.outcome is CycleOutcome.CLOSED
            assert closed.closed_position is not None

            reflection = reflect_on_closed_position(
                journal, canonical_hash(closed.closed_position), now=NOW
            )
            assert reflection.close_reason is closed.closed_position.close_reason
            assert reflection.realized_pnl == closed.closed_position.realized_pnl
            # The opening Fill was matched, which only works because the fill
            # and the verdict it references are both really in the journal.
            assert opened.fill is not None
            assert reflection.opening_fee == opened.fill.fee
            assert reflection.predicted_stop_loss == held.stop_loss
        finally:
            engine.dispose()

    def test_replaying_the_same_chain_adds_no_duplicate_rows(self, tmp_path) -> None:
        """The crash window in `_persist` is recoverable only if replay is a
        no-op. Every chain record is content-addressed, so re-writing an
        identical one returns the same fingerprint and stores one row."""
        engine = create_memory_engine(str(tmp_path / "acct.db"))
        try:
            journal = SqliteDecisionJournal(engine)
            state = fresh_account()
            store = SqlitePortfolioStore(engine)
            store.initialize(state)

            evidence = make_evidence()
            reasoning = make_reasoning(evidence)
            decision = make_decision(evidence, reasoning)
            verdict = make_verdict(decision, evidence, state)

            first = [
                journal.record_evidence(evidence, recorded_at=NOW),
                journal.record_reasoning(reasoning, recorded_at=NOW),
                journal.record_decision(decision, recorded_at=NOW),
                journal.record_verdict(verdict, recorded_at=NOW),
            ]
            result = run_cycle(
                decision=decision,
                evidence=evidence,
                reasoning=reasoning,
                verdict=verdict,
                store=store,
                journal=journal,
                policy=POLICY,
                risk_per_trade_fraction=RISK_FRACTION,
                now=NOW,
            )

            assert result.outcome is CycleOutcome.EXECUTED
            assert list(result.audit_chain_fingerprints) == first
            assert journal.get_verdict(first[3]) == verdict
            assert journal.get_decision(first[2]) == decision
        finally:
            engine.dispose()


# ----------------------------------------------- the thesis, carried and blind


class TestTheThesisIsJournaledWithTheChain:
    """Mission 8 v2.2's seventh record, written by the one writer that may.

    THE TWO THINGS THAT HAVE TO BE TRUE AT ONCE. The thesis must reach the
    journal -- advisory evidence that never got stored would leave reflection
    with nothing to recover -- and it must not reach anything else. These
    tests hold both ends: the record goes in, first and whole, and the module
    that carries it there is proven blind to its contents by
    `test_the_thesis_is_carried_and_never_read` below.
    """

    def test_an_executed_cycle_journals_the_thesis_it_was_given(self) -> None:
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)
        thesis = build_thesis()

        result = cycle(store=store, journal=journal, thesis=thesis)

        assert result.outcome is CycleOutcome.EXECUTED
        assert journal.theses == [thesis]
        assert len(result.audit_chain_fingerprints) == 5

    def test_the_thesis_is_written_first_of_all(self) -> None:
        """IT REFERENCES NOTHING, SO IT GOES IN AHEAD OF EVERYTHING.

        The chain is written leaf-first so an interrupted write leaves a
        shorter chain rather than a broken one. A `MarketThesis` holds no
        fingerprint of any other record, so it is the deepest leaf there is --
        and putting it first means no prefix of an interrupted write can leave
        a stored record pointing at a fingerprint the journal does not hold.
        """
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        cycle(store=store, journal=journal, thesis=build_thesis())

        # The same sequence `test_the_chain_is_written_leaf_first_and_before_
        # the_fill` pins, with the thesis inserted ahead of the chain.
        assert calls == ["load", "load", "save", "record_thesis", *_CHAIN_CALLS, "record_fill"]

    def test_the_account_is_still_saved_before_anything_is_journaled(self) -> None:
        """Finding A, re-asserted with the new record in the sequence: the
        seventh write must not have moved the store/journal ordering."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        cycle(store=store, journal=journal, thesis=build_thesis())

        assert calls.index("save") < calls.index("record_thesis")

    def test_the_journaled_thesis_is_the_object_it_was_handed(self) -> None:
        """Not a copy, not a re-validation, not a reconstruction: the exact
        object the caller passed, exactly as `test_the_session_passes_the_
        engines_own_objects_through_untouched` requires of a `Fill`."""
        journal = RecordingJournal(calls=[])
        store = InMemoryPortfolioStore()
        store.initialize(fresh_account())
        thesis = build_thesis(with_macro=True)

        cycle(store=store, journal=journal, thesis=thesis)

        assert journal.theses[0] is thesis

    def test_a_cycle_with_no_thesis_journals_no_thesis(self) -> None:
        """`None` is a real state, not a defect: a backtest forms no thesis,
        and neither does an analysis where both specialists came back empty.
        The chain is then exactly the four records it has always been."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal, thesis=None)

        assert journal.theses == []
        assert "record_thesis" not in calls
        assert len(result.audit_chain_fingerprints) == 4

    def test_a_veto_journals_no_thesis_either(self) -> None:
        """The thesis rides with the chain that authorised a fill. No fill, no
        chain, no thesis -- advisory evidence is not a reason to start writing
        rows for a trade that never happened."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls)

        result = cycle(
            store=store,
            journal=journal,
            policy=RiskPolicy(kill_switch=KillSwitchState.ENGAGED),
            thesis=build_thesis(),
        )

        assert result.outcome is CycleOutcome.VETOED
        assert journal.theses == []
        assert result.audit_chain_fingerprints == ()

    def test_a_journal_that_fails_on_the_thesis_reports_one_incomplete_write(self) -> None:
        """The first write in the sequence is now the thesis, so this is the
        earliest point the journal can fail. The account is already durable and
        stays so; the cycle reports JOURNAL_UNAVAILABLE once, for the whole
        write, and `audit_chain_fingerprints` stays empty rather than claiming
        a partial chain."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls)
        journal = RecordingJournal(calls=calls, fail_on="record_thesis")

        result = cycle(store=store, journal=journal, thesis=build_thesis())

        assert result.outcome is CycleOutcome.JOURNAL_UNAVAILABLE
        assert result.audit_chain_fingerprints == ()
        assert result.fill is not None
        assert "save" in calls

    def test_a_failed_account_save_journals_no_thesis(self) -> None:
        """Finding A's other half. If the account never changed, nothing about
        the trade is recorded -- including the specialists' reading, which
        would otherwise be the one surviving trace of a trade that did not
        happen."""
        calls: list[str] = []
        store = RecordingStore(fresh_account(), calls=calls, fail_save=True)
        journal = RecordingJournal(calls=calls)

        result = cycle(store=store, journal=journal, thesis=build_thesis())

        assert result.outcome is CycleOutcome.ACCOUNT_UNAVAILABLE
        assert journal.theses == []
        assert not any(call.startswith("record_") for call in calls)

    def test_replaying_an_identical_thesis_adds_no_duplicate_row(self, tmp_path) -> None:
        """Idempotency, against the real journal: the crash window between the
        account save and the journal write is recoverable only if replaying the
        write is a no-op, and a `MarketThesis` is content-addressed like every
        other record."""
        engine = create_memory_engine(str(tmp_path / "acct.db"))
        try:
            journal = SqliteDecisionJournal(engine)
            state = fresh_account()
            store = SqlitePortfolioStore(engine)
            store.initialize(state)
            thesis = build_thesis()
            first = journal.record_thesis(thesis, recorded_at=NOW)

            evidence = make_evidence()
            reasoning = make_reasoning(evidence)
            decision = make_decision(evidence, reasoning)
            result = run_cycle(
                decision=decision,
                evidence=evidence,
                reasoning=reasoning,
                thesis=thesis,
                verdict=make_verdict(decision, evidence, state),
                store=store,
                journal=journal,
                policy=POLICY,
                risk_per_trade_fraction=RISK_FRACTION,
                now=NOW,
            )

            assert result.outcome is CycleOutcome.EXECUTED
            assert result.audit_chain_fingerprints[0] == first
            assert journal.get_thesis(first) == thesis
            assert journal.list_theses_by_analysis_id(thesis.analysis_id) == (thesis,)
        finally:
            engine.dispose()


def test_the_thesis_is_carried_and_never_read() -> None:
    """`orion.paper.session` may hold a `MarketThesis` and may not look inside one.

    THIS IS THE TEST THE MODULE DOCSTRING NAMES, AND IT IS STRUCTURAL. A
    specialist reading must not be one edit away from influencing an execution.
    The session is allowed to take a thesis, pass it on and ask whether it is
    `None`; reading a *field* of it is the first step toward branching on one,
    so an attribute access into the value is a failure here, whether it is
    spelled `thesis.technical_bias` or `chain.thesis.technical_bias`.

    Checked against this file's AST rather than by grep, so a rename or a
    reformat cannot quietly retire it, and asserted positively as well: if the
    module stopped carrying a thesis at all the guarantee would be vacuous, so
    the scan also proves the name is really there to be misused.
    """
    import ast
    from pathlib import Path

    from orion.paper import session as session_module

    tree = ast.parse(Path(session_module.__file__ or "").read_text(encoding="utf-8"))
    reads: list[str] = []
    carried = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "thesis":
            carried = True
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id == "thesis":
            reads.append(f"thesis.{node.attr}")
        if isinstance(value, ast.Attribute) and value.attr == "thesis":
            reads.append(f"{ast.unparse(value)}.{node.attr}")

    assert carried, "orion.paper.session no longer carries a thesis; this guarantee is vacuous"
    assert not reads, f"orion.paper.session reads into the thesis it carries: {reads}"


# ------------------------------------------------- the protocol is satisfied


class TestTheJournalProtocol:
    def test_the_concrete_journal_satisfies_the_narrow_protocol(self, tmp_path) -> None:
        """`orion.paper` may not import `orion.memory` -- `orion.memory` already
        depends on `orion.paper`, so the reverse would be a cycle. `JournalWriter`
        is that dependency inverted, and this is the test that stops the
        restatement from drifting away from the real interface."""
        engine = create_memory_engine(str(tmp_path / "j.db"))
        try:
            journal = SqliteDecisionJournal(engine)
            assert isinstance(journal, JournalWriter)
        finally:
            engine.dispose()

    def test_the_journal_abc_satisfies_the_narrow_protocol(self) -> None:
        """Checked against the ABC as well as the implementation, so a change
        to `DecisionJournal`'s signatures is caught even if no concrete class
        happens to be constructed here."""
        assert issubclass(DecisionJournal, JournalWriter)

    def test_the_protocol_declares_writes_and_only_writes(self) -> None:
        """Phase 2b declared two methods, because a fill and a closed position
        were all that phase produced. 2c journals the chain that authorised the
        fill as well, so the restatement is now `DecisionJournal`'s full *write*
        surface — and still not one read, update or delete. A forward cycle
        appends what it did; reading history back is `orion.reflection`'s job,
        and there is no method here through which this module could do it."""
        declared = {name for name in vars(JournalWriter) if not name.startswith("_")}
        assert declared == {
            "record_evidence",
            "record_reasoning",
            "record_decision",
            "record_verdict",
            # Mission 8 v2.2: the seventh record. Advisory evidence this module
            # carries to the journal and never reads.
            "record_thesis",
            "record_fill",
            "record_closed_position",
        }
        assert not any(
            name.startswith(("get_", "list_", "update_", "delete_")) for name in declared
        )


# ------------------------------------------------------ nothing is invented


class TestNothingIsFabricated:
    def test_every_fill_value_comes_from_the_execution_engine(self) -> None:
        calls: list[str] = []
        state = fresh_account()
        store = RecordingStore(state, calls=calls)
        journal = RecordingJournal(calls=calls)

        evidence = make_evidence()
        reasoning = make_reasoning(evidence)
        decision = make_decision(evidence, reasoning)
        verdict = make_verdict(decision, evidence, state)

        result = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=verdict,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=RISK_FRACTION,
            now=NOW,
        )

        assert result.fill is not None
        fill = result.fill
        # The fill price is the engine's own spread/slippage adjustment of the
        # evidence price, not the evidence price itself and not a round number.
        expected = evidence.last_price * (1 + (POLICY.spread_bps + POLICY.slippage_bps) / 10_000.0)
        assert fill.fill_price == pytest.approx(expected)
        assert fill.fee == pytest.approx(
            fill.fill_price * fill.quantity * POLICY.fee_bps / 10_000.0
        )
        assert fill.risk_verdict_fingerprint == risk_verdict_fingerprint(verdict)
        assert fill.symbol == SYMBOL

    def test_the_quantity_is_the_shared_sizing_function_not_a_guess(self) -> None:
        from orion.risk.portfolio import compute_position_size

        state = fresh_account()
        store = InMemoryPortfolioStore()
        store.initialize(state)
        journal = RecordingJournal(calls=[])

        evidence = make_evidence()
        reasoning = make_reasoning(evidence)
        decision = make_decision(evidence, reasoning)
        verdict = make_verdict(decision, evidence, state)
        marked = mark_to_market(state, now=NOW, last_price=evidence.last_price)

        result = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=verdict,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=RISK_FRACTION,
            now=NOW,
        )

        assert decision.reasoning_ref.stop_loss is not None
        expected = compute_position_size(
            marked.equity,
            evidence.last_price,
            decision.reasoning_ref.stop_loss,
            risk_per_trade_fraction=RISK_FRACTION,
        )
        assert result.fill is not None
        assert result.fill.quantity == expected

    def test_no_cycle_invents_a_closed_position(self) -> None:
        """A cycle that opened a position produces a fill and no close."""
        store = InMemoryPortfolioStore()
        store.initialize(fresh_account())
        journal = RecordingJournal(calls=[])

        result = cycle(store=store, journal=journal)

        assert result.outcome is CycleOutcome.EXECUTED
        assert result.closed_position is None
        assert journal.closed == []


# --------------------------------------------------------- no venue, no loop


class TestThereIsNoVenueAndNoLoop:
    def test_the_session_module_imports_no_network_or_broker_module(self) -> None:
        import ast
        from pathlib import Path

        import orion.paper.session as session_module

        source = Path(session_module.__file__ or "")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        for forbidden in ("httpx", "requests", "socket", "urllib", "ccxt", "sqlalchemy"):
            assert forbidden not in imported

    def test_the_session_does_not_import_the_orchestrator_or_memory(self) -> None:
        """Both walls, asserted from this side too. `orion.orchestrator` is
        forbidden by the engine-package rule; `orion.memory` would be a cycle."""
        import ast
        from pathlib import Path

        import orion.paper.session as session_module

        source = Path(session_module.__file__ or "")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        assert not any(m.startswith("orion.orchestrator") for m in modules)
        assert not any(m.startswith("orion.memory") for m in modules)

    def test_the_session_contains_no_sleep_retry_or_thread(self) -> None:
        from pathlib import Path

        import orion.paper.session as session_module

        code = Path(session_module.__file__ or "").read_text(encoding="utf-8")
        for forbidden in ("time.sleep", "threading", "asyncio", "schedule", "while True"):
            assert forbidden not in code


# ------------------------------------------------- the sole production writer


class TestTheSessionIsTheOnlyProductionWriter:
    def test_nothing_else_in_src_calls_record_fill_or_record_closed_position(self) -> None:
        """Mission 5's Finding B named the long-term mitigation for `Fill`
        provenance: one production caller, always passing `execute()`'s own
        return value. This is that guarantee, committed.

        `orion/memory` is excluded because it *defines* the methods; a call
        anywhere else in `src/` would be a second production writer, which is
        exactly what Finding B says must not exist.
        """
        import ast
        from pathlib import Path

        source = Path("src/orion").resolve()
        callers: set[str] = set()
        for path in source.rglob("*.py"):
            relative = path.relative_to(source).as_posix()
            if relative.startswith("memory/"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"record_fill", "record_closed_position"}
                ):
                    callers.add(relative)

        assert callers == {"paper/session.py"}

    def test_the_session_passes_the_engines_own_objects_through_untouched(self) -> None:
        """The other half of Finding B: the journalled `Fill` must be the one
        `execute()` returned, not one rebuilt from its parts."""
        store = InMemoryPortfolioStore()
        store.initialize(fresh_account())
        journal = RecordingJournal(calls=[])

        result = cycle(store=store, journal=journal)

        assert result.fill is not None
        assert journal.fills == [result.fill]
        # Identity, not just equality: the very object, never a reconstruction.
        assert journal.fills[0] is result.fill
