"""Tests for `SqliteDecisionJournal`: idempotent content-addressed writes,
provenance round-tripping, integrity verification on read, and query
methods.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import OperationalError

from orion.analysis.evidence import evidence_fingerprint
from orion.decision.engine import decide
from orion.decision.schemas import decision_fingerprint
from orion.memory.engine import create_memory_engine
from orion.memory.journal import DecisionJournalError, DecisionJournalIntegrityError
from orion.memory.schema import evidence_table, proposed_decision_table, risk_verdict_table
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.provenance import canonical_hash
from orion.risk.guardian import review
from orion.risk.schemas import risk_verdict_fingerprint
from tests.decision.conftest import build_reasoning
from tests.memory.conftest import (
    BASE_TIME,
    build_approved_trade,
    build_evidence,
    build_policy,
    build_portfolio,
)

RECORDED_AT = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)


# --- record_evidence / get_evidence: round trip + idempotency ---


def test_record_evidence_round_trips(journal: SqliteDecisionJournal) -> None:
    evidence = build_evidence()
    fingerprint = journal.record_evidence(evidence, recorded_at=RECORDED_AT)
    assert fingerprint == evidence_fingerprint(evidence)
    assert journal.get_evidence(fingerprint) == evidence


def test_get_evidence_returns_none_for_unknown_fingerprint(journal: SqliteDecisionJournal) -> None:
    assert journal.get_evidence("a" * 64) is None


def test_recording_identical_evidence_twice_is_idempotent(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    evidence = build_evidence()
    fp1 = journal.record_evidence(evidence, recorded_at=RECORDED_AT)
    fp2 = journal.record_evidence(evidence, recorded_at=RECORDED_AT)
    assert fp1 == fp2
    with engine.connect() as conn:
        rows = conn.execute(
            select(evidence_table.c.fingerprint).where(evidence_table.c.fingerprint == fp1)
        ).all()
    assert len(rows) == 1


def test_recording_identical_evidence_twice_with_different_recorded_at_is_still_idempotent(
    journal: SqliteDecisionJournal,
) -> None:
    evidence = build_evidence()
    fp1 = journal.record_evidence(evidence, recorded_at=RECORDED_AT)
    fp2 = journal.record_evidence(evidence, recorded_at=RECORDED_AT + timedelta(days=1))
    assert fp1 == fp2


def test_recording_two_different_evidence_objects_produces_two_rows(
    journal: SqliteDecisionJournal,
) -> None:
    e1 = build_evidence(last_price=2400.0)
    e2 = build_evidence(last_price=2500.0)
    fp1 = journal.record_evidence(e1, recorded_at=RECORDED_AT)
    fp2 = journal.record_evidence(e2, recorded_at=RECORDED_AT)
    assert fp1 != fp2
    assert journal.get_evidence(fp1) == e1
    assert journal.get_evidence(fp2) == e2


# --- record_reasoning / get_reasoning ---


def test_record_reasoning_round_trips(journal: SqliteDecisionJournal) -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(evidence=evidence)
    fingerprint = journal.record_reasoning(reasoning, recorded_at=RECORDED_AT)
    assert fingerprint == canonical_hash(reasoning)
    assert journal.get_reasoning(fingerprint) == reasoning


def test_get_reasoning_returns_none_for_unknown_fingerprint(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.get_reasoning("b" * 64) is None


# --- record_decision / get_decision / list_decisions_by_analysis_id ---


def test_record_decision_round_trips(journal: SqliteDecisionJournal) -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(evidence=evidence)
    decision = decide(evidence, reasoning, analysis_id="cycle-1", now=BASE_TIME)
    fingerprint = journal.record_decision(decision, recorded_at=RECORDED_AT)
    assert fingerprint == decision_fingerprint(decision)
    assert journal.get_decision(fingerprint) == decision


def test_list_decisions_by_analysis_id_returns_only_matching_decisions(
    journal: SqliteDecisionJournal,
) -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(evidence=evidence)
    decision_a = decide(evidence, reasoning, analysis_id="cycle-a", now=BASE_TIME)
    decision_b = decide(evidence, reasoning, analysis_id="cycle-b", now=BASE_TIME)
    journal.record_decision(decision_a, recorded_at=RECORDED_AT)
    journal.record_decision(decision_b, recorded_at=RECORDED_AT)

    results = journal.list_decisions_by_analysis_id("cycle-a")
    assert results == (decision_a,)


def test_list_decisions_by_analysis_id_returns_empty_tuple_when_none_match(
    journal: SqliteDecisionJournal,
) -> None:
    assert journal.list_decisions_by_analysis_id("nonexistent") == ()


# --- record_verdict / get_verdict / list_verdicts_by_* ---


def test_record_verdict_round_trips(journal: SqliteDecisionJournal) -> None:
    trade = build_approved_trade()
    fingerprint = journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)
    assert fingerprint == risk_verdict_fingerprint(trade.verdict)
    assert journal.get_verdict(fingerprint) == trade.verdict


def test_list_verdicts_by_analysis_id(journal: SqliteDecisionJournal) -> None:
    trade = build_approved_trade()
    journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)
    results = journal.list_verdicts_by_analysis_id(trade.verdict.analysis_id)
    assert results == (trade.verdict,)


def test_list_verdicts_by_symbol_filters_by_range_and_orders_ascending(
    journal: SqliteDecisionJournal,
) -> None:
    early = build_approved_trade()
    journal.record_verdict(early.verdict, recorded_at=RECORDED_AT)

    later_time = BASE_TIME + timedelta(days=1)
    late_evidence = build_evidence(last_price=2400.0)
    late_reasoning = build_reasoning(evidence=late_evidence)
    late_decision = decide(late_evidence, late_reasoning, analysis_id="later-cycle", now=later_time)
    late_verdict = review(
        late_decision,
        late_evidence,
        policy=build_policy(),
        portfolio=build_portfolio(as_of=later_time),
        now=later_time,
    )
    journal.record_verdict(late_verdict, recorded_at=RECORDED_AT + timedelta(days=1))

    results = journal.list_verdicts_by_symbol("XAUUSD")
    assert [v.reviewed_at for v in results] == sorted(v.reviewed_at for v in results)
    assert early.verdict in results
    assert late_verdict in results

    only_early = journal.list_verdicts_by_symbol("XAUUSD", end=BASE_TIME)
    assert only_early == (early.verdict,)


def test_list_verdicts_by_symbol_respects_limit(journal: SqliteDecisionJournal) -> None:
    trade = build_approved_trade()
    journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)
    assert journal.list_verdicts_by_symbol("XAUUSD", limit=0) == ()


def test_list_verdicts_by_symbol_excludes_other_symbols(journal: SqliteDecisionJournal) -> None:
    trade = build_approved_trade()
    journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)
    assert journal.list_verdicts_by_symbol("EURUSD") == ()


# --- record_fill / record_closed_position ---


def test_record_fill_and_closed_position_round_trip(journal: SqliteDecisionJournal) -> None:
    trade = build_approved_trade(
        cash=100_000.0, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    fill_fp = journal.record_fill(result.fill, recorded_at=RECORDED_AT)
    assert fill_fp == canonical_hash(result.fill)
    assert journal.record_fill(result.fill, recorded_at=RECORDED_AT) == fill_fp

    exit_evidence = build_evidence(last_price=2500.0)
    _, closed = resolve_stop_take_profit(
        result.portfolio,
        exit_evidence,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME + timedelta(hours=1),
    )
    assert closed is not None
    closed_fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)
    assert closed_fp == canonical_hash(closed)


# --- integrity verification ---


def test_write_raises_integrity_error_on_fingerprint_collision_with_different_content(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    evidence = build_evidence()
    fingerprint = evidence_fingerprint(evidence)
    journal.record_evidence(evidence, recorded_at=RECORDED_AT)

    # Simulate a hash collision (impossible in practice for SHA-256, but the
    # integrity check must not simply assume that): overwrite the stored
    # payload directly at the database level so it no longer matches the
    # fingerprint it is keyed under, then attempt to record the ORIGINAL
    # (true) evidence again under the same fingerprint.
    with engine.begin() as conn:
        conn.execute(
            evidence_table.update()
            .where(evidence_table.c.fingerprint == fingerprint)
            .values(payload='{"tampered": true}')
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.record_evidence(evidence, recorded_at=RECORDED_AT)


def test_get_evidence_raises_integrity_error_on_corrupted_payload(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    evidence = build_evidence()
    fingerprint = journal.record_evidence(evidence, recorded_at=RECORDED_AT)

    # Corrupt the stored payload in a way that still parses as valid
    # Evidence JSON but no longer hashes to its own primary key (e.g. a
    # single field silently altered on disk).
    tampered = evidence.model_copy(update={"last_price": evidence.last_price + 1.0})
    with engine.begin() as conn:
        conn.execute(
            evidence_table.update()
            .where(evidence_table.c.fingerprint == fingerprint)
            .values(payload=tampered.model_dump_json())
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_evidence(fingerprint)


def test_get_verdict_raises_integrity_error_on_corrupted_payload(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    trade = build_approved_trade()
    fingerprint = journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)

    raw = json.loads(trade.verdict.model_dump_json())
    raw["analysis_id"] = "tampered-analysis-id"
    with engine.begin() as conn:
        conn.execute(
            risk_verdict_table.update()
            .where(risk_verdict_table.c.fingerprint == fingerprint)
            .values(payload=json.dumps(raw))
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_verdict(fingerprint)


# --- HIGH fix regression: malformed/schema-invalid payloads must never
# leak a raw pydantic.ValidationError; they must raise
# DecisionJournalIntegrityError, and must never mutate the stored row ---


def test_get_evidence_raises_decision_journal_integrity_error_on_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    """Payload that is not valid JSON at all (simulated disk corruption) --
    the exact gap a fresh adversarial pass found: the ValidationError this
    raises inside model_validate_json must be caught before it can escape,
    not just the "parses fine but hashes wrong" case."""
    evidence = build_evidence()
    fingerprint = journal.record_evidence(evidence, recorded_at=RECORDED_AT)

    with engine.begin() as conn:
        conn.execute(
            evidence_table.update()
            .where(evidence_table.c.fingerprint == fingerprint)
            .values(payload="not even json {{{")
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_evidence(fingerprint)

    # No state returned (the caller never got a fabricated/partial Evidence)
    # and no state modified: the corrupted row is still exactly what was
    # written above, byte for byte -- a failed read must not also mutate.
    with engine.connect() as conn:
        stored = conn.execute(
            select(evidence_table.c.payload).where(evidence_table.c.fingerprint == fingerprint)
        ).scalar_one()
    assert stored == "not even json {{{"


def test_get_verdict_raises_decision_journal_integrity_error_on_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    trade = build_approved_trade()
    fingerprint = journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)

    with engine.begin() as conn:
        conn.execute(
            risk_verdict_table.update()
            .where(risk_verdict_table.c.fingerprint == fingerprint)
            .values(payload="{ this is not valid json")
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_verdict(fingerprint)


def test_get_evidence_raises_decision_journal_integrity_error_on_schema_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    """Payload IS valid JSON, but fails Evidence's own schema (a required
    field missing entirely) -- distinct from "not JSON at all": this exercises
    pydantic's schema-validation failure path, not its JSON-parse failure
    path, both of which must be caught by the same wrapper."""
    evidence = build_evidence()
    fingerprint = journal.record_evidence(evidence, recorded_at=RECORDED_AT)

    schema_invalid_payload = json.dumps({"symbol": "XAUUSD"})  # missing every other required field
    with engine.begin() as conn:
        conn.execute(
            evidence_table.update()
            .where(evidence_table.c.fingerprint == fingerprint)
            .values(payload=schema_invalid_payload)
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_evidence(fingerprint)

    with engine.connect() as conn:
        stored = conn.execute(
            select(evidence_table.c.payload).where(evidence_table.c.fingerprint == fingerprint)
        ).scalar_one()
    assert stored == schema_invalid_payload


def test_get_decision_raises_decision_journal_integrity_error_on_schema_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(evidence=evidence)
    decision = decide(evidence, reasoning, analysis_id="cycle-schema-invalid", now=BASE_TIME)
    fingerprint = journal.record_decision(decision, recorded_at=RECORDED_AT)

    raw = json.loads(decision.model_dump_json())
    del raw["action"]  # required field removed -- schema-invalid, still valid JSON
    with engine.begin() as conn:
        conn.execute(
            proposed_decision_table.update()
            .where(proposed_decision_table.c.fingerprint == fingerprint)
            .values(payload=json.dumps(raw))
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.get_decision(fingerprint)


def test_list_verdicts_by_symbol_raises_decision_journal_integrity_error_on_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    """The list_* / _verdicts_from_rows path is a separate code path from
    get_verdict -- must be independently proven, not assumed to share the
    fix just because get_verdict does."""
    trade = build_approved_trade()
    fingerprint = journal.record_verdict(trade.verdict, recorded_at=RECORDED_AT)

    with engine.begin() as conn:
        conn.execute(
            risk_verdict_table.update()
            .where(risk_verdict_table.c.fingerprint == fingerprint)
            .values(payload="garbage, not json")
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.list_verdicts_by_symbol("XAUUSD")


def test_list_decisions_by_analysis_id_raises_decision_journal_integrity_error_on_invalid_json(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    evidence = build_evidence()
    reasoning = build_reasoning(evidence=evidence)
    decision = decide(evidence, reasoning, analysis_id="cycle-list-invalid", now=BASE_TIME)
    fingerprint = journal.record_decision(decision, recorded_at=RECORDED_AT)

    with engine.begin() as conn:
        conn.execute(
            proposed_decision_table.update()
            .where(proposed_decision_table.c.fingerprint == fingerprint)
            .values(payload="not json")
        )

    with pytest.raises(DecisionJournalIntegrityError):
        journal.list_decisions_by_analysis_id("cycle-list-invalid")


# --- fail-closed wrapping of unexpected database-layer failures ---


class _BrokenEngine:
    """Simulates a real I/O failure (disk error, permission denial, a
    connection drop) at the SQLAlchemy layer, to prove `SqliteDecisionJournal`
    actually wraps such failures in `DecisionJournalError` rather than
    letting a raw `sqlalchemy.exc` type leak past its own error hierarchy —
    the same "I could not persist" contract every other provider boundary
    in this codebase (`ReasoningProviderError`, `PortfolioStoreError`) is
    held to.
    """

    def connect(self) -> object:
        raise OperationalError("SELECT 1", {}, Exception("simulated I/O failure"))

    def begin(self) -> object:
        raise OperationalError("INSERT ...", {}, Exception("simulated I/O failure"))


def test_write_wraps_unexpected_database_failure_in_decision_journal_error() -> None:
    journal = SqliteDecisionJournal(_BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(DecisionJournalError):
        journal.record_evidence(build_evidence(), recorded_at=RECORDED_AT)


def test_read_wraps_unexpected_database_failure_in_decision_journal_error() -> None:
    journal = SqliteDecisionJournal(_BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(DecisionJournalError):
        journal.get_evidence("a" * 64)


def test_query_wraps_unexpected_database_failure_in_decision_journal_error() -> None:
    journal = SqliteDecisionJournal(_BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(DecisionJournalError):
        journal.list_verdicts_by_symbol("XAUUSD")


# --- determinism ---


def test_recorded_fingerprints_are_deterministic_across_independent_journals(
    engine: Engine,
) -> None:
    j1 = SqliteDecisionJournal(engine)
    j2 = SqliteDecisionJournal(create_memory_engine(":memory:"))
    evidence = build_evidence()
    fp1 = j1.record_evidence(evidence, recorded_at=RECORDED_AT)
    fp2 = j2.record_evidence(evidence, recorded_at=RECORDED_AT)
    assert fp1 == fp2
