from __future__ import annotations

import pytest

from orion.paper.store import InMemoryPortfolioStore, PortfolioStoreError
from tests.paper.conftest import build_portfolio


def test_load_before_initialize_raises() -> None:
    store = InMemoryPortfolioStore()
    with pytest.raises(PortfolioStoreError):
        store.load()


def test_initialize_then_load_returns_the_same_state() -> None:
    store = InMemoryPortfolioStore()
    portfolio = build_portfolio()
    store.initialize(portfolio)
    assert store.load() == portfolio


def test_double_initialize_raises() -> None:
    store = InMemoryPortfolioStore()
    store.initialize(build_portfolio())
    with pytest.raises(PortfolioStoreError):
        store.initialize(build_portfolio())


def test_save_updates_the_stored_state() -> None:
    store = InMemoryPortfolioStore()
    store.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    updated = build_portfolio(cash=95_000.0, starting_balance=100_000.0)
    store.save(updated)
    assert store.load() == updated


def test_save_before_initialize_still_works() -> None:
    # save() has no "must already exist" requirement -- only initialize()
    # refuses to clobber an existing snapshot.
    store = InMemoryPortfolioStore()
    portfolio = build_portfolio()
    store.save(portfolio)
    assert store.load() == portfolio


def test_save_rejects_a_non_portfolio_state() -> None:
    store = InMemoryPortfolioStore()
    with pytest.raises(PortfolioStoreError):
        store.save("not-a-portfolio")  # type: ignore[arg-type]


def test_two_stores_are_independent() -> None:
    store_a = InMemoryPortfolioStore()
    store_b = InMemoryPortfolioStore()
    store_a.initialize(build_portfolio(cash=100_000.0, starting_balance=100_000.0))
    with pytest.raises(PortfolioStoreError):
        store_b.load()
