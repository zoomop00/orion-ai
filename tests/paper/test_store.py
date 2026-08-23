from __future__ import annotations

import pytest

from orion.paper.store import (
    InMemoryPortfolioStore,
    PortfolioRecord,
    PortfolioStoreConcurrencyError,
    PortfolioStoreError,
)
from orion.risk.portfolio import portfolio_state_fingerprint
from tests.paper.conftest import build_portfolio


def test_load_before_initialize_raises() -> None:
    store = InMemoryPortfolioStore()
    with pytest.raises(PortfolioStoreError):
        store.load()


def test_initialize_returns_a_record_at_revision_zero() -> None:
    store = InMemoryPortfolioStore()
    portfolio = build_portfolio()
    record = store.initialize(portfolio)
    assert isinstance(record, PortfolioRecord)
    assert record.state == portfolio
    assert record.revision == 0


def test_initialize_then_load_returns_the_same_state_and_revision() -> None:
    store = InMemoryPortfolioStore()
    portfolio = build_portfolio()
    initialized = store.initialize(portfolio)
    loaded = store.load()
    assert loaded.state == portfolio
    assert loaded.revision == initialized.revision == 0


def test_double_initialize_raises() -> None:
    store = InMemoryPortfolioStore()
    store.initialize(build_portfolio())
    with pytest.raises(PortfolioStoreError):
        store.initialize(build_portfolio())


def test_save_with_the_correct_expected_revision_updates_the_stored_state() -> None:
    store = InMemoryPortfolioStore()
    initial = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    updated_state = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    updated = store.save(updated_state, expected_revision=initial.revision)
    assert store.load().state == updated_state
    assert updated.state == updated_state
    assert updated.revision == initial.revision + 1


def test_save_before_initialize_raises_a_concurrency_error() -> None:
    # save() always requires an existing state to build on -- there is no
    # meaningful revision to match for a store that was never initialized.
    # initialize() is the only way to establish the first state; this is a
    # deliberate design choice, not an oversight.
    store = InMemoryPortfolioStore()
    portfolio = build_portfolio()
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(portfolio, expected_revision=0)


def test_save_rejects_a_non_portfolio_state() -> None:
    store = InMemoryPortfolioStore()
    store.initialize(build_portfolio())
    with pytest.raises(PortfolioStoreError):
        store.save("not-a-portfolio", expected_revision=0)  # type: ignore[arg-type]


def test_two_stores_are_independent() -> None:
    store_a = InMemoryPortfolioStore()
    store_b = InMemoryPortfolioStore()
    store_a.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    with pytest.raises(PortfolioStoreError):
        store_b.load()


# --- optimistic concurrency: monotonic revision (H-ABA1 fix) ---


def test_stale_write_is_rejected_and_does_not_change_the_store() -> None:
    store = InMemoryPortfolioStore()
    v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))

    v1_state = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    v1 = store.save(v1_state, expected_revision=v0.revision)
    assert store.load().state == v1_state

    # A second write still claiming v0's revision -- stale, since the store
    # has already moved on to v1.
    v2_stale_state = build_portfolio(cash=90_000.0, starting_balance=100_000.0)
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(v2_stale_state, expected_revision=v0.revision)
    # Rejected write must not have changed state or revision.
    assert store.load().state == v1_state
    assert store.load().revision == v1.revision


def test_newer_state_cannot_be_silently_reverted_by_an_older_one() -> None:
    store = InMemoryPortfolioStore()
    older = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    newer_state = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    newer = store.save(newer_state, expected_revision=older.revision)
    assert store.load().state == newer_state

    # Attempting to write the stale `older` snapshot back, claiming
    # `older`'s own revision -- must be rejected; the store must still
    # hold `newer` at `newer`'s revision.
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(older.state, expected_revision=older.revision)
    assert store.load().state == newer_state
    assert store.load().revision == newer.revision


def test_classic_aba_attack_is_rejected() -> None:
    # The exact vulnerability found in the prior (content-fingerprint-only)
    # CAS design: store content goes A -> B -> C, where C is byte-identical
    # to A. A stale caller holding A's *content* would have their write
    # wrongly accepted against C under a pure fingerprint check, because
    # C's fingerprint equals A's. The revision must not be fooled by this:
    # C's revision is strictly greater than A's revision, even though their
    # content is identical.
    store = InMemoryPortfolioStore()
    state_a = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    record_a = store.initialize(state_a)  # revision 0

    state_b = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    record_b = store.save(state_b, expected_revision=record_a.revision)  # revision 1

    state_c = build_portfolio(cash=100_000.0, starting_balance=100_000.0)  # content == state_a
    assert state_c == state_a
    record_c = store.save(state_c, expected_revision=record_b.revision)  # revision 2
    assert record_c.state == state_a  # content genuinely round-tripped
    assert record_c.revision == 2  # but the revision kept advancing

    # The stale caller from the very beginning, still holding record_a
    # (revision 0), retries -- this must be rejected even though the
    # store's *content* is currently identical to what they last saw.
    attacker_payload = build_portfolio(cash=1.0, starting_balance=100_000.0)
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(attacker_payload, expected_revision=record_a.revision)
    # Store must still be exactly at C / revision 2 -- untouched.
    final = store.load()
    assert final.state == state_c
    assert final.revision == 2


def test_sequential_saves_with_correct_revisions_chain_correctly() -> None:
    store = InMemoryPortfolioStore()
    v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    v1 = store.save(
        build_portfolio(cash=99_000.0, starting_balance=100_000.0), expected_revision=v0.revision
    )
    v2 = store.save(
        build_portfolio(cash=98_000.0, starting_balance=100_000.0), expected_revision=v1.revision
    )
    v3 = store.save(
        build_portfolio(cash=97_000.0, starting_balance=100_000.0), expected_revision=v2.revision
    )
    assert [v0.revision, v1.revision, v2.revision, v3.revision] == [0, 1, 2, 3]
    assert store.load().state.cash == 97_000.0
    assert store.load().revision == 3


def test_revision_does_not_change_on_a_rejected_write() -> None:
    store = InMemoryPortfolioStore()
    v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(build_portfolio(cash=1.0, starting_balance=100_000.0), expected_revision=999)
    assert store.load().revision == v0.revision == 0


def test_repeated_stale_retries_are_all_rejected() -> None:
    store = InMemoryPortfolioStore()
    v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    store.save(
        build_portfolio(cash=95_000.0, starting_balance=100_000.0), expected_revision=v0.revision
    )
    for _ in range(5):
        with pytest.raises(PortfolioStoreConcurrencyError):
            store.save(
                build_portfolio(cash=1.0, starting_balance=100_000.0), expected_revision=v0.revision
            )
    assert store.load().state.cash == 95_000.0


def test_concurrency_error_message_reports_the_revision_mismatch() -> None:
    store = InMemoryPortfolioStore()
    store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    with pytest.raises(PortfolioStoreConcurrencyError, match="stale snapshot"):
        store.save(build_portfolio(cash=1.0, starting_balance=100_000.0), expected_revision=999)


def test_concurrency_errors_are_deterministic() -> None:
    def attempt() -> str:
        store = InMemoryPortfolioStore()
        v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
        store.save(
            build_portfolio(cash=95_000.0, starting_balance=100_000.0),
            expected_revision=v0.revision,
        )
        try:
            store.save(
                build_portfolio(cash=1.0, starting_balance=100_000.0), expected_revision=v0.revision
            )
            return "accepted"
        except PortfolioStoreConcurrencyError as e:
            return str(e)

    assert attempt() == attempt()


# --- content fingerprint remains a separate, unchanged mechanism ---


def test_content_fingerprint_is_unaffected_by_the_revision_concurrency_fix() -> None:
    # portfolio_state_fingerprint continues to answer "what does this state
    # contain", entirely independent of the store's revision bookkeeping --
    # confirms the two mechanisms were not merged or conflated.
    store = InMemoryPortfolioStore()
    state_a = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    record_a = store.initialize(state_a)
    state_c = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    assert portfolio_state_fingerprint(record_a.state) == portfolio_state_fingerprint(state_c)
