"""Mission 9: resolving an open position, with no analysis anywhere in the call.

`resolve_position` was the first branch of `run_cycle` and is now callable on
its own. Every test here reaches it with a `PriceObservation` and nothing else
-- no reasoning provider, no proposal, no verdict, no `Evidence` -- because that
is exactly what a caller holds when all it did was fetch a price.

The fixtures are `test_session`'s own, imported rather than rebuilt: the
accounts these tests resolve were opened by the real forward cycle against the
real Risk Guardian, so what is being closed here is a position the Guardian
actually approved rather than a hand-assembled one.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import orion.paper.session as session_module
from orion.data.models import (
    DataSourceMeta,
    MarketSnapshot,
    OHLCVBar,
    PriceObservation,
    Timeframe,
)
from orion.paper.execution import ObservedPrice
from orion.paper.schemas import CloseReason
from orion.paper.session import CycleOutcome, ResolutionCoverage, resolve_position
from orion.risk.portfolio import Position

from ..orchestrator.conftest import NOW
from .test_session import (
    POLICY,
    SYMBOL,
    RecordingJournal,
    RecordingStore,
    cycle,
    fresh_account,
    make_evidence,
)


def open_one(calls: list[str]) -> RecordingStore:
    """An account with one real, Guardian-approved position already open."""
    store = RecordingStore(fresh_account(), calls=calls)
    journal = RecordingJournal(calls=calls)
    opened = cycle(store=store, journal=journal)
    assert opened.outcome is CycleOutcome.EXECUTED
    return store


def price_at(last_price: float, *, symbol: str = SYMBOL) -> PriceObservation:
    return PriceObservation(symbol=symbol, last_price=last_price, as_of=NOW)


# --------------------------------------------------- resolution needs nothing


class TestResolutionNeedsNoReasoningProvider:
    def test_a_take_profit_resolves_with_no_model_proposal_or_verdict(self) -> None:
        """The whole mission in one test: a real close, produced without a
        reasoning provider, a `ProposedDecision` or a `RiskVerdict` existing
        anywhere in this call."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        calls.clear()

        # Loaded exactly once, by this caller, so the recorded call sequence is
        # the one `resolve_position` itself produced.
        record = store.load()
        held = record.state.open_position
        assert held is not None

        result = resolve_position(
            record=record,
            price=price_at(held.take_profit + 25.0),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is not None
        assert result.outcome is CycleOutcome.CLOSED
        assert result.closed_position is not None
        assert result.closed_position.close_reason is CloseReason.TAKE_PROFIT_HIT
        # The account first, the journal second -- the existing ordering, kept.
        assert calls == ["load", "save", "record_closed_position"]

    def test_a_stop_loss_resolves_the_same_way(self) -> None:
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)

        held = store.load().state.open_position
        assert held is not None

        result = resolve_position(
            record=store.load(),
            price=price_at(held.stop_loss - 25.0),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is not None
        assert result.outcome is CycleOutcome.CLOSED
        assert result.closed_position is not None
        assert result.closed_position.close_reason is CloseReason.STOP_LOSS_HIT

    def test_the_closed_position_still_names_the_verdict_that_opened_it(self) -> None:
        """Resolution needs no authority of its own precisely because the
        position carries the one that authorised it. The audit link survives a
        close performed with no verdict in sight."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)

        held = store.load().state.open_position
        assert held is not None

        result = resolve_position(
            record=store.load(),
            price=price_at(held.take_profit + 25.0),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is not None
        assert result.closed_position is not None
        assert (
            result.closed_position.opened_by_verdict_fingerprint
            == held.opened_by_verdict_fingerprint
        )

    def test_a_flat_account_returns_none_rather_than_an_outcome(self) -> None:
        """`None` is "nothing to resolve", which is not any `CycleOutcome`.
        Reporting HOLDING would make that outcome ambiguous between "a position
        is open and waiting" and "there is no position at all"."""
        store = RecordingStore(fresh_account(), calls=[])
        journal = RecordingJournal(calls=[])

        result = resolve_position(
            record=store.load(),
            price=price_at(2500.0),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is None
        assert journal.closed == []
        assert journal.fills == []

    def test_a_symbol_mismatch_fails_closed_and_writes_nothing(self) -> None:
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        calls.clear()

        result = resolve_position(
            record=store.load(),
            price=price_at(2500.0, symbol="EURUSD"),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is not None
        assert result.outcome is CycleOutcome.EXECUTION_REFUSED
        assert "save" not in calls
        assert journal.closed == []


# ------------------------------------------------- holding writes nothing


class TestHoldingCreatesNoRevision:
    def test_repeated_holding_leaves_the_revision_untouched(self) -> None:
        """A monitoring caller may run at any frequency. If holding wrote a
        revision, watching an account would grow it without recording a single
        decision -- so the revision must be *unchanged*, not merely unsaved."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        before = store.load().revision
        calls.clear()

        held = store.load().state.open_position
        assert held is not None
        # Strictly between the two levels: neither is reached.
        midpoint = (held.stop_loss + held.take_profit) / 2.0

        for _ in range(5):
            result = resolve_position(
                record=store.load(),
                price=price_at(midpoint),
                store=store,
                journal=journal,
                policy=POLICY,
                now=NOW,
            )
            assert result is not None
            assert result.outcome is CycleOutcome.HOLDING

        assert store.load().revision == before
        assert "save" not in calls
        assert journal.closed == []

    def test_holding_still_reports_a_marked_to_market_projection(self) -> None:
        """Not saving it is not the same as not computing it: the caller is
        told what the account is worth at this price, and the store is simply
        never asked to remember."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)

        held = store.load().state.open_position
        assert held is not None
        midpoint = (held.stop_loss + held.take_profit) / 2.0

        result = resolve_position(
            record=store.load(),
            price=price_at(midpoint),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is not None
        assert result.portfolio is not None
        assert result.portfolio.open_position == held
        assert result.account_was_written is False


# --------------------------------------------- it can never open a position


class TestResolutionCanNeverOpenAPosition:
    def test_no_price_at_any_level_produces_a_fill(self) -> None:
        """Swept across the whole geometry: below the stop, at it, between, at
        the target, and beyond it. None may produce a `Fill`."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)

        held = store.load().state.open_position
        assert held is not None
        prices = [
            held.stop_loss - 50.0,
            held.stop_loss,
            (held.stop_loss + held.take_profit) / 2.0,
            held.take_profit,
            held.take_profit + 50.0,
        ]

        for last_price in prices:
            record = store.load()
            if record.state.open_position is None:
                # Already closed by an earlier price in the sweep; a flat
                # account is exactly the case that must not reopen.
                assert (
                    resolve_position(
                        record=record,
                        price=price_at(last_price),
                        store=store,
                        journal=journal,
                        policy=POLICY,
                        now=NOW,
                    )
                    is None
                )
                continue
            result = resolve_position(
                record=record,
                price=price_at(last_price),
                store=store,
                journal=journal,
                policy=POLICY,
                now=NOW,
            )
            assert result is not None
            assert result.fill is None

        assert journal.fills == []

    def test_the_function_body_calls_nothing_that_could_open_one(self) -> None:
        """Structural, not behavioural. `resolve_position`'s own AST is walked
        and must contain no call capable of opening a position or sizing one. A
        future edit that added one fails here rather than in whichever scenario
        happened to exercise it -- the same idiom
        `test_the_thesis_is_carried_and_never_read` already uses."""
        source = Path(session_module.__file__ or "").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_position"
        )

        called: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)

        for forbidden in (
            "execute",
            "record_fill",
            "compute_position_size",
            "review",
            "decide",
        ):
            assert forbidden not in called


# ------------------------------------ the extraction is one function, not two


class TestTheExtractionIsWhatRunCycleRuns:
    def test_resolving_directly_journals_identically_to_run_cycle(self) -> None:
        """Two accounts opened identically, closed at the same price by the two
        paths. The `ClosedPosition`, the journal writes and the resulting
        account must be indistinguishable -- there is one resolver, not two."""
        cycle_calls: list[str] = []
        direct_calls: list[str] = []
        via_cycle_store = open_one(cycle_calls)
        direct_store = open_one(direct_calls)
        via_cycle_journal = RecordingJournal(calls=cycle_calls)
        direct_journal = RecordingJournal(calls=direct_calls)

        held = via_cycle_store.load().state.open_position
        assert held is not None
        assert direct_store.load().state == via_cycle_store.load().state
        exit_price = held.take_profit + 25.0

        cycle_calls.clear()
        direct_calls.clear()

        through_cycle = cycle(
            store=via_cycle_store,
            journal=via_cycle_journal,
            evidence=make_evidence(last_close=exit_price),
        )
        directly = resolve_position(
            record=direct_store.load(),
            price=price_at(exit_price),
            store=direct_store,
            journal=direct_journal,
            policy=POLICY,
            now=NOW,
        )

        assert directly is not None
        assert through_cycle.outcome is CycleOutcome.CLOSED
        assert directly.outcome is CycleOutcome.CLOSED
        assert through_cycle.closed_position == directly.closed_position
        assert through_cycle.journal_fingerprint == directly.journal_fingerprint
        assert via_cycle_journal.closed == direct_journal.closed
        assert via_cycle_store.load().state == direct_store.load().state
        # `load` differs by design: the direct caller reads the record itself
        # rather than having `run_cycle` read it. Everything either path asked
        # the account or the journal to *write* is identical.
        assert [c for c in cycle_calls if c != "load"] == [c for c in direct_calls if c != "load"]

    def test_run_cycle_still_resolves_before_it_considers_a_new_position(self) -> None:
        """The delegation must not have reordered the two branches: a cycle
        that closed a position does not also open one."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)

        held = store.load().state.open_position
        assert held is not None
        calls.clear()

        result = cycle(
            store=store,
            journal=journal,
            evidence=make_evidence(last_close=held.take_profit + 25.0),
        )

        assert result.outcome is CycleOutcome.CLOSED
        assert result.fill is None
        assert "record_fill" not in calls


def test_evidence_still_satisfies_the_narrowed_price_contract() -> None:
    """Why no pre-Mission-9 caller had to change. `Evidence` carries a symbol
    and a last price, so it satisfies `ObservedPrice` structurally -- the
    resolver now asks for *less* than it used to, never for something else."""
    assert isinstance(make_evidence(), ObservedPrice)
    assert isinstance(price_at(2500.0), ObservedPrice)


# ------------------------------- Mission 9 Phase 2: the bar-aware path


def window_around(held: Position, *bars: OHLCVBar) -> MarketSnapshot:
    """A snapshot that reaches back past the entry, then holds `bars`.

    The leading bar sits at `opened_at` so `window_coverage` can *prove* the
    window reached back; without it every result would be `TRUNCATED` and the
    coverage assertions would all pass for the wrong reason. It sits exactly
    there, not an hour earlier, so the step from it to the first examined bar is
    one interval -- an earlier lead with no bar covering the entry is itself a
    hole across the boundary, which coverage now correctly reports as `GAPPED`.
    """
    lead = OHLCVBar(
        timestamp=held.opened_at,
        open=held.entry_price,
        high=held.entry_price,
        low=held.entry_price,
        close=held.entry_price,
        volume=1_000.0,
    )
    return MarketSnapshot(
        source=DataSourceMeta(
            provider="fake", symbol=SYMBOL, timeframe=Timeframe.H1, retrieved_at=NOW
        ),
        bars=[lead, *bars],
    )


def wick_bar(held: Position, *, low: float, hours_after: int = 1) -> OHLCVBar:
    """A bar whose LOW reached `low` and whose CLOSE did not move.

    The shape a last price cannot see: price went there and came back.
    """
    return OHLCVBar(
        timestamp=held.opened_at + timedelta(hours=hours_after),
        open=held.entry_price,
        high=held.entry_price,
        low=low,
        close=held.entry_price,
        volume=1_000.0,
    )


def quiet_bar(held: Position, *, hours_after: int = 1) -> OHLCVBar:
    return OHLCVBar(
        timestamp=held.opened_at + timedelta(hours=hours_after),
        open=held.entry_price,
        high=held.entry_price,
        low=held.entry_price,
        close=held.entry_price,
        volume=1_000.0,
    )


class TestTheBarPathThroughResolvePosition:
    def test_a_wick_through_the_stop_closes_where_a_last_price_holds(self) -> None:
        """The two paths, same account, same bar. Phase 1's resolver sees a
        close that never left the range; Phase 2's sees the low that did."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        record = store.load()
        held = record.state.open_position
        assert held is not None
        bars = window_around(held, wick_bar(held, low=held.stop_loss - 5.0))

        by_last_price = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )
        assert by_last_price is not None
        assert by_last_price.outcome is CycleOutcome.HOLDING

        by_bars = resolve_position(
            record=store.load(),
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=bars,
        )
        assert by_bars is not None
        assert by_bars.outcome is CycleOutcome.CLOSED
        assert by_bars.closed_position is not None
        assert by_bars.closed_position.close_reason is CloseReason.STOP_LOSS_HIT

    def test_the_close_is_persisted_account_first_then_journal(self) -> None:
        """The existing ordering, unchanged on the new path."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        record = store.load()
        held = record.state.open_position
        assert held is not None
        calls.clear()

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=window_around(held, wick_bar(held, low=held.stop_loss - 5.0)),
        )

        assert result is not None
        assert result.outcome is CycleOutcome.CLOSED
        assert calls == ["save", "record_closed_position"]
        # A close still writes no chain: the records that authorised this
        # position were journaled by the cycle that opened it.
        assert result.audit_chain_fingerprints == ()
        assert journal.evidence == []
        assert journal.verdicts == []

    def test_no_fill_is_ever_written_on_the_bar_path(self) -> None:
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        record = store.load()
        held = record.state.open_position
        assert held is not None

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=window_around(held, wick_bar(held, low=held.stop_loss - 5.0)),
        )

        assert result is not None
        assert result.fill is None
        assert journal.fills == []

    def test_the_triggering_bar_timestamp_is_reported_but_not_journaled(self) -> None:
        """Provenance without touching `ClosedPosition`: the bar is named on the
        result, and `closed_at` is still the caller-supplied `now`."""
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        record = store.load()
        held = record.state.open_position
        assert held is not None
        trigger = wick_bar(held, low=held.stop_loss - 5.0)

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=window_around(held, trigger),
        )

        assert result is not None
        assert result.triggering_bar_at == trigger.timestamp
        assert result.closed_position is not None
        assert result.closed_position.closed_at == NOW
        # The journaled record carries no bar field at all -- the schema and its
        # fingerprint are untouched by this phase.
        assert not hasattr(result.closed_position, "triggering_bar_at")

    def test_holding_on_the_bar_path_creates_no_revision(self) -> None:
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        record = store.load()
        held = record.state.open_position
        assert held is not None
        before = record.revision
        calls.clear()

        for _ in range(3):
            result = resolve_position(
                record=store.load(),
                price=price_at(held.entry_price),
                store=store,
                journal=journal,
                policy=POLICY,
                now=NOW,
                bars=window_around(held, quiet_bar(held)),
            )
            assert result is not None
            assert result.outcome is CycleOutcome.HOLDING

        assert store.load().revision == before
        assert "save" not in calls
        assert journal.closed == []

    def test_a_symbol_mismatch_on_the_bar_path_fails_closed(self) -> None:
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        record = store.load()
        held = record.state.open_position
        assert held is not None
        calls.clear()
        wrong = MarketSnapshot(
            source=DataSourceMeta(
                provider="fake", symbol="EURUSD", timeframe=Timeframe.H1, retrieved_at=NOW
            ),
            bars=[quiet_bar(held)],
        )

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=wrong,
        )

        assert result is not None
        assert result.outcome is CycleOutcome.EXECUTION_REFUSED
        assert "save" not in calls
        assert journal.closed == []


class TestCoverageRidesOnTheResult:
    def test_a_complete_window_reports_complete(self) -> None:
        store = open_one([])
        journal = RecordingJournal(calls=[])
        record = store.load()
        held = record.state.open_position
        assert held is not None

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=window_around(held, quiet_bar(held)),
        )

        assert result is not None
        assert result.outcome is CycleOutcome.HOLDING
        assert result.coverage is ResolutionCoverage.COMPLETE
        assert result.coverage_is_incomplete is False

    def test_a_holding_over_a_truncated_window_is_marked_incomplete(self) -> None:
        """The guarantee that matters: HOLDING claims neither level was
        reached, and that claim is only sound if everything was examined."""
        store = open_one([])
        journal = RecordingJournal(calls=[])
        record = store.load()
        held = record.state.open_position
        assert held is not None
        # No leading bar: the window starts after the entry.
        short_window = MarketSnapshot(
            source=DataSourceMeta(
                provider="fake", symbol=SYMBOL, timeframe=Timeframe.H1, retrieved_at=NOW
            ),
            bars=[quiet_bar(held, hours_after=3)],
        )

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=short_window,
        )

        assert result is not None
        assert result.outcome is CycleOutcome.HOLDING
        assert result.coverage is ResolutionCoverage.TRUNCATED
        assert result.coverage_is_incomplete is True

    def test_a_holding_over_a_gapped_window_is_marked_incomplete(self) -> None:
        store = open_one([])
        journal = RecordingJournal(calls=[])
        record = store.load()
        held = record.state.open_position
        assert held is not None

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=window_around(
                held, quiet_bar(held, hours_after=1), quiet_bar(held, hours_after=6)
            ),
        )

        assert result is not None
        assert result.outcome is CycleOutcome.HOLDING
        assert result.coverage is ResolutionCoverage.GAPPED
        assert result.coverage_is_incomplete is True

    def test_an_imperfect_window_still_closes_a_position_that_really_hit(self) -> None:
        """Fail-closed on the *claim*, not on the action. Missing bars downgrade
        what the result asserts; they never suppress a real exit."""
        store = open_one([])
        journal = RecordingJournal(calls=[])
        record = store.load()
        held = record.state.open_position
        assert held is not None
        gapped = MarketSnapshot(
            source=DataSourceMeta(
                provider="fake", symbol=SYMBOL, timeframe=Timeframe.H1, retrieved_at=NOW
            ),
            bars=[wick_bar(held, low=held.stop_loss - 5.0, hours_after=4)],
        )

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
            bars=gapped,
        )

        assert result is not None
        assert result.outcome is CycleOutcome.CLOSED
        assert result.coverage is ResolutionCoverage.TRUNCATED

    def test_the_last_price_path_reports_no_coverage_at_all(self) -> None:
        """Coverage is a statement about a window of bars. A last-price
        resolution examined no window, so it claims nothing rather than
        claiming completeness."""
        store = open_one([])
        journal = RecordingJournal(calls=[])
        record = store.load()
        held = record.state.open_position
        assert held is not None

        result = resolve_position(
            record=record,
            price=price_at(held.entry_price),
            store=store,
            journal=journal,
            policy=POLICY,
            now=NOW,
        )

        assert result is not None
        assert result.coverage is None
        assert result.coverage_is_incomplete is False


class TestRunCycleIsUntouchedByPhase2:
    def test_a_run_cycle_result_carries_no_coverage_or_trigger(self) -> None:
        """`orion paper` is the compatibility path: it still resolves on a last
        price, and its results make no claim about a bar window."""
        store = RecordingStore(fresh_account(), calls=[])
        journal = RecordingJournal(calls=[])

        opened = cycle(store=store, journal=journal)

        assert opened.outcome is CycleOutcome.EXECUTED
        assert opened.coverage is None
        assert opened.triggering_bar_at is None

    def test_run_cycle_still_closes_on_a_last_price(self) -> None:
        calls: list[str] = []
        store = open_one(calls)
        journal = RecordingJournal(calls=calls)
        held = store.load().state.open_position
        assert held is not None

        result = cycle(
            store=store,
            journal=journal,
            evidence=make_evidence(last_close=held.take_profit + 25.0),
        )

        assert result.outcome is CycleOutcome.CLOSED
        assert result.coverage is None
        assert result.triggering_bar_at is None
