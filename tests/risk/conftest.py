"""Shared ProposedDecision / RiskPolicy / PortfolioState builders for Risk
Guardian tests.

Reuses `tests.decision.conftest`'s `build_evidence`/`build_reasoning` rather
than duplicating them — both test suites need the identical domain objects,
and `build_proposal` below drives them through the real `orion.decision.
engine.decide()` rather than hand-constructing a `ProposedDecision`, so a
test fixture always reflects what the actual pipeline would produce.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.evidence import Evidence
from orion.analysis.structure import VolatilityLevel
from orion.decision.engine import decide
from orion.decision.schemas import ProposedDecision
from orion.reasoning.schemas import Action, Direction, ReasonCode, RiskLevel
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import PortfolioState, Position, initial_portfolio_state
from tests.decision.conftest import (
    BASE_TIME,
    DEFAULT_REQUIRED_BARS,
    build_evidence,
    build_reasoning,
)

__all__ = [
    "BASE_TIME",
    "DEFAULT_REQUIRED_BARS",
    "DUMMY_VERDICT_FINGERPRINT",
    "build_evidence",
    "build_reasoning",
    "build_proposal",
    "build_policy",
    "build_portfolio",
    "build_position",
]

DUMMY_VERDICT_FINGERPRINT = "a" * 64


def build_proposal(
    *,
    action: Action = Action.WAIT,
    direction: Direction = Direction.NONE,
    evidence: Evidence | None = None,
    decided_at: datetime = BASE_TIME,
    analysis_id: str = "test-analysis-1",
    stop_loss: float | None = None,
    take_profit: float | None = None,
    confidence: float = 0.6,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    insufficient_data: bool = False,
    reason_codes: list[ReasonCode] | None = None,
) -> ProposedDecision:
    """Build a `ProposedDecision` via the real `decide()`, not by hand.

    `evidence` defaults to `build_evidence()`'s own defaults (good trend,
    enough bars, RSI/ATR present), so a BUY/SELL `action` passes through the
    Decision Engine unchanged unless the test deliberately makes the evidence
    thin/unknown — exactly like the real pipeline.

    When `stop_loss`/`take_profit` are both left unspecified for a BUY/SELL,
    they default to a 2%-of-price offset (RR=2.0) — wider than `tests.
    decision.conftest.build_reasoning`'s own 2385.0/2430.0 default (a 15
    point, ~0.6% offset around a 2400.0 price). That narrower geometry is
    fine for Decision-Engine-layer tests, which never check affordability
    against a cash-covered account, but it is *unaffordable* here: at
    `RiskPolicy`'s default 1% max_risk_per_trade_fraction, a 0.6%-away stop
    implies a position notional of ~160% of equity (`orion.risk.portfolio.
    compute_position_size`'s notional/equity ratio is `risk_fraction *
    entry/stop_distance`, independent of account size), which
    `orion.risk.controls.position_size_within_limits` correctly vetoes as
    exceeding available cash under a no-leverage policy. A wider default
    here keeps every existing "this trade should be APPROVED" test
    affordable without weakening the real 1% default anywhere.
    """
    ev = evidence if evidence is not None else build_evidence()
    if action in (Action.BUY, Action.SELL) and stop_loss is None and take_profit is None:
        offset = ev.last_price * 0.02
        if direction is Direction.LONG:
            stop_loss, take_profit = ev.last_price - offset, ev.last_price + 2 * offset
        elif direction is Direction.SHORT:
            stop_loss, take_profit = ev.last_price + offset, ev.last_price - 2 * offset
    reasoning = build_reasoning(
        action=action,
        direction=direction,
        evidence=ev,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=confidence,
        risk_level=risk_level,
        insufficient_data=insufficient_data,
        reason_codes=reason_codes,
    )
    return decide(ev, reasoning, analysis_id=analysis_id, now=decided_at)


def build_policy(
    *,
    kill_switch: KillSwitchState = KillSwitchState.DISENGAGED,
    max_staleness_bars: float = 3.0,
    max_decision_age_bars: float = 1.0,
    min_risk_reward: float = 1.0,
    blocked_volatility_levels: tuple[VolatilityLevel, ...] = (VolatilityLevel.UNKNOWN,),
    max_risk_per_trade_fraction: float = 0.01,
    max_exposure_fraction: float = 1.0,
    max_daily_loss_fraction: float = 0.03,
    max_weekly_loss_fraction: float = 0.08,
    max_drawdown_fraction: float = 0.15,
    max_portfolio_staleness_bars: float = 3.0,
) -> RiskPolicy:
    """Kill switch defaults to DISENGAGED here (unlike `RiskPolicy`'s own
    fail-safe ENGAGED default) so most tests can focus on the control under
    test without every one of them needing to explicitly disengage it — the
    fail-safe-default behavior itself is tested directly against a bare
    `RiskPolicy()`, not through this builder. Every other default here
    matches `RiskPolicy`'s own real default exactly (see the
    `blocked_volatility_levels` precedent this docstring inherited: a
    builder default that silently disagreed with the real default would
    make every test that omits an argument exercise a policy no operator
    would actually run).
    """
    return RiskPolicy(
        kill_switch=kill_switch,
        max_staleness_bars=max_staleness_bars,
        max_decision_age_bars=max_decision_age_bars,
        min_risk_reward=min_risk_reward,
        blocked_volatility_levels=blocked_volatility_levels,
        max_risk_per_trade_fraction=max_risk_per_trade_fraction,
        max_exposure_fraction=max_exposure_fraction,
        max_daily_loss_fraction=max_daily_loss_fraction,
        max_weekly_loss_fraction=max_weekly_loss_fraction,
        max_drawdown_fraction=max_drawdown_fraction,
        max_portfolio_staleness_bars=max_portfolio_staleness_bars,
    )


def build_portfolio(
    *,
    symbol: str = "XAUUSD",
    as_of: datetime = BASE_TIME,
    cash: float = 100_000.0,
    equity: float | None = None,
    starting_balance: float = 100_000.0,
    open_position: Position | None = None,
    equity_peak: float | None = None,
    daily_period_start: datetime | None = None,
    daily_opening_equity: float | None = None,
    weekly_period_start: datetime | None = None,
    weekly_opening_equity: float | None = None,
) -> PortfolioState:
    """A flat (no open position), well-formed `PortfolioState` by default,
    at `BASE_TIME`, with $100,000 starting cash — comfortably large
    relative to `build_evidence()`'s $2,400 default XAUUSD price and
    `build_proposal`'s default trade geometry, so a default-sized trade's
    notional/risk-per-trade tests aren't fighting an undersized account.
    Every field can be overridden independently to build a specific control
    scenario (an already-open position, a drawn-down account, a breached
    daily-loss baseline, etc.); `daily_period_start`/`weekly_period_start`
    default to the UTC day/week `as_of` falls in, matching what
    `orion.risk.portfolio.initial_portfolio_state` would compute.
    """
    base = initial_portfolio_state(symbol=symbol, starting_balance=starting_balance, now=as_of)
    resolved_equity = equity if equity is not None else cash
    return PortfolioState(
        symbol=symbol,
        as_of=as_of,
        cash=cash,
        equity=resolved_equity,
        starting_balance=starting_balance,
        open_position=open_position,
        equity_peak=equity_peak
        if equity_peak is not None
        else max(starting_balance, resolved_equity),
        daily_period_start=(
            daily_period_start if daily_period_start is not None else base.daily_period_start
        ),
        daily_opening_equity=(
            daily_opening_equity if daily_opening_equity is not None else starting_balance
        ),
        weekly_period_start=(
            weekly_period_start if weekly_period_start is not None else base.weekly_period_start
        ),
        weekly_opening_equity=(
            weekly_opening_equity if weekly_opening_equity is not None else starting_balance
        ),
    )


def build_position(
    *,
    direction: Direction = Direction.LONG,
    quantity: float = 1.0,
    entry_price: float = 2400.0,
    opened_at: datetime = BASE_TIME,
    stop_loss: float = 2350.0,
    take_profit: float = 2500.0,
    opened_by_verdict_fingerprint: str = DUMMY_VERDICT_FINGERPRINT,
) -> Position:
    """An open `Position` matching `build_proposal`'s default trade
    geometry (2% offset, RR=2.0) around `build_evidence`'s default
    $2,400.0 price."""
    return Position(
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        opened_at=opened_at,
        stop_loss=stop_loss,
        take_profit=take_profit,
        opened_by_verdict_fingerprint=opened_by_verdict_fingerprint,
    )
