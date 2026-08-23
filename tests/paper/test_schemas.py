from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from orion.paper.schemas import ClosedPosition, CloseReason, Fill, Order
from orion.reasoning.schemas import Direction
from tests.paper.conftest import BASE_TIME

_FINGERPRINT = "a" * 64


def test_order_rejects_none_direction() -> None:
    with pytest.raises(ValidationError):
        Order(
            symbol="XAUUSD",
            direction=Direction.NONE,
            quantity=1.0,
            stop_loss=2350.0,
            take_profit=2500.0,
            requested_at=BASE_TIME,
            risk_verdict_fingerprint=_FINGERPRINT,
        )


def test_order_rejects_naive_requested_at() -> None:
    with pytest.raises(ValidationError):
        Order(
            symbol="XAUUSD",
            direction=Direction.LONG,
            quantity=1.0,
            stop_loss=2350.0,
            take_profit=2500.0,
            requested_at=BASE_TIME.replace(tzinfo=None),
            risk_verdict_fingerprint=_FINGERPRINT,
        )


def test_order_is_frozen() -> None:
    order = Order(
        symbol="XAUUSD",
        direction=Direction.LONG,
        quantity=1.0,
        stop_loss=2350.0,
        take_profit=2500.0,
        requested_at=BASE_TIME,
        risk_verdict_fingerprint=_FINGERPRINT,
    )
    with pytest.raises(ValidationError):
        order.quantity = 2.0  # type: ignore[misc]


def test_fill_rejects_none_direction() -> None:
    with pytest.raises(ValidationError):
        Fill(
            symbol="XAUUSD",
            direction=Direction.NONE,
            quantity=1.0,
            fill_price=2400.0,
            fee=1.0,
            filled_at=BASE_TIME,
            risk_verdict_fingerprint=_FINGERPRINT,
        )


def test_fill_rejects_naive_filled_at() -> None:
    with pytest.raises(ValidationError):
        Fill(
            symbol="XAUUSD",
            direction=Direction.LONG,
            quantity=1.0,
            fill_price=2400.0,
            fee=1.0,
            filled_at=BASE_TIME.replace(tzinfo=None),
            risk_verdict_fingerprint=_FINGERPRINT,
        )


def test_closed_position_rejects_none_direction() -> None:
    with pytest.raises(ValidationError):
        ClosedPosition(
            symbol="XAUUSD",
            direction=Direction.NONE,
            quantity=1.0,
            entry_price=2400.0,
            exit_price=2350.0,
            opened_at=BASE_TIME,
            closed_at=BASE_TIME + timedelta(hours=1),
            close_reason=CloseReason.STOP_LOSS_HIT,
            realized_pnl=-50.0,
            fee=1.0,
            opened_by_verdict_fingerprint=_FINGERPRINT,
        )


def test_closed_position_rejects_closed_before_opened() -> None:
    with pytest.raises(ValidationError):
        ClosedPosition(
            symbol="XAUUSD",
            direction=Direction.LONG,
            quantity=1.0,
            entry_price=2400.0,
            exit_price=2350.0,
            opened_at=BASE_TIME,
            closed_at=BASE_TIME - timedelta(hours=1),
            close_reason=CloseReason.STOP_LOSS_HIT,
            realized_pnl=-50.0,
            fee=1.0,
            opened_by_verdict_fingerprint=_FINGERPRINT,
        )


def test_closed_position_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        ClosedPosition(
            symbol="XAUUSD",
            direction=Direction.LONG,
            quantity=1.0,
            entry_price=2400.0,
            exit_price=2500.0,
            opened_at=BASE_TIME.replace(tzinfo=None),
            closed_at=BASE_TIME + timedelta(hours=1),
            close_reason=CloseReason.TAKE_PROFIT_HIT,
            realized_pnl=100.0,
            fee=1.0,
            opened_by_verdict_fingerprint=_FINGERPRINT,
        )


def test_closed_position_accepts_a_valid_record() -> None:
    closed = ClosedPosition(
        symbol="XAUUSD",
        direction=Direction.LONG,
        quantity=1.0,
        entry_price=2400.0,
        exit_price=2500.0,
        opened_at=BASE_TIME,
        closed_at=BASE_TIME + timedelta(hours=1),
        close_reason=CloseReason.TAKE_PROFIT_HIT,
        realized_pnl=99.0,
        fee=1.0,
        opened_by_verdict_fingerprint=_FINGERPRINT,
    )
    assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
