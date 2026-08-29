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

from orion.analysis.evidence import Evidence, build_evidence
from orion.decision.engine import decide
from orion.decision.schemas import ProposedDecision
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
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.guardian import review
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import PortfolioState, initial_portfolio_state, mark_to_market
from orion.risk.schemas import RiskOutcome, RiskVerdict, risk_verdict_fingerprint

from ..orchestrator.conftest import (
    LAST_BAR_TIME,
    NOW,
    FakeReasoningProvider,
    make_bars,
    make_snapshot,
)

SYMBOL = "XAUUSD"
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
    """A `JournalWriter` that records the order of what it was asked to do."""

    def __init__(self, *, calls: list[str], fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail
        self.fills: list[Fill] = []
        self.closed: list[ClosedPosition] = []

    def record_fill(self, fill: Fill, *, recorded_at: datetime) -> str:
        self.calls.append("record_fill")
        if self.fail:
            raise RuntimeError("journal is down")
        self.fills.append(fill)
        return "fill-fingerprint"

    def record_closed_position(self, closed: ClosedPosition, *, recorded_at: datetime) -> str:
        self.calls.append("record_closed_position")
        if self.fail:
            raise RuntimeError("journal is down")
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
):
    """Run one cycle against real engines, with only the policy varied."""
    evidence = evidence or make_evidence()
    reasoning = make_reasoning(evidence)
    decision = make_decision(evidence, reasoning)
    if use_verdict and verdict is None:
        verdict = make_verdict(decision, evidence, store.load().state, policy=policy)
    return run_cycle(
        decision=decision,
        evidence=evidence,
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
        assert calls == ["load", "load", "save", "record_fill"]
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
        assert calls == ["load", "load", "save", "record_fill"]

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

    def test_the_protocol_declares_only_what_the_session_needs(self) -> None:
        """Narrower than `DecisionJournal` on purpose: this module writes fills
        and closed positions, so it must not be able to write anything else."""
        declared = {name for name in vars(JournalWriter) if not name.startswith("_")}
        assert declared == {"record_fill", "record_closed_position"}


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
