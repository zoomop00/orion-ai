from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from orion.reasoning.schemas import Direction
from orion.risk.portfolio import (
    PortfolioState,
    Position,
    compute_position_size,
    expected_equity,
    initial_portfolio_state,
    mark_to_market,
    portfolio_state_fingerprint,
)

_FINGERPRINT = "a" * 64
BASE_TIME = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)  # a Sunday


def _position(**overrides: object) -> Position:
    defaults: dict[str, object] = {
        "direction": Direction.LONG,
        "quantity": 1.0,
        "entry_price": 2400.0,
        "opened_at": BASE_TIME,
        "stop_loss": 2350.0,
        "take_profit": 2500.0,
        "opened_by_verdict_fingerprint": _FINGERPRINT,
    }
    defaults.update(overrides)
    return Position(**defaults)


# --- Position ---


def test_valid_long_position_constructs() -> None:
    position = _position(direction=Direction.LONG)
    assert position.direction is Direction.LONG


def test_valid_short_position_constructs() -> None:
    position = _position(direction=Direction.SHORT, stop_loss=2450.0, take_profit=2300.0)
    assert position.direction is Direction.SHORT


def test_position_rejects_none_direction() -> None:
    with pytest.raises(ValidationError, match="direction=NONE"):
        _position(direction=Direction.NONE)


def test_position_rejects_naive_opened_at() -> None:
    with pytest.raises(ValidationError):
        _position(opened_at=BASE_TIME.replace(tzinfo=None))


def test_position_rejects_zero_stop_distance() -> None:
    with pytest.raises(ValidationError, match="zero stop distance"):
        _position(stop_loss=2400.0)


def test_position_rejects_incoherent_long_geometry() -> None:
    with pytest.raises(ValidationError, match="LONG position levels incoherent"):
        _position(direction=Direction.LONG, stop_loss=2410.0, take_profit=2500.0)


def test_position_rejects_incoherent_short_geometry() -> None:
    with pytest.raises(ValidationError, match="SHORT position levels incoherent"):
        _position(direction=Direction.SHORT, stop_loss=2390.0, take_profit=2300.0)


def test_long_unrealized_pnl_is_positive_when_price_rises() -> None:
    position = _position(direction=Direction.LONG, entry_price=2400.0, quantity=2.0)
    assert position.unrealized_pnl(2450.0) == pytest.approx(100.0)


def test_long_unrealized_pnl_is_negative_when_price_falls() -> None:
    position = _position(direction=Direction.LONG, entry_price=2400.0, quantity=2.0)
    assert position.unrealized_pnl(2350.0) == pytest.approx(-100.0)


def test_short_unrealized_pnl_is_positive_when_price_falls() -> None:
    position = _position(
        direction=Direction.SHORT,
        entry_price=2400.0,
        quantity=2.0,
        stop_loss=2450.0,
        take_profit=2300.0,
    )
    assert position.unrealized_pnl(2350.0) == pytest.approx(100.0)


def test_short_unrealized_pnl_is_negative_when_price_rises() -> None:
    position = _position(
        direction=Direction.SHORT,
        entry_price=2400.0,
        quantity=2.0,
        stop_loss=2450.0,
        take_profit=2300.0,
    )
    assert position.unrealized_pnl(2450.0) == pytest.approx(-100.0)


def test_unrealized_pnl_is_zero_at_entry_price() -> None:
    position = _position(entry_price=2400.0)
    assert position.unrealized_pnl(2400.0) == pytest.approx(0.0)


# --- PortfolioState ---


def _portfolio(**overrides: object) -> PortfolioState:
    defaults: dict[str, object] = {
        "symbol": "XAUUSD",
        "as_of": BASE_TIME,
        "cash": 100_000.0,
        "equity": 100_000.0,
        "starting_balance": 100_000.0,
        "open_position": None,
        "equity_peak": 100_000.0,
        "daily_period_start": BASE_TIME.replace(hour=0, minute=0, second=0, microsecond=0),
        "daily_opening_equity": 100_000.0,
        "weekly_period_start": BASE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=BASE_TIME.weekday()),
        "weekly_opening_equity": 100_000.0,
    }
    defaults.update(overrides)
    return PortfolioState(**defaults)


def test_valid_flat_portfolio_constructs() -> None:
    portfolio = _portfolio()
    assert portfolio.open_position is None


def test_portfolio_rejects_naive_as_of() -> None:
    with pytest.raises(ValidationError):
        _portfolio(as_of=BASE_TIME.replace(tzinfo=None))


def test_portfolio_rejects_equity_exceeding_equity_peak() -> None:
    with pytest.raises(ValidationError, match="equity_peak must be the monotonic high-water mark"):
        _portfolio(equity=200_000.0, equity_peak=100_000.0)


def test_portfolio_rejects_daily_period_start_after_as_of() -> None:
    with pytest.raises(ValidationError, match="daily_period_start cannot be after as_of"):
        _portfolio(daily_period_start=BASE_TIME + timedelta(days=1))


def test_portfolio_rejects_weekly_period_start_after_as_of() -> None:
    with pytest.raises(ValidationError, match="weekly_period_start cannot be after as_of"):
        _portfolio(weekly_period_start=BASE_TIME + timedelta(days=1))


def test_portfolio_rejects_weekly_period_start_after_daily_period_start() -> None:
    day_start = BASE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
    # as_of pushed out far enough that neither period_start individually
    # exceeds it -- isolating the daily/weekly-relative-order check from
    # the as_of-relative checks above it.
    with pytest.raises(
        ValidationError, match="weekly_period_start cannot be after daily_period_start"
    ):
        _portfolio(
            as_of=day_start + timedelta(days=2),
            daily_period_start=day_start,
            weekly_period_start=day_start + timedelta(days=1),
        )


# --- initial_portfolio_state ---


def test_initial_portfolio_state_is_flat() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=50_000.0, now=BASE_TIME)
    assert portfolio.cash == 50_000.0
    assert portfolio.equity == 50_000.0
    assert portfolio.equity_peak == 50_000.0
    assert portfolio.daily_opening_equity == 50_000.0
    assert portfolio.weekly_opening_equity == 50_000.0
    assert portfolio.open_position is None


def test_initial_portfolio_state_computes_utc_day_and_week_start() -> None:
    # 2026-08-23 is a Sunday; the UTC week starts Monday 2026-08-17.
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=1000.0, now=BASE_TIME)
    assert portfolio.daily_period_start == datetime(2026, 8, 23, 0, 0, tzinfo=UTC)
    assert portfolio.weekly_period_start == datetime(2026, 8, 17, 0, 0, tzinfo=UTC)


# --- mark_to_market ---


def test_mark_to_market_with_no_position_equity_equals_cash() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    marked = mark_to_market(portfolio, now=BASE_TIME + timedelta(hours=1), last_price=9999.0)
    assert marked.equity == marked.cash == 100_000.0


def test_mark_to_market_with_long_position_reflects_unrealized_gain() -> None:
    portfolio = _portfolio(cash=90_000.0, equity=90_000.0, open_position=_position(quantity=1.0))
    marked = mark_to_market(portfolio, now=BASE_TIME + timedelta(hours=1), last_price=2450.0)
    assert marked.equity == pytest.approx(90_000.0 + 50.0)


def test_mark_to_market_updates_equity_peak_upward() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    portfolio = portfolio.model_copy(update={"cash": 110_000.0, "equity": 100_000.0})
    marked = mark_to_market(portfolio, now=BASE_TIME + timedelta(hours=1), last_price=0.0)
    assert marked.equity_peak == 110_000.0


def test_mark_to_market_never_lowers_equity_peak() -> None:
    portfolio = _portfolio(cash=80_000.0, equity=80_000.0, equity_peak=100_000.0)
    marked = mark_to_market(portfolio, now=BASE_TIME + timedelta(hours=1), last_price=0.0)
    assert marked.equity_peak == 100_000.0


def test_mark_to_market_within_the_same_utc_day_keeps_the_daily_baseline() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    later_same_day = BASE_TIME + timedelta(hours=2)
    marked = mark_to_market(
        portfolio.model_copy(update={"cash": 90_000.0, "equity": 90_000.0}),
        now=later_same_day,
        last_price=0.0,
    )
    assert marked.daily_period_start == portfolio.daily_period_start
    assert marked.daily_opening_equity == portfolio.daily_opening_equity


def test_mark_to_market_crossing_a_utc_day_boundary_resets_the_daily_baseline() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    next_day = BASE_TIME + timedelta(days=1)
    marked = mark_to_market(
        portfolio.model_copy(update={"cash": 90_000.0, "equity": 90_000.0}),
        now=next_day,
        last_price=0.0,
    )
    assert marked.daily_period_start == datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    assert marked.daily_opening_equity == 90_000.0


def test_mark_to_market_crossing_a_utc_week_boundary_resets_the_weekly_baseline() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    next_monday = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    marked = mark_to_market(
        portfolio.model_copy(update={"cash": 90_000.0, "equity": 90_000.0}),
        now=next_monday,
        last_price=0.0,
    )
    assert marked.weekly_period_start == datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    assert marked.weekly_opening_equity == 90_000.0


def test_mark_to_market_rejects_moving_backwards_in_time() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    with pytest.raises(ValueError, match="cannot move a PortfolioState backwards"):
        mark_to_market(portfolio, now=BASE_TIME - timedelta(hours=1), last_price=100.0)


def test_mark_to_market_is_deterministic() -> None:
    portfolio = _portfolio(cash=90_000.0, equity=90_000.0, open_position=_position())
    later = BASE_TIME + timedelta(hours=3)
    marked_1 = mark_to_market(portfolio, now=later, last_price=2420.0)
    marked_2 = mark_to_market(portfolio, now=later, last_price=2420.0)
    assert marked_1 == marked_2


def test_mark_to_market_equity_always_matches_expected_equity() -> None:
    # mark_to_market's own output must always satisfy the exact invariant
    # orion.risk.guardian.review independently re-checks.
    portfolio = _portfolio(cash=90_000.0, equity=90_000.0, open_position=_position())
    marked = mark_to_market(portfolio, now=BASE_TIME + timedelta(hours=1), last_price=2430.0)
    assert marked.equity == expected_equity(marked.cash, marked.open_position, 2430.0)


# --- expected_equity ---


def test_expected_equity_flat_account_equals_cash() -> None:
    assert expected_equity(50_000.0, None, 9999.0) == 50_000.0


def test_expected_equity_with_long_position_adds_unrealized_gain() -> None:
    position = _position(direction=Direction.LONG, entry_price=2400.0, quantity=2.0)
    assert expected_equity(90_000.0, position, 2450.0) == pytest.approx(90_000.0 + 100.0)


def test_expected_equity_with_short_position_adds_unrealized_gain() -> None:
    position = _position(
        direction=Direction.SHORT,
        entry_price=2400.0,
        quantity=2.0,
        stop_loss=2450.0,
        take_profit=2300.0,
    )
    assert expected_equity(90_000.0, position, 2350.0) == pytest.approx(90_000.0 + 100.0)


def test_expected_equity_at_entry_price_equals_cash() -> None:
    position = _position(entry_price=2400.0)
    assert expected_equity(90_000.0, position, 2400.0) == 90_000.0


# --- compute_position_size ---


def test_compute_position_size_typical_case() -> None:
    # risk_amount = 100000*0.01 = 1000; stop_distance = 50; size = 20.
    size = compute_position_size(100_000.0, 2400.0, 2350.0, risk_per_trade_fraction=0.01)
    assert size == pytest.approx(20.0)


def test_compute_position_size_scales_linearly_with_equity() -> None:
    small = compute_position_size(10_000.0, 2400.0, 2350.0, risk_per_trade_fraction=0.01)
    large = compute_position_size(100_000.0, 2400.0, 2350.0, risk_per_trade_fraction=0.01)
    assert large == pytest.approx(small * 10.0)


def test_compute_position_size_zero_when_equity_non_positive() -> None:
    assert compute_position_size(0.0, 2400.0, 2350.0, risk_per_trade_fraction=0.01) == 0.0
    assert compute_position_size(-100.0, 2400.0, 2350.0, risk_per_trade_fraction=0.01) == 0.0


def test_compute_position_size_zero_when_stop_equals_entry_never_divides_by_zero() -> None:
    assert compute_position_size(100_000.0, 2400.0, 2400.0, risk_per_trade_fraction=0.01) == 0.0


# --- portfolio_state_fingerprint ---


def test_portfolio_state_fingerprint_is_reproducible() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    assert portfolio_state_fingerprint(portfolio) == portfolio_state_fingerprint(portfolio)


def test_portfolio_state_fingerprint_matches_independent_recomputation() -> None:
    portfolio = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    payload = json.dumps(portfolio.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    manual = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert portfolio_state_fingerprint(portfolio) == manual


def test_portfolio_state_fingerprint_changes_when_cash_changes() -> None:
    a = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_000.0, now=BASE_TIME)
    b = initial_portfolio_state(symbol="XAUUSD", starting_balance=100_001.0, now=BASE_TIME)
    assert portfolio_state_fingerprint(a) != portfolio_state_fingerprint(b)


def test_portfolio_state_fingerprint_changes_when_open_position_changes() -> None:
    flat = _portfolio(open_position=None)
    with_position = _portfolio(open_position=_position())
    assert portfolio_state_fingerprint(flat) != portfolio_state_fingerprint(with_position)


def test_portfolio_state_fingerprint_changes_when_only_as_of_differs() -> None:
    # Sanity check on the hash mechanism itself: two genuinely different
    # `as_of` timestamps must still produce two different fingerprints
    # (the timestamp IS part of the hashed content) -- guards against a
    # canonicalization bug that accidentally drops a field.
    a = _portfolio(as_of=BASE_TIME)
    b = _portfolio(as_of=BASE_TIME + timedelta(microseconds=1))
    assert portfolio_state_fingerprint(a) != portfolio_state_fingerprint(b)
