"""End-to-end replay/optimistic-concurrency protection: `orion.paper.
execution.execute` combined with `orion.paper.store.InMemoryPortfolioStore`.

This is the regression suite for the Mission 4 H-NEW1/H-ABA1 fixes:
`execute()` alone cannot know whether the `PortfolioState` it was given is
still current (it is a pure function with no store dependency, by design),
so replay protection lives at the store boundary instead
(`PortfolioStore.save`'s monotonic-revision compare-and-swap).
`execute()` computing a redundant result from a stale snapshot is harmless
on its own — nothing is persisted by calling it — the guarantee this suite
proves is that the store never ends up reflecting more than one fill for
one approved verdict, regardless of how many times a caller (mis)calls
`execute()` with a stale snapshot, and regardless of whether the store's
content ever coincidentally repeats (the ABA case).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.store import InMemoryPortfolioStore, PortfolioStoreConcurrencyError
from tests.paper.conftest import BASE_TIME, build_approved_trade, build_evidence

ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)


def test_legitimate_sequential_execution_succeeds() -> None:
    trade = build_approved_trade(cash=100_000.0)
    store = InMemoryPortfolioStore()
    record = store.initialize(trade.portfolio)

    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        record.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    saved = store.save(result.portfolio, expected_revision=record.revision)

    assert store.load().state == result.portfolio
    assert store.load().state.open_position is not None
    assert saved.revision == record.revision + 1


def test_retry_with_stale_snapshot_fails_closed_without_mutating_account_state() -> None:
    # The exact scenario requirement 8 (of the H-NEW1 fix) asks for: a
    # caller receives an approved trade, executes it (successfully, and
    # the fill is persisted), then retries -- reusing the ORIGINAL,
    # now-stale PortfolioRecord (a crash-resume, a timeout-triggered
    # retry, a race). The retry's execute() call succeeds on its own
    # (pure, harmless), but its save() attempt must be rejected, and the
    # account must end up reflecting exactly one fill, never two.
    trade = build_approved_trade(cash=100_000.0)
    store = InMemoryPortfolioStore()
    record = store.initialize(trade.portfolio)

    stale_record = store.load()  # the record the caller will (wrongly) reuse

    # First (legitimate) attempt.
    result_1 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        stale_record.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    store.save(result_1.portfolio, expected_revision=stale_record.revision)
    cash_after_first_save = store.load().state.cash

    # Retry: execute() itself is still callable (it is pure and has no way
    # to know the store has moved on) -- this must not raise on its own.
    result_2 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        stale_record.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    # But persisting it must fail closed: `stale_record`'s revision no
    # longer matches the store's actual current revision.
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(result_2.portfolio, expected_revision=stale_record.revision)

    # Account state is untouched by the rejected retry: still exactly the
    # first execution's result, one position, cash debited exactly once.
    final = store.load()
    assert final.state.cash == cash_after_first_save
    assert final.state == result_1.portfolio
    assert final.state.open_position is not None
    assert record.revision == stale_record.revision  # sanity: same starting point


def test_replay_never_produces_a_second_position_in_the_store() -> None:
    trade = build_approved_trade(cash=100_000.0)
    store = InMemoryPortfolioStore()
    stale_record = store.initialize(trade.portfolio)

    result_1 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        stale_record.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    store.save(result_1.portfolio, expected_revision=stale_record.revision)

    # Attempt the replay several times in a row -- every single one must be
    # rejected; none may ever land, and the revision the store reports
    # keeps moving further out of reach of the stale caller.
    for _ in range(3):
        replay_result = execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            stale_record.state,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )
        with pytest.raises(PortfolioStoreConcurrencyError):
            store.save(replay_result.portfolio, expected_revision=stale_record.revision)

    assert store.load().state == result_1.portfolio
    assert store.load().state.open_position is not None


def test_a_correctly_reloaded_snapshot_after_a_legitimate_execution_can_still_act() -> None:
    # Confirms the fix does not block legitimate forward progress: closing
    # the position (via a second, correctly-sequenced state transition)
    # still works once the caller reloads the current record rather than
    # reusing a stale one.
    trade = build_approved_trade(
        cash=100_000.0, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    store = InMemoryPortfolioStore()
    record_0 = store.initialize(trade.portfolio)

    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        record_0.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    store.save(opened.portfolio, expected_revision=record_0.revision)

    record_1 = store.load()  # correctly reloaded -- not stale
    exit_evidence = build_evidence(last_price=2500.0)
    closed_portfolio, closed = resolve_stop_take_profit(
        record_1.state, exit_evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    store.save(closed_portfolio, expected_revision=record_1.revision)

    assert closed is not None
    assert store.load().state.open_position is None
    assert store.load().state == closed_portfolio
    assert store.load().revision == record_1.revision + 1


def test_replay_after_open_close_reopen_is_still_rejected() -> None:
    # A stale snapshot from *before the position was ever opened*, replayed
    # after a full open -> close -> reopen cycle, must still be rejected --
    # even though a pure content-fingerprint CAS could theoretically be
    # fooled if the account's content happened to coincide at some point in
    # that cycle. The monotonic revision cannot be fooled this way.
    trade = build_approved_trade(
        cash=100_000.0, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    store = InMemoryPortfolioStore()
    stale_record = store.initialize(trade.portfolio)  # pre-open, revision 0

    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        stale_record.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    r1 = store.save(opened.portfolio, expected_revision=stale_record.revision)

    exit_evidence = build_evidence(last_price=2500.0)
    closed_portfolio, _ = resolve_stop_take_profit(
        store.load().state,
        exit_evidence,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME + timedelta(hours=1),
    )
    r2 = store.save(closed_portfolio, expected_revision=r1.revision)
    assert store.load().state.open_position is None

    # Reopen a second position using the (now-flat) current record.
    trade_2 = build_approved_trade(
        cash=store.load().state.cash, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    reopened = execute(
        trade_2.proposal,
        trade_2.verdict,
        trade_2.evidence,
        store.load().state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME + timedelta(hours=2),
    )
    store.save(reopened.portfolio, expected_revision=r2.revision)
    assert store.load().state.open_position is not None

    # The ORIGINAL pre-open stale_record (revision 0) replays now.
    replay = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        stale_record.state,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(replay.portfolio, expected_revision=stale_record.revision)


def test_replay_protection_is_deterministic() -> None:
    # Two independent (store, stale-record) setups built from identical
    # inputs must behave identically -- same rejection, same final state.
    def run() -> tuple[object, bool]:
        trade = build_approved_trade(cash=100_000.0)
        store = InMemoryPortfolioStore()
        stale_record = store.initialize(trade.portfolio)
        result_1 = execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            stale_record.state,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )
        store.save(result_1.portfolio, expected_revision=stale_record.revision)
        result_2 = execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            stale_record.state,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )
        rejected = False
        try:
            store.save(result_2.portfolio, expected_revision=stale_record.revision)
        except PortfolioStoreConcurrencyError:
            rejected = True
        return store.load().state, rejected

    final_1, rejected_1 = run()
    final_2, rejected_2 = run()
    assert rejected_1 is rejected_2 is True
    assert final_1 == final_2
