"""Shared fixtures for orion.memory tests.

Every engine is `:memory:` by default (fast, isolated, no filesystem I/O)
via `orion.memory.engine.create_memory_engine`, the same factory production
code uses -- these tests exercise the real `SqliteDecisionJournal`/
`SqlitePortfolioStore` against a real SQLite database, not a hand-written
Python-object test double, per the approved Mission 2 architecture (no
parallel in-memory fake to maintain alongside the real implementation).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from orion.memory.engine import create_memory_engine
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.memory.sqlite_journal import SqliteDecisionJournal
from tests.paper.conftest import (
    BASE_TIME,
    ApprovedTrade,
    build_approved_trade,
    build_evidence,
    build_policy,
    build_portfolio,
    build_position,
    build_proposal,
)

__all__ = [
    "BASE_TIME",
    "ApprovedTrade",
    "build_approved_trade",
    "build_evidence",
    "build_policy",
    "build_portfolio",
    "build_position",
    "build_proposal",
]


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_memory_engine(":memory:")
    yield eng
    eng.dispose()


@pytest.fixture
def journal(engine: Engine) -> SqliteDecisionJournal:
    return SqliteDecisionJournal(engine)


@pytest.fixture
def store(engine: Engine) -> SqlitePortfolioStore:
    return SqlitePortfolioStore(engine)
