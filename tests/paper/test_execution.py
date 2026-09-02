"""Tests for `orion.paper.execution` -- deterministic paper order/fill
simulation and last-price-only stop/take-profit resolution.

Covers: correctness (fill price direction, fee, resulting portfolio),
determinism, cash conservation across a full open/close round trip, BUY/SELL
P&L sign correctness, the "only an APPROVED verdict is executable" boundary,
and every fail-closed raise path.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orion.paper.execution import execute, resolve_stop_take_profit
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason, PaperExecutionError
from orion.reasoning.schemas import Action, Direction
from orion.risk.guardian import review
from orion.risk.policy import KillSwitchState
from orion.risk.portfolio import mark_to_market, portfolio_state_fingerprint
from orion.risk.schemas import RiskOutcome, RiskReasonCode, risk_verdict_fingerprint
from tests.paper.conftest import (
    BASE_TIME,
    build_approved_trade,
    build_evidence,
    build_policy,
    build_portfolio,
    build_position,
    build_proposal,
)

ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)


# --- execute(): opening a position ---


def test_execute_opens_a_long_position_with_a_worse_than_reference_fill_price() -> None:
    trade = build_approved_trade(action=Action.BUY, direction=Direction.LONG, entry_price=2400.0)
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    # A LONG fills higher than the reference price -- worse for the buyer.
    assert result.fill.fill_price > trade.evidence.last_price
    assert result.fill.direction is Direction.LONG
    assert result.fill.fee > 0
    assert result.portfolio.open_position is not None
    assert result.portfolio.open_position.entry_price == result.fill.fill_price
    assert result.portfolio.cash < trade.portfolio.cash


def test_execute_opens_a_short_position_with_a_worse_than_reference_fill_price() -> None:
    trade = build_approved_trade(
        action=Action.SELL,
        direction=Direction.SHORT,
        entry_price=2400.0,
        stop_loss=2450.0,
        take_profit=2300.0,
    )
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    # A SHORT fills lower than the reference price -- worse for the seller.
    assert result.fill.fill_price < trade.evidence.last_price
    assert result.fill.direction is Direction.SHORT


def test_execute_records_the_risk_verdict_fingerprint_on_the_fill_and_position() -> None:
    trade = build_approved_trade()
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    expected = risk_verdict_fingerprint(trade.verdict)
    assert result.fill.risk_verdict_fingerprint == expected
    assert result.portfolio.open_position is not None
    assert result.portfolio.open_position.opened_by_verdict_fingerprint == expected


def test_execute_with_zero_cost_policy_fills_at_the_reference_price() -> None:
    trade = build_approved_trade(entry_price=2400.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    assert result.fill.fill_price == pytest.approx(2400.0)
    assert result.fill.fee == 0.0


def test_execute_is_deterministic() -> None:
    trade = build_approved_trade()
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    result_1 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    result_2 = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    assert result_1.fill == result_2.fill
    assert result_1.portfolio == result_2.portfolio


# --- execute(): fail-closed raise paths ---


def test_execute_rejects_a_vetoed_verdict() -> None:
    evidence = build_evidence(last_price=2400.0)
    proposal = build_proposal(action=Action.BUY, direction=Direction.LONG, evidence=evidence)
    vetoing_policy = build_policy(kill_switch=KillSwitchState.ENGAGED)
    portfolio = build_portfolio()
    verdict = review(proposal, evidence, policy=vetoing_policy, portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.VETOED
    with pytest.raises(PaperExecutionError, match="not APPROVED"):
        execute(
            proposal,
            verdict,
            evidence,
            portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_no_trade_proposed_verdict() -> None:
    evidence = build_evidence()
    proposal = build_proposal(action=Action.WAIT, evidence=evidence)
    portfolio = build_portfolio()
    verdict = review(proposal, evidence, policy=build_policy(), portfolio=portfolio, now=BASE_TIME)
    assert verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED
    with pytest.raises(PaperExecutionError, match="not APPROVED"):
        execute(
            proposal,
            verdict,
            evidence,
            portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_proposal_verdict_mismatch() -> None:
    trade = build_approved_trade()
    # A different, independently-approved verdict for an unrelated proposal.
    other_evidence = build_evidence(last_price=1234.5)
    other_proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=other_evidence,
        analysis_id="other-analysis",
    )
    other_portfolio = build_portfolio()
    other_verdict = review(
        other_proposal,
        other_evidence,
        policy=build_policy(),
        portfolio=other_portfolio,
        now=BASE_TIME,
    )
    with pytest.raises(PaperExecutionError, match="does not match proposal"):
        execute(
            trade.proposal,
            other_verdict,
            trade.evidence,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_verdict_with_tampered_action_or_direction() -> None:
    # Requirement 10b regression: a model_copy-reconstructed verdict whose
    # own action/direction disagree with its (untouched) decision_ref must
    # be caught by execute()'s explicit check -- not merely happen to be
    # caught as a side effect of Position's/Fill's geometry/NONE-rejection
    # validators (which the earlier ad-hoc probe relied on incidentally).
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    tampered = trade.verdict.model_copy(
        update={"action": Action.SELL, "direction": Direction.SHORT}
    )
    # decision_ref itself is untouched, so the decision_fingerprint check
    # alone would not catch this -- confirms the two checks are independent.
    assert tampered.decision_ref.action == Action.BUY
    assert tampered.decision_ref.direction == Direction.LONG

    with pytest.raises(PaperExecutionError, match="does not match verdict.decision_ref"):
        execute(
            trade.proposal,
            tampered,
            trade.evidence,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_verdict_tampered_to_none_direction() -> None:
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
    )
    tampered = trade.verdict.model_copy(update={"action": Action.WAIT, "direction": Direction.NONE})
    with pytest.raises(PaperExecutionError, match="does not match verdict.decision_ref"):
        execute(
            trade.proposal,
            tampered,
            trade.evidence,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_accepts_an_untampered_verdict_where_action_matches_decision_ref() -> None:
    # Positive control: the new check must not false-positive on a
    # legitimately-constructed, untampered verdict.
    trade = build_approved_trade(
        action=Action.SELL,
        direction=Direction.SHORT,
        entry_price=2400.0,
        stop_loss=2450.0,
        take_profit=2300.0,
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
    assert result.portfolio.open_position is not None
    assert result.portfolio.open_position.direction is Direction.SHORT


def test_execute_rejects_evidence_not_linked_to_the_approved_proposal() -> None:
    # H2 regression: review/approve using Evidence A, then supply a
    # different Evidence B (same symbol, materially different last_price)
    # to execute() -- a fresh/different Evidence must never be executable
    # against an old approval, even though nothing about B is individually
    # invalid and its symbol still matches.
    trade = build_approved_trade(entry_price=2400.0)  # reviewed/approved at 2400.0 (Evidence A)
    evidence_b = build_evidence(last_price=2490.0)  # same symbol, different price (Evidence B)
    assert evidence_b.symbol == trade.evidence.symbol
    assert evidence_b.last_price != trade.evidence.last_price

    with pytest.raises(PaperExecutionError, match="does not match the Evidence"):
        execute(
            trade.proposal,
            trade.verdict,
            evidence_b,
            trade.portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )
    # No account mutation occurred: raising means execute() returned
    # nothing and the (frozen, unreachable-to-mutate-in-place) portfolio
    # passed in is untouched.
    assert trade.portfolio.open_position is None
    assert trade.portfolio.cash == trade.portfolio.starting_balance


def test_execute_rejects_a_portfolio_symbol_mismatch() -> None:
    trade = build_approved_trade()
    mismatched_portfolio = build_portfolio(symbol="EURUSD")
    with pytest.raises(PaperExecutionError, match="does not match evidence.symbol"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            mismatched_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


# --- execute(): portfolio_ref integration check (HIGH cross-mission finding fix) ---
#
# A fresh, cross-mission integration audit of the full Evidence -> Reasoning
# -> ProposedDecision -> RiskVerdict -> Paper Execution chain found that
# execute() verified every other RiskVerdict reference (decision_ref,
# evidence_ref transitively via proposal) but never verified portfolio_ref --
# meaning a caller could substitute a completely different, unreviewed
# PortfolioState and still have it execute, silently bypassing every
# portfolio-dependent control review() actually evaluated. Empirically
# confirmed exploitable before the fix (a $5,000,000 account executed
# against a verdict approved for a $100,000 one); this section is the
# regression suite for the fix.


def test_execute_accepts_a_portfolio_that_matches_verdict_portfolio_ref() -> None:
    # Positive control: the fix must not false-positive on the legitimate
    # case -- the exact portfolio review() was called with.
    trade = build_approved_trade(cash=100_000.0)
    assert portfolio_state_fingerprint(trade.portfolio) == trade.verdict.portfolio_ref.content_hash
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    assert result.portfolio.open_position is not None


def test_execute_rejects_a_same_symbol_but_entirely_different_portfolio() -> None:
    # The exact exploit scenario found by the audit: same symbol, no open
    # position, ample cash -- every *other* check would pass -- but this is
    # simply not the account the Risk Guardian reviewed.
    trade = build_approved_trade(cash=100_000.0)
    unrelated_portfolio = build_portfolio(cash=5_000_000.0, starting_balance=5_000_000.0)
    assert unrelated_portfolio.symbol == trade.portfolio.symbol
    assert (
        portfolio_state_fingerprint(unrelated_portfolio) != trade.verdict.portfolio_ref.content_hash
    )
    with pytest.raises(PaperExecutionError, match="does not match verdict.portfolio_ref"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            unrelated_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_portfolio_with_different_cash_or_equity() -> None:
    # A more surgical variant than the wholesale-substitution case above:
    # only cash/equity differ (a plausible "stale reload picked up a
    # different balance" scenario), everything else about the portfolio is
    # identical in shape.
    trade = build_approved_trade(cash=100_000.0)
    different_cash_portfolio = build_portfolio(cash=99_000.0, starting_balance=100_000.0)
    with pytest.raises(PaperExecutionError, match="does not match verdict.portfolio_ref"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            different_cash_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_tampered_portfolio_that_is_otherwise_plausible() -> None:
    # A model_copy-tampered portfolio that changes a field the other checks
    # never look at (equity_peak) -- proves the fix catches arbitrary
    # content drift, not just the fields execute()'s other checks happen to
    # read (symbol, open_position, cash).
    trade = build_approved_trade(cash=100_000.0)
    tampered = trade.portfolio.model_copy(update={"equity_peak": 999_999.0})
    assert tampered.symbol == trade.portfolio.symbol
    assert tampered.open_position is None
    assert tampered.cash == trade.portfolio.cash
    with pytest.raises(PaperExecutionError, match="does not match verdict.portfolio_ref"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            tampered,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_stale_mark_to_market_portfolio() -> None:
    # A "stale" portfolio in the temporal sense: a legitimately-derived
    # mark_to_market() step forward in time from the reviewed snapshot
    # (e.g. a caller that re-marked the account before re-checking whether
    # the verdict it holds is still current) -- still not what was
    # reviewed, must still be rejected.
    trade = build_approved_trade(cash=100_000.0)
    later = mark_to_market(
        trade.portfolio, now=BASE_TIME + timedelta(minutes=30), last_price=trade.evidence.last_price
    )
    with pytest.raises(PaperExecutionError, match="does not match verdict.portfolio_ref"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            later,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_rejected_portfolio_mismatch_causes_zero_state_mutation() -> None:
    # A rejected execute() call must leave every input untouched -- no
    # fabricated Fill, no fabricated Position, and (since PortfolioState is
    # frozen) the caller's own objects provably unchanged after the raise.
    trade = build_approved_trade(cash=100_000.0)
    unrelated_portfolio = build_portfolio(cash=5_000_000.0, starting_balance=5_000_000.0)
    original_snapshot = unrelated_portfolio.model_copy()

    with pytest.raises(PaperExecutionError, match="does not match verdict.portfolio_ref"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            unrelated_portfolio,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )

    assert unrelated_portfolio == original_snapshot
    assert unrelated_portfolio.open_position is None
    assert unrelated_portfolio.cash == 5_000_000.0
    # The legitimately-reviewed portfolio is untouched too -- a rejected
    # call using a *different* object must never leak a side effect back
    # onto the correct one.
    assert trade.portfolio.open_position is None
    assert trade.portfolio.cash == trade.portfolio.starting_balance


def test_execute_rejects_portfolio_mismatch_before_the_open_position_check() -> None:
    # Ordering regression: the fix is the LAST validation gate (after the
    # symbol and open-position checks, so their own more specific messages
    # are preserved for the scenarios they were written for -- see
    # test_execute_rejects_a_portfolio_symbol_mismatch and test_execute_
    # rejects_opening_while_a_position_is_already_open). This test proves a
    # portfolio that passes every *other* check (right symbol, flat,
    # affordable) but is still the wrong account is caught by the new
    # check specifically, not silently accepted because it "looked fine."
    trade = build_approved_trade(cash=100_000.0)
    plausible_but_wrong = build_portfolio(
        symbol=trade.portfolio.symbol, cash=200_000.0, starting_balance=200_000.0
    )
    assert plausible_but_wrong.symbol == trade.evidence.symbol
    assert plausible_but_wrong.open_position is None
    with pytest.raises(PaperExecutionError, match="does not match verdict.portfolio_ref"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            plausible_but_wrong,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_non_positive_quantity() -> None:
    trade = build_approved_trade()
    with pytest.raises(PaperExecutionError, match="quantity must be positive"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            trade.portfolio,
            quantity=0.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_opening_while_a_position_is_already_open() -> None:
    trade = build_approved_trade()
    already_open = trade.portfolio.model_copy(update={"open_position": build_position()})
    with pytest.raises(PaperExecutionError, match="single-position-at-a-time"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            already_open,
            quantity=1.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


def test_execute_rejects_a_quantity_that_exceeds_available_cash() -> None:
    # The Risk Guardian approved this proposal for *some* size; execute()
    # still independently guards cash sufficiency against whatever quantity
    # the caller actually supplies, rather than trusting it blindly.
    trade = build_approved_trade(cash=1_000.0)
    with pytest.raises(PaperExecutionError, match="exceeds available cash"):
        execute(
            trade.proposal,
            trade.verdict,
            trade.evidence,
            trade.portfolio,
            quantity=1_000.0,
            policy=ZERO_COST_POLICY,
            now=BASE_TIME,
        )


# --- resolve_stop_take_profit ---


def test_resolve_with_no_open_position_marks_to_market_and_closes_nothing() -> None:
    portfolio = build_portfolio(open_position=None)
    evidence = build_evidence(last_price=9999.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is None
    assert new_portfolio.open_position is None


def test_resolve_when_neither_level_is_reached_leaves_the_position_open() -> None:
    position = build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2420.0)  # between stop and take-profit
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME
    )
    assert closed is None
    assert new_portfolio.open_position == position
    assert new_portfolio.equity == pytest.approx(90_000.0 + position.unrealized_pnl(2420.0))


def test_resolve_long_position_at_stop_loss_closes_with_a_loss() -> None:
    position = build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2350.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert closed.realized_pnl < 0
    assert new_portfolio.open_position is None


def test_resolve_long_position_at_take_profit_closes_with_a_gain() -> None:
    position = build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2500.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert closed.realized_pnl > 0
    assert new_portfolio.open_position is None


def test_resolve_short_position_at_stop_loss_closes_with_a_loss() -> None:
    position = build_position(
        direction=Direction.SHORT, entry_price=2400.0, stop_loss=2450.0, take_profit=2300.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2450.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert closed.realized_pnl < 0


def test_resolve_short_position_at_take_profit_closes_with_a_gain() -> None:
    position = build_position(
        direction=Direction.SHORT, entry_price=2400.0, stop_loss=2450.0, take_profit=2300.0
    )
    portfolio = build_portfolio(open_position=position, cash=90_000.0)
    evidence = build_evidence(last_price=2300.0)
    new_portfolio, closed = resolve_stop_take_profit(
        portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert closed.realized_pnl > 0


def test_resolve_rejects_a_symbol_mismatch() -> None:
    portfolio = build_portfolio(symbol="EURUSD", open_position=build_position())
    evidence = build_evidence(last_price=2500.0)  # symbol="XAUUSD"
    # "price.symbol", not "evidence.symbol": Mission 9 narrowed this parameter
    # to `ObservedPrice`, and the message names the parameter it actually has.
    with pytest.raises(PaperExecutionError, match="does not match price.symbol"):
        resolve_stop_take_profit(portfolio, evidence, policy=ZERO_COST_POLICY, now=BASE_TIME)


# --- cash conservation across a full open/close round trip ---


def test_cash_conservation_across_open_and_close_long() -> None:
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=10.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2500.0)  # take-profit hit
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    expected_cash = trade.portfolio.cash - opened.fill.fee + closed.realized_pnl
    assert closed_portfolio.cash == pytest.approx(expected_cash)
    assert closed_portfolio.equity == pytest.approx(closed_portfolio.cash)


def test_cash_conservation_across_open_and_close_short() -> None:
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    trade = build_approved_trade(
        action=Action.SELL,
        direction=Direction.SHORT,
        entry_price=2400.0,
        stop_loss=2450.0,
        take_profit=2300.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=10.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2300.0)  # take-profit hit
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    expected_cash = trade.portfolio.cash - opened.fill.fee + closed.realized_pnl
    assert closed_portfolio.cash == pytest.approx(expected_cash)


def test_a_losing_round_trip_still_conserves_cash() -> None:
    policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=10.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2350.0)  # stop-loss hit
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed.realized_pnl < 0
    expected_cash = trade.portfolio.cash - opened.fill.fee + closed.realized_pnl
    assert closed_portfolio.cash == pytest.approx(expected_cash)


# --- full feedback loop: BUY -> APPROVED -> PAPER FILL -> updated PortfolioState -> next review()


def test_full_buy_approve_fill_feedback_loop_blocks_a_second_trade() -> None:
    trade = build_approved_trade(action=Action.BUY, direction=Direction.LONG, entry_price=2400.0)
    result = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=ZERO_COST_POLICY,
        now=BASE_TIME,
    )
    assert result.portfolio.open_position is not None

    # A second BUY proposal, reviewed against the *updated* portfolio from
    # the first fill -- the feedback loop this test exists to prove closes.
    second_evidence = build_evidence(last_price=2400.0)
    second_proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=second_evidence,
        analysis_id="second-analysis",
    )
    second_verdict = review(
        second_proposal,
        second_evidence,
        policy=build_policy(),
        portfolio=result.portfolio,
        now=BASE_TIME,
    )
    assert second_verdict.outcome is RiskOutcome.VETOED
    assert RiskReasonCode.POSITION_ALREADY_OPEN in second_verdict.reason_codes


def test_full_loop_permits_a_new_trade_after_the_position_closes() -> None:
    policy = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)
    trade = build_approved_trade(
        action=Action.BUY,
        direction=Direction.LONG,
        entry_price=2400.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        cash=100_000.0,
    )
    opened = execute(
        trade.proposal,
        trade.verdict,
        trade.evidence,
        trade.portfolio,
        quantity=1.0,
        policy=policy,
        now=BASE_TIME,
    )
    exit_evidence = build_evidence(last_price=2500.0)
    closed_portfolio, closed = resolve_stop_take_profit(
        opened.portfolio, exit_evidence, policy=policy, now=BASE_TIME + timedelta(hours=1)
    )
    assert closed is not None
    assert closed_portfolio.open_position is None

    third_proposal = build_proposal(
        action=Action.BUY,
        direction=Direction.LONG,
        evidence=exit_evidence,
        analysis_id="third-analysis",
    )
    third_verdict = review(
        third_proposal,
        exit_evidence,
        policy=build_policy(),
        portfolio=closed_portfolio,
        now=BASE_TIME + timedelta(hours=1),
    )
    assert third_verdict.outcome is RiskOutcome.APPROVED
