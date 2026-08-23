"""Deterministic paper order/fill/closed-position records.

Every model here is an immutable audit record of a simulated execution
event — never a live order, never routed to a broker. `orion.paper` is the
only layer in ORION permitted to mutate account state; every function that
produces one of these records is still a pure function of its arguments
(see `orion.paper.execution`), and the "mutation" is always "produce a new
`orion.risk.portfolio.PortfolioState`", never an in-place update.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.reasoning.schemas import Direction

_FINGERPRINT_FIELD = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class PaperExecutionError(RuntimeError):
    """Raised when a simulated order cannot be evaluated at all — a
    malformed or mismatched input, never an ordinary market condition.

    Mirrors `orion.risk.guardian.RiskGuardianError`'s "I could not decide"
    vs. "I decided, and the answer is no" distinction one layer further
    down: an order that is not executable because `RiskVerdict.outcome` is
    not APPROVED is a fail-closed `PaperExecutionError`, never a silently
    skipped or fabricated fill.
    """


class Order(BaseModel):
    """A simulated order derived from an APPROVED `RiskVerdict`. Never sent
    anywhere; `orion.paper.execution.execute` consumes it immediately and
    it is not itself journaled — the resulting `Fill` is the audit record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    direction: Direction
    quantity: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    requested_at: datetime
    risk_verdict_fingerprint: str = _FINGERPRINT_FIELD

    @model_validator(mode="after")
    def _check_order_invariants(self) -> Order:
        if self.direction is Direction.NONE:
            raise ValueError("an order cannot have direction=NONE")
        if self.requested_at.tzinfo is None:
            raise ValueError("requested_at must be a timezone-aware datetime")
        return self


class Fill(BaseModel):
    """The deterministic result of simulating an order-open against
    `evidence.last_price` under a `PaperExecutionPolicy`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    direction: Direction
    quantity: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    fee: float = Field(ge=0)
    filled_at: datetime
    risk_verdict_fingerprint: str = _FINGERPRINT_FIELD

    @model_validator(mode="after")
    def _check_fill_invariants(self) -> Fill:
        if self.direction is Direction.NONE:
            raise ValueError("a fill cannot have direction=NONE")
        if self.filled_at.tzinfo is None:
            raise ValueError("filled_at must be a timezone-aware datetime")
        return self


class CloseReason(StrEnum):
    """Why a position was closed. v1 only ever closes mechanically, via
    last-price-only stop/take-profit resolution (Mission 4 architecture
    decision B6) — there is no AI/Risk-Guardian-approved close yet, so
    every `ClosedPosition` in this milestone carries one of these two."""

    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"


class ClosedPosition(BaseModel):
    """An immutable realized-P&L record produced when an open position is
    closed. `realized_pnl` is net of the closing leg's fee only — the
    opening leg's fee already reduced cash directly when the position was
    opened, so it is not double-counted here; see `orion.paper.execution`'s
    cash-conservation derivation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    direction: Direction
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    opened_at: datetime
    closed_at: datetime
    close_reason: CloseReason
    realized_pnl: float
    fee: float = Field(ge=0)
    opened_by_verdict_fingerprint: str = _FINGERPRINT_FIELD

    @model_validator(mode="after")
    def _check_closed_position_invariants(self) -> ClosedPosition:
        if self.direction is Direction.NONE:
            raise ValueError("a closed position cannot have direction=NONE")
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be a timezone-aware datetime")
        if self.closed_at.tzinfo is None:
            raise ValueError("closed_at must be a timezone-aware datetime")
        if self.closed_at < self.opened_at:
            raise ValueError("closed_at cannot be before opened_at")
        return self
