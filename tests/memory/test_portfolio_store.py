"""Tests for `SqlitePortfolioStore`: the durable implementation of `orion.
paper.store.PortfolioStore` behind the exact same interface.

Mirrors the scenarios in `tests/paper/test_store.py` (same CAS/ABA
guarantees must hold for the durable implementation, not just the in-memory
one) plus what only a durable store can be asked to prove: restart
persistence across a fresh process/engine, and genuine cross-thread
concurrent writers.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import OperationalError

from orion.memory.engine import create_memory_engine
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.memory.schema import portfolio_state_revision_table
from orion.paper.store import PortfolioStoreConcurrencyError, PortfolioStoreError
from orion.risk.portfolio import portfolio_state_fingerprint
from tests.memory.conftest import build_portfolio

# --- parity with InMemoryPortfolioStore's own CAS/ABA suite ---


def test_load_before_initialize_raises(store: SqlitePortfolioStore) -> None:
    with pytest.raises(PortfolioStoreError):
        store.load()


def test_initialize_returns_a_record_at_revision_zero(store: SqlitePortfolioStore) -> None:
    portfolio = build_portfolio()
    record = store.initialize(portfolio)
    assert record.state == portfolio
    assert record.revision == 0


def test_initialize_then_load_returns_the_same_state_and_revision(
    store: SqlitePortfolioStore,
) -> None:
    portfolio = build_portfolio()
    initialized = store.initialize(portfolio)
    loaded = store.load()
    assert loaded.state == portfolio
    assert loaded.revision == initialized.revision == 0


def test_double_initialize_raises(store: SqlitePortfolioStore) -> None:
    store.initialize(build_portfolio())
    with pytest.raises(PortfolioStoreError):
        store.initialize(build_portfolio())


def test_save_with_the_correct_expected_revision_updates_the_stored_state(
    store: SqlitePortfolioStore,
) -> None:
    initial = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    updated_state = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    updated = store.save(updated_state, expected_revision=initial.revision)
    assert store.load().state == updated_state
    assert updated.revision == initial.revision + 1


def test_save_before_initialize_raises_a_concurrency_error(store: SqlitePortfolioStore) -> None:
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(build_portfolio(), expected_revision=0)


def test_save_rejects_a_non_portfolio_state(store: SqlitePortfolioStore) -> None:
    store.initialize(build_portfolio())
    with pytest.raises(PortfolioStoreError):
        store.save("not-a-portfolio", expected_revision=0)  # type: ignore[arg-type]


def test_stale_write_is_rejected_and_does_not_change_the_store(
    store: SqlitePortfolioStore,
) -> None:
    v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    v1_state = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    v1 = store.save(v1_state, expected_revision=v0.revision)

    v2_stale_state = build_portfolio(cash=90_000.0, starting_balance=100_000.0)
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(v2_stale_state, expected_revision=v0.revision)
    assert store.load().state == v1_state
    assert store.load().revision == v1.revision


def test_classic_aba_attack_is_rejected(store: SqlitePortfolioStore) -> None:
    state_a = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
    record_a = store.initialize(state_a)  # revision 0

    state_b = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    record_b = store.save(state_b, expected_revision=record_a.revision)  # revision 1

    state_c = build_portfolio(cash=100_000.0, starting_balance=100_000.0)  # content == state_a
    assert state_c == state_a
    record_c = store.save(state_c, expected_revision=record_b.revision)  # revision 2
    assert record_c.revision == 2
    assert portfolio_state_fingerprint(record_c.state) == portfolio_state_fingerprint(state_a)

    # A stale caller from the very beginning, still holding record_a
    # (revision 0), retries -- must be rejected even though the store's
    # *content* is currently identical to what they last saw.
    attacker_payload = build_portfolio(cash=1.0, starting_balance=100_000.0)
    with pytest.raises(PortfolioStoreConcurrencyError):
        store.save(attacker_payload, expected_revision=record_a.revision)
    final = store.load()
    assert final.state == state_c
    assert final.revision == 2


def test_sequential_saves_with_correct_revisions_chain_correctly(
    store: SqlitePortfolioStore,
) -> None:
    v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    v1 = store.save(
        build_portfolio(cash=99_000.0, starting_balance=100_000.0), expected_revision=v0.revision
    )
    v2 = store.save(
        build_portfolio(cash=98_000.0, starting_balance=100_000.0), expected_revision=v1.revision
    )
    assert [v0.revision, v1.revision, v2.revision] == [0, 1, 2]
    assert store.load().state.cash == 98_000.0


# --- HIGH fix regression: a corrupted portfolio_state_revision payload
# must never leak a raw pydantic.ValidationError out of load(); it must
# raise PortfolioStoreError, the documented type, and must not mutate the
# stored row or hand back any state at all ---


def test_load_raises_portfolio_store_error_on_invalid_json(
    store: SqlitePortfolioStore, engine: Engine
) -> None:
    """Payload that is not valid JSON at all -- the exact gap a fresh
    adversarial pass found on this money-critical path: a genuinely
    unparseable row previously raised an unwrapped
    pydantic_core.ValidationError instead of PortfolioStoreError."""
    store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))

    with engine.begin() as conn:
        conn.execute(
            portfolio_state_revision_table.update()
            .where(portfolio_state_revision_table.c.revision == 0)
            .values(payload="not even json {{{")
        )

    with pytest.raises(PortfolioStoreError):
        store.load()

    # The failed read must not have mutated the corrupted row.
    with engine.connect() as conn:
        stored = conn.execute(
            select(portfolio_state_revision_table.c.payload).where(
                portfolio_state_revision_table.c.revision == 0
            )
        ).scalar_one()
    assert stored == "not even json {{{"


def test_load_raises_portfolio_store_error_on_schema_invalid_json(
    store: SqlitePortfolioStore, engine: Engine
) -> None:
    """Payload IS valid JSON but fails PortfolioState's own schema (every
    required field missing) -- the schema-validation-failure path, distinct
    from the JSON-parse-failure path above; both must be caught."""
    store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))

    schema_invalid_payload = "{}"
    with engine.begin() as conn:
        conn.execute(
            portfolio_state_revision_table.update()
            .where(portfolio_state_revision_table.c.revision == 0)
            .values(payload=schema_invalid_payload)
        )

    with pytest.raises(PortfolioStoreError):
        store.load()

    with engine.connect() as conn:
        stored = conn.execute(
            select(portfolio_state_revision_table.c.payload).where(
                portfolio_state_revision_table.c.revision == 0
            )
        ).scalar_one()
    assert stored == schema_invalid_payload


def test_load_corruption_error_is_not_a_concurrency_error(
    store: SqlitePortfolioStore, engine: Engine
) -> None:
    """A corrupted payload is a data-integrity problem, not a stale-write
    problem -- must not be misreported as PortfolioStoreConcurrencyError,
    which would wrongly suggest "reload and retry" is a valid recovery."""
    store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    with engine.begin() as conn:
        conn.execute(
            portfolio_state_revision_table.update()
            .where(portfolio_state_revision_table.c.revision == 0)
            .values(payload="not json")
        )

    with pytest.raises(PortfolioStoreError) as exc_info:
        store.load()
    assert not isinstance(exc_info.value, PortfolioStoreConcurrencyError)


# --- fail-closed wrapping of unexpected database-layer failures ---


class _BrokenEngine:
    """Simulates a real I/O failure at the SQLAlchemy layer -- proves
    `SqlitePortfolioStore` wraps it in `PortfolioStoreError` rather than
    letting a raw `sqlalchemy.exc` type leak past this store's own
    documented "I could not persist" contract.
    """

    def connect(self) -> object:
        raise OperationalError("SELECT 1", {}, Exception("simulated I/O failure"))

    def begin(self) -> object:
        raise OperationalError("INSERT ...", {}, Exception("simulated I/O failure"))


def test_load_wraps_unexpected_database_failure_in_portfolio_store_error() -> None:
    broken = SqlitePortfolioStore(_BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(PortfolioStoreError):
        broken.load()


def test_initialize_wraps_unexpected_database_failure_in_portfolio_store_error() -> None:
    broken = SqlitePortfolioStore(_BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(PortfolioStoreError):
        broken.initialize(build_portfolio())


def test_save_wraps_unexpected_database_failure_in_portfolio_store_error() -> None:
    broken = SqlitePortfolioStore(_BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(PortfolioStoreError):
        broken.save(build_portfolio(), expected_revision=0)


# --- restart persistence: a genuinely separate engine/process re-opens the file ---


def test_state_survives_a_fresh_engine_reopening_the_same_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "orion_test.db")

        engine_a = create_memory_engine(db_path)
        store_a = SqlitePortfolioStore(engine_a)
        portfolio = build_portfolio(cash=100_000.0, starting_balance=100_000.0)
        store_a.initialize(portfolio)
        updated = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
        store_a.save(updated, expected_revision=0)
        engine_a.dispose()

        # A brand new engine/store pointing at the same file -- simulates a
        # process restart. No state is shared in memory between the two.
        engine_b = create_memory_engine(db_path)
        store_b = SqlitePortfolioStore(engine_b)
        record = store_b.load()
        assert record.state == updated
        assert record.revision == 1
        engine_b.dispose()


def test_a_stale_expected_revision_is_still_rejected_after_a_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "orion_test.db")
        engine_a = create_memory_engine(db_path)
        store_a = SqlitePortfolioStore(engine_a)
        store_a.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
        store_a.save(
            build_portfolio(cash=95_000.0, starting_balance=100_000.0), expected_revision=0
        )
        engine_a.dispose()

        engine_b = create_memory_engine(db_path)
        store_b = SqlitePortfolioStore(engine_b)
        with pytest.raises(PortfolioStoreConcurrencyError):
            store_b.save(build_portfolio(cash=1.0, starting_balance=100_000.0), expected_revision=0)
        engine_b.dispose()


# --- genuine concurrent writers (real file, one pooled connection per
# thread -- deliberately NOT the `:memory:` fixture, which pins every
# checkout to a single shared connection via StaticPool and so cannot
# exercise real cross-connection locking behavior) ---


def test_concurrent_writers_racing_on_the_same_expected_revision_exactly_one_wins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_memory_engine(str(Path(tmp) / "concurrent_race.db"))
        store = SqlitePortfolioStore(engine)
        v0 = store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))

        results: list[str] = []
        lock = threading.Lock()

        def attempt(i: int) -> None:
            try:
                store.save(
                    build_portfolio(cash=100_000.0 - i, starting_balance=100_000.0),
                    expected_revision=v0.revision,
                )
                with lock:
                    results.append("accepted")
            except PortfolioStoreConcurrencyError:
                with lock:
                    results.append("rejected")

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count("accepted") == 1
        assert results.count("rejected") == 9
        assert store.load().revision == 1
        engine.dispose()


def test_concurrent_sequential_writers_never_lose_or_duplicate_a_revision() -> None:
    # Five threads each try, in a tight retry-on-conflict loop, to advance
    # the store by exactly one revision using whatever the *current*
    # revision is at the moment they read it -- a realistic "reload and
    # retry on conflict" pattern. No revision may ever be skipped or issued
    # twice; the final cash must reflect exactly `target_advances` debits.
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_memory_engine(str(Path(tmp) / "concurrent_sequential.db"))
        store = SqlitePortfolioStore(engine)
        store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
        target_advances = 20
        counter_lock = threading.Lock()
        remaining = {"count": target_advances}

        def worker() -> None:
            while True:
                with counter_lock:
                    if remaining["count"] <= 0:
                        return
                    remaining["count"] -= 1
                while True:
                    current = store.load()
                    try:
                        store.save(
                            build_portfolio(
                                cash=current.state.cash - 1, starting_balance=100_000.0
                            ),
                            expected_revision=current.revision,
                        )
                        break
                    except PortfolioStoreConcurrencyError:
                        continue

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = store.load()
        assert final.revision == target_advances
        assert final.state.cash == 100_000.0 - target_advances
        engine.dispose()
