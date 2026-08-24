"""Deterministic paper-order execution.

`execute` is the only place in ORION that turns an APPROVED `RiskVerdict`
into a simulated fill and a new `PortfolioState`. It never accepts a VETOED
or NO_TRADE_PROPOSED verdict — there is nothing to execute, and treating
either as executable would be exactly the "the guardian may only narrow,
never invent" violation `orion.risk.guardian`/`orion.decision.engine`
already refuse one layer up, mirrored here at the execution boundary.

`resolve_stop_take_profit` mechanically closes the open position when
`evidence.last_price` reaches its stop_loss or take_profit — last-price-only
resolution (Mission 4 architecture decision B6; no intrabar high/low, no
latency realism, that is Mission 6's scope). This is never gated by the
Risk Guardian: a stop/take-profit level is a pre-committed exit the AI
already proposed and the Risk Guardian already approved when the position
was opened, so reaching it needs no new approval — the same way a real
broker's resting stop order does not re-ask permission to fill.

Both functions are pure: no network, filesystem, randomness, or internal
clock. Spread/slippage/fees are deterministic functions of
`(PaperExecutionPolicy, price)` — identical arguments always produce an
identical result. `orion.risk` never imports from this module; `orion.
paper` depends on `orion.risk`, never the other way around, so the Risk
Guardian remains pure and side-effect-free regardless of what this module
does.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.decision.schemas import ProposedDecision, decision_fingerprint
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, CloseReason, Fill, PaperExecutionError
from orion.reasoning.schemas import Direction
from orion.risk.portfolio import (
    PortfolioState,
    Position,
    mark_to_market,
    portfolio_state_fingerprint,
)
from orion.risk.schemas import RiskOutcome, RiskVerdict, risk_verdict_fingerprint


class ExecutionResult:
    """The outcome of one `execute()` call: the `Fill` that opened the
    position, and the resulting `PortfolioState`. A plain container, not a
    Pydantic model — it is never journaled or hashed as a unit; its parts
    are."""

    __slots__ = ("fill", "portfolio")

    def __init__(self, *, fill: Fill, portfolio: PortfolioState) -> None:
        self.fill = fill
        self.portfolio = portfolio


def _fill_price_for_opening(
    reference_price: float, direction: Direction, policy: PaperExecutionPolicy
) -> float:
    """Opening a LONG buys (fills higher); opening a SHORT sells (fills
    lower) — paper trading must never be more favorable than the real cost
    of trading would be."""
    cost_bps = policy.spread_bps + policy.slippage_bps
    adjustment = reference_price * (cost_bps / 10_000.0)
    return (
        reference_price + adjustment
        if direction is Direction.LONG
        else reference_price - adjustment
    )


def _fill_price_for_closing(
    reference_price: float, direction: Direction, policy: PaperExecutionPolicy
) -> float:
    """Closing a LONG sells (fills lower); closing a SHORT buys back
    (fills higher) — the opposite adjustment direction from opening."""
    cost_bps = policy.spread_bps + policy.slippage_bps
    adjustment = reference_price * (cost_bps / 10_000.0)
    return (
        reference_price - adjustment
        if direction is Direction.LONG
        else reference_price + adjustment
    )


def _fee(notional: float, policy: PaperExecutionPolicy) -> float:
    return notional * (policy.fee_bps / 10_000.0)


def execute(
    proposal: ProposedDecision,
    verdict: RiskVerdict,
    evidence: Evidence,
    portfolio: PortfolioState,
    *,
    quantity: float,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> ExecutionResult:
    """Simulate opening a position by acting on `verdict`.

    `quantity` is a required, caller-supplied value — never computed
    internally from a re-derived risk fraction. The Risk Guardian's own
    `position_size_within_limits` control already computes the size a
    proposal is entitled to (`orion.risk.portfolio.compute_position_size`)
    before ever approving it; this function trusts that number came from
    there rather than re-deriving it from a `RiskPolicy` it has no reason
    to import (see the module docstring's layering note).

    Raises PaperExecutionError if `verdict.outcome` is not APPROVED (only
    an approved trade is executable — see the module docstring), if
    `verdict.decision_ref` does not match `proposal`, if `verdict.action`/
    `verdict.direction` does not match `verdict.decision_ref.action`/
    `.direction` (an explicit check, not reliance on `Position`'s/`Fill`'s
    own geometry/NONE-rejection validators happening to catch a
    `model_copy`-tampered verdict as a side effect), if `proposal.
    evidence_ref` does not match `evidence` (the same "evaluating a
    coincidence" integrity concern enforced at every other layer boundary
    in ORION, mirroring `orion.risk.guardian.EvidenceLinkageError` one
    layer up -- a fresh or different Evidence, even with the same symbol,
    must never be filled against an approval that was reviewed against a
    different one), if `portfolio.symbol` does not match `evidence.
    symbol`, if `quantity` is not positive, if `proposal.reasoning_ref` has
    no stop_loss/take_profit, if a position is already open
    (single-position-at-a-time — `orion.risk.controls.
    no_conflicting_open_position` should already have vetoed this
    upstream, but this layer's guarantee must not depend on another
    layer's diligence), if `portfolio` is not the exact `PortfolioState`
    `verdict` was reviewed against (`portfolio_state_fingerprint(portfolio)
    != verdict.portfolio_ref.content_hash` — a cross-mission integration
    finding: every other `RiskVerdict` reference (`decision_ref`,
    `evidence_ref` transitively via `proposal`) was already verified here,
    but `portfolio_ref` was not, meaning a caller could substitute a
    different, stale, or otherwise-unreviewed account and still have it
    execute, silently bypassing every portfolio-dependent control
    `review()` actually evaluated against the real account), or if the
    resulting notional plus fee exceeds available cash (cash-covered only,
    no leverage — `orion.risk.controls.position_size_within_limits` should
    already have vetoed this upstream). None of these is an ordinary
    market condition; there is no such condition in v1 (cash-covered,
    last-price-only — Mission 4 architecture decisions B4/B6).
    """
    if verdict.outcome is not RiskOutcome.APPROVED:
        raise PaperExecutionError(
            f"verdict.outcome is {verdict.outcome.value!r}, not APPROVED; "
            "paper execution only ever acts on an approved verdict"
        )
    if decision_fingerprint(proposal) != verdict.decision_ref.content_hash:
        raise PaperExecutionError(
            "verdict.decision_ref does not match proposal; cannot establish that this "
            "verdict concerns this proposal"
        )
    if (
        verdict.action != verdict.decision_ref.action
        or verdict.direction != verdict.decision_ref.direction
    ):
        # RiskVerdict's own validator already enforces action==decision_ref.
        # action (and direction==decision_ref.direction) at normal
        # construction time, but `model_copy` bypasses every validator --
        # this layer's guarantee must not depend on the verdict having been
        # constructed normally, the same defense-in-depth reasoning already
        # applied throughout orion.risk (`proposal_was_not_upgraded`,
        # `classify_trade_levels`'s NONE-direction branch). Explicit, not
        # incidental: this does not rely on Position's/Fill's own geometry
        # or NONE-rejection validators happening to catch a tampered
        # direction as a side effect.
        raise PaperExecutionError(
            "verdict.action/direction does not match verdict.decision_ref.action/direction; "
            "cannot establish that this verdict's own action/direction reflects what "
            "orion.risk.guardian.review actually approved"
        )
    if evidence_fingerprint(evidence) != proposal.evidence_ref.content_hash:
        raise PaperExecutionError(
            "proposal.evidence_ref does not match the Evidence passed to execute(); "
            "cannot establish that this proposal (and the verdict that approved it) was "
            "produced from this evidence -- a fresh or different Evidence, even with the "
            "same symbol, must never be filled against a stale approval"
        )
    if portfolio.symbol != evidence.symbol:
        raise PaperExecutionError(
            f"portfolio.symbol ({portfolio.symbol!r}) does not match evidence.symbol "
            f"({evidence.symbol!r})"
        )
    if quantity <= 0:
        raise PaperExecutionError(f"quantity must be positive, got {quantity}")
    ref = proposal.reasoning_ref
    if ref.stop_loss is None or ref.take_profit is None:
        raise PaperExecutionError(
            "proposal.reasoning_ref has no stop_loss/take_profit; an APPROVED verdict "
            "should never reach execute() without coherent trade levels"
        )
    if portfolio.open_position is not None:
        raise PaperExecutionError(
            "portfolio already holds an open position; Mission 4 v1 is single-position-at-a-time"
        )
    if portfolio_state_fingerprint(portfolio) != verdict.portfolio_ref.content_hash:
        # Cross-mission integration finding: every other RiskVerdict
        # reference (decision_ref, evidence_ref transitively via proposal)
        # was already verified above -- portfolio_ref was not, meaning a
        # caller could substitute a different, stale, or otherwise-
        # unreviewed PortfolioState and still have it execute, silently
        # bypassing every portfolio-dependent control review() actually
        # evaluated (position_size_within_limits, exposure_within_limit,
        # daily/weekly_loss_limit, max_drawdown, portfolio_state_is_fresh)
        # against the real account. This is the final gate, run last and
        # deliberately after the symbol/open-position checks above, so
        # those checks' own, more specific error messages are preserved
        # for the scenarios they were written to catch; this one closes
        # everything else -- a portfolio that is symbol-correct, flat, and
        # affordable, but simply is not the account this verdict concerns.
        raise PaperExecutionError(
            "portfolio does not match verdict.portfolio_ref; cannot establish that this "
            "portfolio is the exact PortfolioState orion.risk.guardian.review actually "
            "reviewed when approving this verdict -- a different, stale, or substituted "
            "account must never be executed against an approval computed for another one"
        )

    fingerprint = risk_verdict_fingerprint(verdict)
    fill_price = _fill_price_for_opening(evidence.last_price, verdict.direction, policy)
    notional = quantity * fill_price
    fee = _fee(notional, policy)
    if notional + fee > portfolio.cash:
        raise PaperExecutionError(
            f"notional+fee ({notional + fee:.2f}) exceeds available cash "
            f"({portfolio.cash:.2f}); this should already have been vetoed by "
            "orion.risk.controls.position_size_within_limits upstream"
        )

    fill = Fill(
        symbol=evidence.symbol,
        direction=verdict.direction,
        quantity=quantity,
        fill_price=fill_price,
        fee=fee,
        filled_at=now,
        risk_verdict_fingerprint=fingerprint,
    )
    new_position = Position(
        direction=verdict.direction,
        quantity=quantity,
        entry_price=fill_price,
        opened_at=now,
        stop_loss=ref.stop_loss,
        take_profit=ref.take_profit,
        opened_by_verdict_fingerprint=fingerprint,
    )
    new_cash = portfolio.cash - notional - fee
    intermediate = PortfolioState(
        symbol=portfolio.symbol,
        as_of=portfolio.as_of,
        cash=new_cash,
        equity=new_cash,
        starting_balance=portfolio.starting_balance,
        open_position=new_position,
        # max() guards the "equity <= equity_peak" invariant on this
        # intermediate object itself -- mark_to_market below recomputes the
        # real equity_peak from the marked-to-market equity anyway, but
        # constructing this intermediate must already be self-consistent.
        equity_peak=max(portfolio.equity_peak, new_cash),
        daily_period_start=portfolio.daily_period_start,
        daily_opening_equity=portfolio.daily_opening_equity,
        weekly_period_start=portfolio.weekly_period_start,
        weekly_opening_equity=portfolio.weekly_opening_equity,
    )
    marked = mark_to_market(intermediate, now=now, last_price=evidence.last_price)
    return ExecutionResult(fill=fill, portfolio=marked)


def resolve_stop_take_profit(
    portfolio: PortfolioState,
    evidence: Evidence,
    *,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> tuple[PortfolioState, ClosedPosition | None]:
    """Mechanically close the open position if `evidence.last_price` has
    reached its stop_loss or take_profit.

    Returns `(new_portfolio, None)` — still marked-to-market — if there is
    no open position or neither level has been reached. Returns
    `(new_portfolio, closed_position)` if a level was reached; the returned
    portfolio has no open position and reflects the realized P&L.

    Raises PaperExecutionError if `portfolio.symbol` does not match
    `evidence.symbol`.
    """
    if portfolio.symbol != evidence.symbol:
        raise PaperExecutionError(
            f"portfolio.symbol ({portfolio.symbol!r}) does not match evidence.symbol "
            f"({evidence.symbol!r})"
        )
    position = portfolio.open_position
    last_price = evidence.last_price
    if position is None:
        return mark_to_market(portfolio, now=now, last_price=last_price), None

    if position.direction is Direction.LONG:
        hit_stop = last_price <= position.stop_loss
        hit_take_profit = last_price >= position.take_profit
    else:
        hit_stop = last_price >= position.stop_loss
        hit_take_profit = last_price <= position.take_profit

    if not hit_stop and not hit_take_profit:
        return mark_to_market(portfolio, now=now, last_price=last_price), None

    reason = CloseReason.STOP_LOSS_HIT if hit_stop else CloseReason.TAKE_PROFIT_HIT
    exit_price = _fill_price_for_closing(last_price, position.direction, policy)
    notional = position.quantity * exit_price
    fee = _fee(notional, policy)
    # Net of the closing fee only -- the opening fee already reduced cash
    # directly when the position was opened. Cash conservation across a
    # full round trip is: cash_after_close = cash_before_open + realized_pnl
    # - fee_open - fee_close (verified in tests/paper/test_execution.py).
    realized_pnl = position.unrealized_pnl(exit_price) - fee

    closed = ClosedPosition(
        symbol=portfolio.symbol,
        direction=position.direction,
        quantity=position.quantity,
        entry_price=position.entry_price,
        exit_price=exit_price,
        opened_at=position.opened_at,
        closed_at=now,
        close_reason=reason,
        realized_pnl=realized_pnl,
        fee=fee,
        opened_by_verdict_fingerprint=position.opened_by_verdict_fingerprint,
    )

    new_cash = portfolio.cash + position.quantity * position.entry_price + realized_pnl
    intermediate = PortfolioState(
        symbol=portfolio.symbol,
        as_of=portfolio.as_of,
        cash=new_cash,
        equity=new_cash,
        starting_balance=portfolio.starting_balance,
        open_position=None,
        # See the identical max() note in execute() -- a large favorable
        # gap can realize more profit than any prior mark_to_market call
        # ever observed, so equity_peak must be allowed to jump here too.
        equity_peak=max(portfolio.equity_peak, new_cash),
        daily_period_start=portfolio.daily_period_start,
        daily_opening_equity=portfolio.daily_opening_equity,
        weekly_period_start=portfolio.weekly_period_start,
        weekly_opening_equity=portfolio.weekly_opening_equity,
    )
    marked = mark_to_market(intermediate, now=now, last_price=last_price)
    return marked, closed
