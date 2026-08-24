"""Tests for `orion.reflection.analysis.reflect_on_closed_position`.

Covers: the positive path against a real journaled chain, determinism,
every fail-closed path (missing verdict/decision/closed-position, corrupted
journal payload propagating unchanged, naive timestamp), Fill
disambiguation (zero/one/ambiguous matches), and that `TradeReflection`
carries no autonomous judgment -- only the objective facts it was built to
expose.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from orion.memory.journal import DecisionJournalIntegrityError
from orion.memory.schema import closed_position_table
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason, Fill
from orion.provenance import canonical_hash
from orion.reasoning.schemas import Action, Direction, RiskLevel
from orion.reflection.analysis import ReflectionError, reflect_on_closed_position
from orion.reflection.schemas import TradeReflection
from tests.memory.conftest import build_evidence
from tests.paper.conftest import build_approved_trade
from tests.reflection.conftest import (
    BASE_TIME,
    RECORDED_AT,
    open_close_and_journal,
)

# --- positive path ---


def test_reflect_on_closed_position_take_profit_round_trip(
    journal: SqliteDecisionJournal,
) -> None:
    trade = open_close_and_journal(
        journal, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0, exit_price=2500.0
    )
    reflected_at = BASE_TIME + timedelta(hours=2)

    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=reflected_at
    )

    assert reflection.closed_position_fingerprint == trade.closed_position_fingerprint
    assert (
        reflection.opened_by_verdict_fingerprint
        == trade.closed_position.opened_by_verdict_fingerprint
    )
    assert reflection.decision_fingerprint == trade.verdict.decision_ref.content_hash

    assert reflection.predicted_action is Action.BUY
    assert reflection.predicted_direction is Direction.LONG
    assert reflection.predicted_confidence == 0.6
    assert reflection.predicted_risk_level is RiskLevel.MEDIUM
    assert reflection.predicted_stop_loss == 2350.0
    assert reflection.predicted_take_profit == 2500.0
    # predicted RR against evidence.last_price (2400.0): (2500-2400)/(2400-2350) = 2.0
    assert reflection.predicted_risk_reward == pytest.approx(2.0)

    assert reflection.policy_min_risk_reward == 1.0
    assert reflection.approval_margin == pytest.approx(1.0)

    assert reflection.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert reflection.entry_price == 2400.0
    assert reflection.exit_price == 2500.0
    assert reflection.realized_pnl == trade.closed_position.realized_pnl
    assert reflection.closing_fee == trade.closed_position.fee
    assert reflection.opening_fee == trade.fill.fee

    assert reflection.exit_matched_predicted_take_profit is True
    assert reflection.exit_matched_predicted_stop_loss is False
    assert reflection.reflected_at == reflected_at


def test_reflect_on_closed_position_stop_loss_round_trip(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(
        journal, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0, exit_price=2350.0
    )
    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    assert reflection.close_reason is CloseReason.STOP_LOSS_HIT
    assert reflection.exit_matched_predicted_take_profit is False
    assert reflection.exit_matched_predicted_stop_loss is True
    assert reflection.realized_pnl < 0


def test_reflect_on_closed_position_nonzero_fees_split_correctly(
    journal: SqliteDecisionJournal,
) -> None:
    # A real (non-zero-cost) policy, built by hand rather than via
    # open_close_and_journal (which hardcodes ZERO_COST_POLICY), to prove
    # opening and closing fees are attributed to the correct leg, not
    # summed together or confused with each other.
    real_policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    approved = build_approved_trade(
        cash=100_000.0, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    result = execute(
        approved.proposal,
        approved.verdict,
        approved.evidence,
        approved.portfolio,
        quantity=1.0,
        policy=real_policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2500.0)
    _, closed = resolve_stop_take_profit(
        result.portfolio, exit_evidence, policy=real_policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    journal.record_decision(approved.proposal, recorded_at=RECORDED_AT)
    journal.record_verdict(approved.verdict, recorded_at=RECORDED_AT)
    journal.record_fill(result.fill, recorded_at=RECORDED_AT)
    closed_fp = journal.record_closed_position(closed, recorded_at=RECORDED_AT)

    reflection = reflect_on_closed_position(journal, closed_fp, now=BASE_TIME + timedelta(hours=2))
    assert result.fill.fee > 0 and closed.fee > 0  # sanity: both legs genuinely charged a fee
    assert reflection.opening_fee == result.fill.fee
    assert reflection.closing_fee == closed.fee


# --- determinism ---


def test_reflect_on_closed_position_is_deterministic(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal)
    now = BASE_TIME + timedelta(hours=2)
    r1 = reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=now)
    r2 = reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=now)
    assert r1 == r2


# --- fail-closed: missing chain links ---


def test_reflect_raises_on_unknown_closed_position_fingerprint(
    journal: SqliteDecisionJournal,
) -> None:
    with pytest.raises(ReflectionError, match="no ClosedPosition"):
        reflect_on_closed_position(journal, "a" * 64, now=BASE_TIME)


def test_reflect_raises_when_verdict_missing_from_journal(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal, journal_verdict=False)
    with pytest.raises(ReflectionError, match="not in the journal"):
        reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=BASE_TIME)


def test_reflect_raises_when_decision_missing_from_journal(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal, journal_decision=False)
    with pytest.raises(ReflectionError, match="not in the journal"):
        reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=BASE_TIME)


# --- fail-closed: corrupted journal payload propagates unchanged ---


def test_reflect_propagates_decision_journal_integrity_error_on_corrupted_closed_position(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    trade = open_close_and_journal(journal)
    with engine.begin() as conn:
        conn.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == trade.closed_position_fingerprint)
            .values(payload="not even json {{{")
        )
    with pytest.raises(DecisionJournalIntegrityError):
        reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=BASE_TIME)


def test_reflect_propagates_decision_journal_integrity_error_on_hash_mismatch(
    journal: SqliteDecisionJournal, engine: Engine
) -> None:
    trade = open_close_and_journal(journal)
    tampered = trade.closed_position.model_copy(update={"realized_pnl": 999_999.0})
    with engine.begin() as conn:
        conn.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == trade.closed_position_fingerprint)
            .values(payload=tampered.model_dump_json())
        )
    with pytest.raises(DecisionJournalIntegrityError):
        reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=BASE_TIME)


# --- fail-closed: naive timestamp ---


def test_reflect_raises_on_naive_now(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal)
    naive_now = datetime(2026, 8, 24, 12, 0)  # no tzinfo
    with pytest.raises(ReflectionError, match="timezone-aware"):
        reflect_on_closed_position(journal, trade.closed_position_fingerprint, now=naive_now)


# --- Fill disambiguation: zero / one / ambiguous matches ---


def test_reflect_opening_fee_is_none_when_no_fill_journaled(
    journal: SqliteDecisionJournal,
) -> None:
    trade = open_close_and_journal(journal, journal_fill=False)
    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    assert reflection.opening_fee is None


def test_reflect_opening_fee_matches_the_single_fill(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal)
    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    assert reflection.opening_fee == trade.fill.fee


def test_reflect_raises_on_ambiguous_multiple_matching_fills(
    journal: SqliteDecisionJournal,
) -> None:
    trade = open_close_and_journal(journal)
    # Journal a SECOND, distinct Fill for the same verdict, at the exact
    # same filled_at as the real one (the only field reflect_on_closed_
    # position uses to disambiguate) -- simulates a corrupted/duplicated
    # write, not a legitimate replay (a legitimate replay would have a
    # different filled_at).
    duplicate_fill = Fill(
        symbol=trade.fill.symbol,
        direction=trade.fill.direction,
        quantity=trade.fill.quantity + 1.0,  # different content -> different fingerprint
        fill_price=trade.fill.fill_price,
        fee=trade.fill.fee,
        filled_at=trade.fill.filled_at,
        risk_verdict_fingerprint=trade.fill.risk_verdict_fingerprint,
    )
    assert canonical_hash(duplicate_fill) != canonical_hash(trade.fill)
    journal.record_fill(duplicate_fill, recorded_at=RECORDED_AT)

    with pytest.raises(ReflectionError, match="cannot determine which one"):
        reflect_on_closed_position(
            journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
        )


def test_reflect_ignores_a_fill_with_a_different_filled_at(
    journal: SqliteDecisionJournal,
) -> None:
    trade = open_close_and_journal(journal)
    # A second Fill for the same verdict but a DIFFERENT filled_at (e.g. a
    # legitimate replay at a different `now`) must not be treated as
    # ambiguous -- only the timestamp-matching Fill is a candidate.
    other_time_fill = Fill(
        symbol=trade.fill.symbol,
        direction=trade.fill.direction,
        quantity=trade.fill.quantity,
        fill_price=trade.fill.fill_price,
        fee=trade.fill.fee,
        filled_at=trade.fill.filled_at + timedelta(minutes=5),
        risk_verdict_fingerprint=trade.fill.risk_verdict_fingerprint,
    )
    journal.record_fill(other_time_fill, recorded_at=RECORDED_AT)

    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    assert reflection.opening_fee == trade.fill.fee


# --- TradeReflection is frozen: no mutation, no update/delete capability ---


def test_trade_reflection_is_frozen(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal)
    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    with pytest.raises(ValidationError):
        reflection.realized_pnl = 0.0  # type: ignore[misc]


def test_trade_reflection_rejects_unexpected_fields(journal: SqliteDecisionJournal) -> None:
    trade = open_close_and_journal(journal)
    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    payload = json.loads(reflection.model_dump_json())
    payload["quality_score"] = 0.9  # an invented metric this schema must reject

    with pytest.raises(ValidationError):
        TradeReflection.model_validate(payload)


def test_trade_reflection_validator_rejects_inconsistent_exit_match_flags(
    journal: SqliteDecisionJournal,
) -> None:
    trade = open_close_and_journal(journal)
    reflection = reflect_on_closed_position(
        journal, trade.closed_position_fingerprint, now=BASE_TIME + timedelta(hours=2)
    )
    payload = json.loads(reflection.model_dump_json())
    payload["exit_matched_predicted_stop_loss"] = True  # contradicts close_reason

    with pytest.raises(ValidationError):
        TradeReflection.model_validate(payload)
