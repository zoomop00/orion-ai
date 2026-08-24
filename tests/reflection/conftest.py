"""Shared fixtures/builders for orion.reflection tests.

Defines its own `engine`/`journal` fixtures (a real `:memory:` SQLite-backed
`SqliteDecisionJournal`, not a hand-written test double) -- pytest fixtures
are not inherited across sibling test packages via a plain import, only
through the `conftest.py` chain, so these are redefined here rather than
imported from `tests.memory.conftest`, whose *builders* (not fixtures) are
reused below. Every `TradeReflection` under test is computed from a chain
that was actually produced by `decide()`/`review()`/`execute()`/
`resolve_stop_take_profit()`, never hand-assembled, mirroring the
convention already established in `tests/memory/test_journal_fills_and_
closed_positions.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from orion.memory.engine import create_memory_engine
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, Fill
from orion.risk.schemas import RiskVerdict
from tests.memory.conftest import BASE_TIME, build_approved_trade, build_evidence

__all__ = [
    "BASE_TIME",
    "RECORDED_AT",
    "ZERO_COST_POLICY",
    "JournaledTrade",
    "open_close_and_journal",
]

RECORDED_AT = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_memory_engine(":memory:")
    yield eng
    eng.dispose()


@pytest.fixture
def journal(engine: Engine) -> SqliteDecisionJournal:
    return SqliteDecisionJournal(engine)


@dataclass(frozen=True)
class JournaledTrade:
    """A fully-journaled, real open-then-close trade cycle -- every
    fingerprint a test needs to probe `reflect_on_closed_position` with."""

    closed_position_fingerprint: str
    closed_position: ClosedPosition
    verdict: RiskVerdict
    fill: Fill


def open_close_and_journal(
    journal: SqliteDecisionJournal,
    *,
    entry_price: float = 2400.0,
    stop_loss: float = 2350.0,
    take_profit: float = 2500.0,
    exit_price: float = 2500.0,
    cash: float = 100_000.0,
    opened_at: datetime = BASE_TIME,
    closed_at: datetime | None = None,
    journal_evidence: bool = True,
    journal_decision: bool = True,
    journal_verdict: bool = True,
    journal_fill: bool = True,
) -> JournaledTrade:
    """Run a real BUY -> APPROVED -> filled -> closed cycle and journal it,
    returning every fingerprint a test needs. The `journal_*` flags let a
    test deliberately omit one link (to exercise `reflect_on_closed_
    position`'s fail-closed behavior on an incomplete chain) without
    hand-constructing anything."""
    trade = build_approved_trade(
        cash=cash, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit
    )
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=opened_at,
    )
    exit_evidence = build_evidence(last_price=exit_price)
    _, closed = resolve_stop_take_profit(
        result.portfolio,
        exit_evidence,
        policy=ZERO_COST_POLICY,
        now=closed_at or (opened_at + timedelta(hours=1)),
    )
    assert closed is not None

    if journal_evidence:
        journal.record_evidence(trade.evidence, recorded_at=RECORDED_AT)
    if journal_decision:
        journal.record_decision(trade.proposal, recorded_at=RECORDED_AT)
    if journal_verdict:
        journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)
    if journal_fill:
        journal.record_fill(result.fill, recorded_at=RECORDED_AT)
    closed_fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)

    return JournaledTrade(
        closed_position_fingerprint=closed_fp,
        closed_position=closed,
        verdict=trade.verdict,
        fill=result.fill,
    )
