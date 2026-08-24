"""Tests for `SqliteDecisionJournal`'s Fill/ClosedPosition read paths --
`get_fill`, `get_closed_position`, and the four `list_*` query methods added
to close the "Mission 5 needs realized trade outcomes but the journal had no
read path for them" gap found in the Missions 2-4 integration audit.

Mirrors `tests/memory/test_journal.py`'s existing conventions exactly (the
same corruption/integrity/idempotency/restart patterns already proven there
for Evidence/ReasoningOutput/ProposedDecision/RiskVerdict) so this suite is
additive coverage of the same guarantees, not a new testing philosophy.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from orion.memory.engine import create_memory_engine
from orion.memory.journal import DecisionJournal, DecisionJournalIntegrityError
from orion.memory.schema import closed_position_table, fill_table
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, CloseReason, Fill
from orion.provenance import canonical_hash
from orion.reasoning.schemas import Direction
from tests.memory.conftest import BASE_TIME, build_approved_trade, build_evidence

RECORDED_AT = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)
DUMMY_VERDICT_FP = "a" * 64
OTHER_VERDICT_FP = "b" * 64


def _open_and_close(
    *,
    entry_price: float = 2400.0,
    stop_loss: float = 2350.0,
    take_profit: float = 2500.0,
    exit_price: float = 2500.0,
    opened_at: datetime = BASE_TIME,
    closed_at: datetime | None = None,
) -> tuple[Fill, ClosedPosition]:
    """A legitimate Fill + ClosedPosition pair, produced by the real
    `execute()`/`resolve_stop_take_profit()` pipeline -- not hand-built --
    for the positive-path tests below."""
    trade = build_approved_trade(
        cash=100_000.0, entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit
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
    return result.fill, closed


def _hand_built_fill(
    *, symbol: str = "EURUSD", filled_at: datetime = BASE_TIME, verdict_fp: str = OTHER_VERDICT_FP
) -> Fill:
    """A standalone Fill, not produced by execute() -- legitimate test data
    for symbol-diversity queries; Fill has no cross-object validator that
    would reject this (see docs/DECISIONS.md's writer-trust-boundary note)."""
    return Fill(
        symbol=symbol,
        direction=Direction.LONG,
        quantity=1.0,
        fill_price=1.10,
        fee=0.0,
        filled_at=filled_at,
        risk_verdict_fingerprint=verdict_fp,
    )


def _hand_built_closed_position(
    *, symbol: str = "EURUSD", closed_at: datetime = BASE_TIME, verdict_fp: str = OTHER_VERDICT_FP
) -> ClosedPosition:
    return ClosedPosition(
        symbol=symbol,
        direction=Direction.LONG,
        quantity=1.0,
        entry_price=1.10,
        exit_price=1.12,
        opened_at=closed_at - timedelta(hours=1),
        closed_at=closed_at,
        close_reason=CloseReason.TAKE_PROFIT_HIT,
        realized_pnl=0.02,
        fee=0.0,
        opened_by_verdict_fingerprint=verdict_fp,
    )


# --- get_fill: success / missing ---


def test_get_fill_round_trips(journal: SqliteDecisionJournal) -> None:
    fill, _ = _open_and_close()
    fp = journal.record_fill(fill, recorded_at=RECORDED_AT)
    assert fp == canonical_hash(fill)
    assert journal.get_fill(fp) == fill


def test_get_fill_returns_none_for_unknown_fingerprint(journal: SqliteDecisionJournal) -> None:
    assert journal.get_fill("c" * 64) is None


# --- get_closed_position: success / missing ---


def test_get_closed_position_round_trips(journal: SqliteDecisionJournal) -> None:
    _, closed = _open_and_close()
    fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    assert fp == canonical_hash(closed)
    assert journal.get_closed_position(fp) == closed


def test_get_closed_position_returns_none_for_unknown_fingerprint(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.get_closed_position("d" * 64) is None


# --- list_fills_by_verdict_fingerprint ---


def test_list_fills_by_verdict_fingerprint_isolates_the_right_fills(
    journal: SqliteDecisionJournal,
) -> None:
    fill, _ = _open_and_close()
    journal.record_fill(fill, recorded_at=RECORDED_AT)
    other = _hand_built_fill(symbol="XAUUSD", verdict_fp=OTHER_VERDICT_FP)
    journal.record_fill(other, recorded_at=RECORDED_AT)

    results = journal.list_fills_by_verdict_fingerprint(fill.risk_verdict_fingerprint)
    assert results == (fill,)
    assert other not in results


def test_list_fills_by_verdict_fingerprint_returns_empty_tuple_when_none_match(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.list_fills_by_verdict_fingerprint("e" * 64) == ()


# --- list_closed_positions_by_verdict_fingerprint ---


def test_list_closed_positions_by_verdict_fingerprint_isolates_the_right_records(
    journal: SqliteDecisionJournal,
) -> None:
    _, closed = _open_and_close()
    journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    other = _hand_built_closed_position(symbol="XAUUSD", verdict_fp=OTHER_VERDICT_FP)
    journal.record_closed_position(other, recorded_at=RECORDED_AT)

    results = journal.list_closed_positions_by_verdict_fingerprint(
        closed.opened_by_verdict_fingerprint
    )
    assert results == (closed,)
    assert other not in results


def test_list_closed_positions_by_verdict_fingerprint_returns_empty_tuple_when_none_match(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.list_closed_positions_by_verdict_fingerprint("f" * 64) == ()


# --- list_fills_by_symbol / list_closed_positions_by_symbol: symbol filtering ---


def test_list_fills_by_symbol_excludes_other_symbols(journal: SqliteDecisionJournal) -> None:
    xauusd_fill, _ = _open_and_close()
    journal.record_fill(xauusd_fill, recorded_at=RECORDED_AT)
    eurusd_fill = _hand_built_fill(symbol="EURUSD")
    journal.record_fill(eurusd_fill, recorded_at=RECORDED_AT)

    assert journal.list_fills_by_symbol("XAUUSD") == (xauusd_fill,)
    assert journal.list_fills_by_symbol("EURUSD") == (eurusd_fill,)
    assert journal.list_fills_by_symbol("GBPUSD") == ()


def test_list_closed_positions_by_symbol_excludes_other_symbols(
    journal: SqliteDecisionJournal,
) -> None:
    _, xauusd_closed = _open_and_close()
    journal.record_closed_position(xauusd_closed, recorded_at=RECORDED_AT)
    eurusd_closed = _hand_built_closed_position(symbol="EURUSD")
    journal.record_closed_position(eurusd_closed, recorded_at=RECORDED_AT)

    assert journal.list_closed_positions_by_symbol("XAUUSD") == (xauusd_closed,)
    assert journal.list_closed_positions_by_symbol("EURUSD") == (eurusd_closed,)
    assert journal.list_closed_positions_by_symbol("GBPUSD") == ()


# --- date-range filtering + pagination + deterministic ordering ---


def test_list_fills_by_symbol_filters_by_date_range_and_orders_ascending(
    journal: SqliteDecisionJournal,
) -> None:
    early = _hand_built_fill(symbol="XAUUSD", filled_at=BASE_TIME)
    mid = _hand_built_fill(symbol="XAUUSD", filled_at=BASE_TIME + timedelta(hours=1))
    late = _hand_built_fill(symbol="XAUUSD", filled_at=BASE_TIME + timedelta(hours=2))
    for f in (late, early, mid):  # recorded out of chronological order deliberately
        journal.record_fill(f, recorded_at=RECORDED_AT)

    all_results = journal.list_fills_by_symbol("XAUUSD")
    assert all_results == (early, mid, late)  # ordered by filled_at, not insertion order

    windowed = journal.list_fills_by_symbol(
        "XAUUSD", start=BASE_TIME + timedelta(hours=1), end=BASE_TIME + timedelta(hours=1)
    )
    assert windowed == (mid,)

    empty_range = journal.list_fills_by_symbol(
        "XAUUSD", start=BASE_TIME + timedelta(hours=3), end=BASE_TIME + timedelta(hours=1)
    )
    assert empty_range == ()


def test_list_closed_positions_by_symbol_filters_by_date_range_and_orders_ascending(
    journal: SqliteDecisionJournal,
) -> None:
    early = _hand_built_closed_position(symbol="XAUUSD", closed_at=BASE_TIME)
    mid = _hand_built_closed_position(symbol="XAUUSD", closed_at=BASE_TIME + timedelta(hours=1))
    late = _hand_built_closed_position(symbol="XAUUSD", closed_at=BASE_TIME + timedelta(hours=2))
    for c in (mid, late, early):
        journal.record_closed_position(c, recorded_at=RECORDED_AT)

    assert journal.list_closed_positions_by_symbol("XAUUSD") == (early, mid, late)


def test_list_fills_by_symbol_pagination_boundaries(journal: SqliteDecisionJournal) -> None:
    fills = [
        _hand_built_fill(symbol="XAUUSD", filled_at=BASE_TIME + timedelta(hours=i))
        for i in range(3)
    ]
    for f in fills:
        journal.record_fill(f, recorded_at=RECORDED_AT)

    assert journal.list_fills_by_symbol("XAUUSD", limit=0) == ()
    assert journal.list_fills_by_symbol("XAUUSD", limit=1) == (fills[0],)
    assert journal.list_fills_by_symbol("XAUUSD", limit=2) == (fills[0], fills[1])
    assert journal.list_fills_by_symbol("XAUUSD", limit=1000) == tuple(fills)


def test_list_closed_positions_by_symbol_pagination_boundaries(
    journal: SqliteDecisionJournal,
) -> None:
    closes = [
        _hand_built_closed_position(symbol="XAUUSD", closed_at=BASE_TIME + timedelta(hours=i))
        for i in range(3)
    ]
    for c in closes:
        journal.record_closed_position(c, recorded_at=RECORDED_AT)

    assert journal.list_closed_positions_by_symbol("XAUUSD", limit=0) == ()
    assert journal.list_closed_positions_by_symbol("XAUUSD", limit=2) == tuple(closes[:2])
    assert journal.list_closed_positions_by_symbol("XAUUSD", limit=1000) == tuple(closes)


def test_list_fills_by_symbol_returns_empty_tuple_for_unknown_symbol(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.list_fills_by_symbol("NEVERTRADED") == ()


def test_list_closed_positions_by_symbol_returns_empty_tuple_for_unknown_symbol(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.list_closed_positions_by_symbol("NEVERTRADED") == ()


# --- duplicate / idempotent records ---


def test_recording_identical_fill_twice_is_idempotent(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    fill, _ = _open_and_close()
    fp1 = journal.record_fill(fill, recorded_at=RECORDED_AT)
    fp2 = journal.record_fill(fill, recorded_at=RECORDED_AT + timedelta(days=1))
    assert fp1 == fp2
    with engine.connect() as conn:
        rows = conn.execute(
            select(fill_table.c.fingerprint).where(fill_table.c.fingerprint == fp1)
        ).all()
    assert len(rows) == 1


def test_recording_identical_closed_position_twice_is_idempotent(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    _, closed = _open_and_close()
    fp1 = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    fp2 = journal.record_closed_position(closed, recorded_at=RECORDED_AT + timedelta(days=1))
    assert fp1 == fp2
    with engine.connect() as conn:
        rows = conn.execute(
            select(closed_position_table.c.fingerprint).where(
                closed_position_table.c.fingerprint == fp1
            )
        ).all()
    assert len(rows) == 1


# --- corrupted payloads: invalid JSON, schema-invalid JSON, hash mismatch ---


def test_get_fill_raises_integrity_error_on_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    fill, _ = _open_and_close()
    fp = journal.record_fill(fill, recorded_at=RECORDED_AT)
    with engine.begin() as conn:
        conn.execute(
            fill_table.update()
            .where(fill_table.c.fingerprint == fp)
            .values(payload="not even json {{{")
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_fill(fp)


def test_get_fill_raises_integrity_error_on_schema_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    fill, _ = _open_and_close()
    fp = journal.record_fill(fill, recorded_at=RECORDED_AT)
    schema_invalid = json.dumps({"symbol": "XAUUSD"})  # missing every other required field
    with engine.begin() as conn:
        conn.execute(
            fill_table.update().where(fill_table.c.fingerprint == fp).values(payload=schema_invalid)
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_fill(fp)


def test_get_fill_raises_integrity_error_on_hash_mismatch(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    fill, _ = _open_and_close()
    fp = journal.record_fill(fill, recorded_at=RECORDED_AT)
    tampered = fill.model_copy(update={"fill_price": fill.fill_price + 1.0})
    with engine.begin() as conn:
        conn.execute(
            fill_table.update()
            .where(fill_table.c.fingerprint == fp)
            .values(payload=tampered.model_dump_json())
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_fill(fp)


def test_get_closed_position_raises_integrity_error_on_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    _, closed = _open_and_close()
    fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    with engine.begin() as conn:
        conn.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == fp)
            .values(payload="garbage, not json")
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_closed_position(fp)


def test_get_closed_position_raises_integrity_error_on_schema_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    _, closed = _open_and_close()
    fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    with engine.begin() as conn:
        conn.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == fp)
            .values(payload="{}")
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_closed_position(fp)


def test_get_closed_position_raises_integrity_error_on_hash_mismatch(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    _, closed = _open_and_close()
    fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    tampered = closed.model_copy(update={"realized_pnl": closed.realized_pnl + 1000.0})
    with engine.begin() as conn:
        conn.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == fp)
            .values(payload=tampered.model_dump_json())
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_closed_position(fp)


def test_list_fills_by_symbol_raises_integrity_error_on_corrupted_row(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    fill, _ = _open_and_close()
    fp = journal.record_fill(fill, recorded_at=RECORDED_AT)
    with engine.begin() as conn:
        conn.execute(
            fill_table.update().where(fill_table.c.fingerprint == fp).values(payload="not json")
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.list_fills_by_symbol("XAUUSD")


def test_list_closed_positions_by_symbol_raises_integrity_error_on_corrupted_row(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    _, closed = _open_and_close()
    fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    with engine.begin() as conn:
        conn.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == fp)
            .values(payload="not json")
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.list_closed_positions_by_symbol("XAUUSD")


# --- fingerprint collision (same fingerprint, different content) ---


def test_record_fill_raises_integrity_error_on_fingerprint_collision(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    fill, _ = _open_and_close()
    fp = journal.record_fill(fill, recorded_at=RECORDED_AT)
    with engine.begin() as conn:
        conn.execute(
            fill_table.update()
            .where(fill_table.c.fingerprint == fp)
            .values(payload='{"tampered": true}')
        )
    with pytest.raises(DecisionJournalIntegrityError):
        journal.record_fill(fill, recorded_at=RECORDED_AT)


# --- restart persistence ---


def test_fill_and_closed_position_survive_a_fresh_engine_reopening_the_same_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "journal_fills_restart.db")
        engine_a = create_memory_engine(db_path)
        journal_a = SqliteDecisionJournal(engine_a)
        fill, closed = _open_and_close()
        fill_fp = journal_a.record_fill(fill, recorded_at=RECORDED_AT)
        closed_fp = journal_a.record_closed_position(closed, recorded_at=RECORDED_AT)
        engine_a.dispose()

        engine_b = create_memory_engine(db_path)
        journal_b = SqliteDecisionJournal(engine_b)
        assert journal_b.get_fill(fill_fp) == fill
        assert journal_b.get_closed_position(closed_fp) == closed
        assert journal_b.list_fills_by_symbol("XAUUSD") == (fill,)
        assert journal_b.list_closed_positions_by_symbol("XAUUSD") == (closed,)
        assert journal_b.list_fills_by_verdict_fingerprint(fill.risk_verdict_fingerprint) == (fill,)
        assert journal_b.list_closed_positions_by_verdict_fingerprint(
            closed.opened_by_verdict_fingerprint
        ) == (closed,)
        engine_b.dispose()


# --- no update/delete capability remains ---


def test_decision_journal_still_has_no_update_or_delete_methods() -> None:
    forbidden_substrings = ("update", "delete", "remove", "mutate", "set_")
    offenders = [
        name
        for name in DecisionJournal.__abstractmethods__
        if any(bad in name for bad in forbidden_substrings)
    ]
    assert offenders == []
    # The new methods are additive only -- confirm they actually landed.
    assert "get_fill" in DecisionJournal.__abstractmethods__
    assert "get_closed_position" in DecisionJournal.__abstractmethods__
    assert "list_fills_by_verdict_fingerprint" in DecisionJournal.__abstractmethods__
    assert "list_fills_by_symbol" in DecisionJournal.__abstractmethods__
    assert "list_closed_positions_by_verdict_fingerprint" in DecisionJournal.__abstractmethods__
    assert "list_closed_positions_by_symbol" in DecisionJournal.__abstractmethods__
