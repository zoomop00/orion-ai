"""Portfolio/account state the Risk Guardian reads as context, and the
deterministic pure functions that derive it.

`Position`/`PortfolioState` are read-only input context for `orion.risk` —
the same status `RiskPolicy` already has. They live here, in `orion.risk`,
not in `orion.paper` (the execution package), so `orion.risk` never needs
to import from an execution/persistence layer: `orion.paper` depends on
`orion.risk.portfolio` to build the snapshot it hands to `review()`, never
the other way around. This is the Mission 4 architecture decision recorded
in `docs/DECISIONS.md`.

Every function in this module is pure: no network, filesystem, randomness,
or internal clock. `orion.paper` is the only layer in ORION permitted to
mutate account state over time — "mutating" here always means "produce a
new `PortfolioState`", never an in-place update of an existing one.

ORION trades exactly one symbol and holds at most one open position at a
time (Mission 4 architecture decision B2 — no scaling, averaging,
pyramiding, or multi-position portfolio model). Cash-covered only: no
leverage, margin, borrowing, or liquidation model (B4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.provenance import canonical_hash
from orion.reasoning.schemas import Direction


class Position(BaseModel):
    """One open paper position.

    Frozen: closing or modifying a position produces a new `PortfolioState`
    with a different (or absent) `open_position`, never a mutation of this
    object in place.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: Direction
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    opened_at: datetime
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    opened_by_verdict_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def _check_position_invariants(self) -> Position:
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be a timezone-aware datetime")
        if self.direction is Direction.NONE:
            raise ValueError("an open position cannot have direction=NONE")
        # Restated defense-in-depth, same rationale as orion.risk.controls'
        # classify_trade_levels: a Position reconstructed from a future
        # persisted/relaxed source must not rely on the Risk Guardian's own
        # diligence at the moment it was opened.
        if self.stop_loss == self.entry_price:
            raise ValueError("stop_loss cannot equal entry_price (zero stop distance)")
        if self.direction is Direction.LONG and not (
            self.stop_loss < self.entry_price < self.take_profit
        ):
            raise ValueError(
                f"LONG position levels incoherent: stop_loss={self.stop_loss}, "
                f"entry_price={self.entry_price}, take_profit={self.take_profit}"
            )
        if self.direction is Direction.SHORT and not (
            self.take_profit < self.entry_price < self.stop_loss
        ):
            raise ValueError(
                f"SHORT position levels incoherent: take_profit={self.take_profit}, "
                f"entry_price={self.entry_price}, stop_loss={self.stop_loss}"
            )
        return self

    def unrealized_pnl(self, last_price: float) -> float:
        """Mark this position to `last_price`. Pure; reads no state beyond
        its own fields and the given price."""
        sign = 1.0 if self.direction is Direction.LONG else -1.0
        return (last_price - self.entry_price) * self.quantity * sign


class PortfolioState(BaseModel):
    """A read-only snapshot of the paper account, as of `as_of`.

    `equity` is a plain frozen field — this model alone never carries a
    market price, so its own constructor cannot validate `equity` against
    `cash`/`open_position` the way `mark_to_market` computes it correctly.
    That does not make `equity` trusted caller-supplied truth:
    `orion.risk.guardian.review()` independently re-verifies it (via
    `expected_equity(cash, open_position, evidence.last_price)`, raising
    `PortfolioIntegrityError` on any mismatch) before any portfolio-
    dependent control ever reads it. `equity_peak`/`daily_opening_equity`/
    `weekly_opening_equity` are explicit fields (Mission 4 architecture
    decision B3: "the baseline must be explicit portfolio state, not
    guessed or reconstructed from arbitrary fields"), rolled forward only
    by `mark_to_market`, never recomputed ad hoc by a Risk Guardian control.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    as_of: datetime
    cash: float = Field(ge=0)
    equity: float = Field(ge=0)
    starting_balance: float = Field(gt=0)
    open_position: Position | None = None
    equity_peak: float = Field(gt=0)
    daily_period_start: datetime
    daily_opening_equity: float = Field(gt=0)
    weekly_period_start: datetime
    weekly_opening_equity: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_portfolio_invariants(self) -> PortfolioState:
        for name, value in (
            ("as_of", self.as_of),
            ("daily_period_start", self.daily_period_start),
            ("weekly_period_start", self.weekly_period_start),
        ):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        if self.equity > self.equity_peak:
            raise ValueError(
                f"equity ({self.equity}) exceeds equity_peak ({self.equity_peak}); "
                "equity_peak must be the monotonic high-water mark"
            )
        if self.daily_period_start > self.as_of:
            raise ValueError("daily_period_start cannot be after as_of")
        if self.weekly_period_start > self.as_of:
            raise ValueError("weekly_period_start cannot be after as_of")
        if self.weekly_period_start > self.daily_period_start:
            raise ValueError("weekly_period_start cannot be after daily_period_start")
        return self


def expected_equity(cash: float, open_position: Position | None, last_price: float) -> float:
    """The equity a self-consistent `PortfolioState` must report: `cash`
    plus the open position's unrealized P&L at `last_price` (zero if flat).

    The single formula `mark_to_market` uses to produce `equity`, and
    `orion.risk.guardian.review` uses to verify a caller-supplied
    `PortfolioState` was not fabricated — the same "one shared function,
    never two independent implementations" principle this codebase already
    applies to provenance hashing (`orion.provenance.canonical_hash`) and
    trade-level geometry (`orion.risk.controls.classify_trade_levels`).
    Sharing this exact computation is what makes `review()`'s equity check
    and `mark_to_market`'s own equity computation structurally unable to
    drift apart, rather than two hand-written formulas that happen to
    agree today.
    """
    unrealized = 0.0 if open_position is None else open_position.unrealized_pnl(last_price)
    return cash + unrealized


def _utc_day_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _utc_week_start(moment: datetime) -> datetime:
    day_start = _utc_day_start(moment)
    return day_start - timedelta(days=day_start.weekday())


def initial_portfolio_state(
    *, symbol: str, starting_balance: float, now: datetime
) -> PortfolioState:
    """The flat (no position) account at inception, at `now`.

    The only place a `PortfolioState` is minted from nothing rather than
    derived from a prior one via `mark_to_market`.
    """
    return PortfolioState(
        symbol=symbol,
        as_of=now,
        cash=starting_balance,
        equity=starting_balance,
        starting_balance=starting_balance,
        open_position=None,
        equity_peak=starting_balance,
        daily_period_start=_utc_day_start(now),
        daily_opening_equity=starting_balance,
        weekly_period_start=_utc_week_start(now),
        weekly_opening_equity=starting_balance,
    )


def mark_to_market(state: PortfolioState, *, now: datetime, last_price: float) -> PortfolioState:
    """Produce a fresh `PortfolioState` as of `now`, re-marking equity to
    `last_price` and rolling `equity_peak`/daily/weekly baselines forward.

    Pure function: `now` and `last_price` are the only external inputs, and
    identical arguments always produce an identical result. Daily/weekly
    baselines advance in whole-period jumps against UTC boundaries — if
    `now` lands in a later UTC day/week than `state`'s current period, the
    baseline resets to the newly-marked equity at the moment this function
    first observes the new period. ORION only marks state at analysis-cycle
    granularity (not continuously), so this is a documented, deterministic
    convention, not an attempt at continuous intraday baseline tracking.

    Raises ValueError if `now` is before `state.as_of` — this function only
    ever advances a `PortfolioState` forward in time.
    """
    if now < state.as_of:
        raise ValueError(
            f"now ({now.isoformat()}) is before state.as_of ({state.as_of.isoformat()}); "
            "mark_to_market cannot move a PortfolioState backwards in time"
        )
    equity = expected_equity(state.cash, state.open_position, last_price)
    equity_peak = max(state.equity_peak, equity)

    day_start = _utc_day_start(now)
    if day_start > state.daily_period_start:
        daily_period_start, daily_opening_equity = day_start, equity
    else:
        daily_period_start = state.daily_period_start
        daily_opening_equity = state.daily_opening_equity

    week_start = _utc_week_start(now)
    if week_start > state.weekly_period_start:
        weekly_period_start, weekly_opening_equity = week_start, equity
    else:
        weekly_period_start = state.weekly_period_start
        weekly_opening_equity = state.weekly_opening_equity

    return PortfolioState(
        symbol=state.symbol,
        as_of=now,
        cash=state.cash,
        equity=equity,
        starting_balance=state.starting_balance,
        open_position=state.open_position,
        equity_peak=equity_peak,
        daily_period_start=daily_period_start,
        daily_opening_equity=daily_opening_equity,
        weekly_period_start=weekly_period_start,
        weekly_opening_equity=weekly_opening_equity,
    )


def compute_position_size(
    equity: float, entry_price: float, stop_loss: float, *, risk_per_trade_fraction: float
) -> float:
    """Deterministic risk-based position size: the quantity such that a
    stop-out loses exactly `risk_per_trade_fraction * equity` (never more).

    Pure function of its arguments — never AI-proposed (see
    `docs/DECISIONS.md`, Mission 4 requirement 6). Returns `0.0` (never
    raises, never divides by zero) if `equity <= 0` or `stop_loss ==
    entry_price`: callers are expected to have already rejected a
    degenerate stop via `orion.risk.controls.classify_trade_levels` before
    reaching this, and returning `0.0` here is a second, independent line
    of defense, not the primary one.
    """
    stop_distance = abs(entry_price - stop_loss)
    if equity <= 0 or stop_distance <= 0:
        return 0.0
    risk_amount = equity * risk_per_trade_fraction
    return risk_amount / stop_distance


def portfolio_state_fingerprint(state: PortfolioState) -> str:
    """A deterministic fingerprint of `state`'s full content.

    Thin, domain-named wrapper around `orion.provenance.canonical_hash` —
    mirrors `orion.analysis.evidence.evidence_fingerprint`, `orion.decision.
    schemas.decision_fingerprint`, and `orion.risk.schemas.
    risk_verdict_fingerprint` exactly, per this codebase's rule that
    provenance/identity hashing lives in one shared mechanism, never two
    independent implementations that might silently drift apart.

    `orion.paper.store.PortfolioStore.save()` uses this as the
    compare-and-swap token that proves a write is based on the store's
    actual current snapshot, not a stale one — the mechanism that closes
    the Mission 4 replay/lost-update gap recorded in `docs/DECISIONS.md`.
    A fingerprint (not a monotonic version counter) was chosen deliberately:
    it requires no new field on `PortfolioState`, no bookkeeping in every
    state-producing function to keep a counter correctly incremented, and
    it is the same "one shared function" idiom every other identity check
    in this codebase already uses — a future durable `PortfolioStore`
    (Mission 2) can implement the same contract atomically with an ordinary
    `UPDATE ... WHERE fingerprint = ?` (or equivalent transactional
    compare-and-swap), no schema migration to add a version column needed.
    """
    return canonical_hash(state)
