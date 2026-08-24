"""Intrabar (OHLC-aware) stop-loss/take-profit resolution for backtesting.

`orion.paper.execution.resolve_stop_take_profit` deliberately only checks
`evidence.last_price` — a documented, approved Mission 4 v1 choice (B6 in
`docs/DECISIONS.md`): correct for live trading (no visibility into the
future), but not realistic for backtesting, where the full OHLC bar is
already known. This module is the Mission 6 counterpart with genuine
intrabar awareness — a sibling function, not a modification of the Mission 4
one, which stays exactly as shipped for live trading.

**Ambiguity and the conservative tie-break (M6-Q2, approved):** if a single
bar's `[low, high]` range reaches *both* the stop-loss and the take-profit,
OHLC data alone cannot say which was actually touched first intraday. This
module always resolves **stop-loss first** in that case — the deterministic,
conservative convention: it never assumes a better outcome for the backtest
than reality might have given. This is a genuine modeling choice, not a
computed fact from the data, and is documented here and in `docs/
DECISIONS.md` for exactly that reason.

The fill-price/fee adjustment formulas mirror `orion.paper.execution`'s
private `_fill_price_for_closing`/`_fee`/`_fill_price_for_opening` helpers
exactly, reimplemented here rather than imported: those names are private to
their module (leading underscore), and this codebase's convention is not to
reach across a package's privacy boundary. The formulas are three lines each
and are kept deliberately identical — noted here so a future reader can see
they are a conscious, minimal duplication of a trivial formula, not an
independent second implementation invented from scratch.

**`execute_delayed` (M6-Q3, approved):** `orion.paper.execution.execute` was
built assuming zero time elapses between `review()` and `execute()` — it
requires `evidence_fingerprint(evidence) == proposal.evidence_ref.content_hash`,
i.e. the *exact same* Evidence the proposal was reasoned from. Modeling order
latency genuinely needs the opposite: the fill must price off a *later* bar's
Evidence than the one the proposal was approved against. Rather than relax
`execute()`'s integrity check (an existing, already-audited paper execution
contract this mission was explicitly told not to modify), `execute_delayed`
is a new, engine-owned sibling that keeps every other one of `execute()`'s
checks — proposal/verdict linkage, action/direction consistency, and,
critically, the `portfolio_state_fingerprint` check against
`verdict.portfolio_ref` — and deliberately does *not* check the fill
Evidence against `proposal.evidence_ref`, since by construction it never
matches. The portfolio-fingerprint check still passing is what makes this
safe: `orion.backtest.engine.run_backtest` never mark-to-markets the pending
`PortfolioState` while a fill is in flight (a flat account's equity cannot
move with price anyway), so the exact `PortfolioState` `review()` reviewed
is still the one presented here, unchanged.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.evidence import Evidence
from orion.data.models import OHLCVBar
from orion.decision.schemas import ProposedDecision, decision_fingerprint
from orion.paper.execution import ExecutionResult
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


def _fill_price_for_closing(
    reference_price: float, direction: Direction, policy: PaperExecutionPolicy
) -> float:
    """Identical formula to `orion.paper.execution._fill_price_for_closing`:
    closing a LONG sells (fills lower); closing a SHORT buys back (fills
    higher)."""
    cost_bps = policy.spread_bps + policy.slippage_bps
    adjustment = reference_price * (cost_bps / 10_000.0)
    return (
        reference_price - adjustment
        if direction is Direction.LONG
        else reference_price + adjustment
    )


def _fee(notional: float, policy: PaperExecutionPolicy) -> float:
    """Identical formula to `orion.paper.execution._fee`."""
    return notional * (policy.fee_bps / 10_000.0)


def _fill_price_for_opening(
    reference_price: float, direction: Direction, policy: PaperExecutionPolicy
) -> float:
    """Identical formula to `orion.paper.execution._fill_price_for_opening`."""
    cost_bps = policy.spread_bps + policy.slippage_bps
    adjustment = reference_price * (cost_bps / 10_000.0)
    return (
        reference_price + adjustment
        if direction is Direction.LONG
        else reference_price - adjustment
    )


def execute_delayed(
    proposal: ProposedDecision,
    verdict: RiskVerdict,
    fill_evidence: Evidence,
    portfolio: PortfolioState,
    *,
    quantity: float,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> ExecutionResult:
    """Open a position from an APPROVED `verdict`, pricing the fill off
    `fill_evidence.last_price` — the Evidence for the *fill* bar, which is
    `fill_delay_bars` bars later than the Evidence `proposal`/`verdict` were
    actually reviewed against. See the module docstring's `execute_delayed`
    section for why this cannot simply call `orion.paper.execution.execute`.

    Every check `execute()` performs is performed here too, in the same
    order, with the same `PaperExecutionError` messages where the concern is
    identical — *except* the Evidence-linkage check, which is structurally
    impossible to satisfy here by design (see the module docstring) and is
    replaced with a narrower symbol-consistency check instead.
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
        raise PaperExecutionError(
            "verdict.action/direction does not match verdict.decision_ref.action/direction; "
            "cannot establish that this verdict's own action/direction reflects what "
            "orion.risk.guardian.review actually approved"
        )
    if fill_evidence.symbol != proposal.evidence_ref.symbol:
        raise PaperExecutionError(
            f"fill_evidence.symbol ({fill_evidence.symbol!r}) does not match "
            f"proposal.evidence_ref.symbol ({proposal.evidence_ref.symbol!r}); a delayed "
            "fill must still concern the same instrument the proposal was reviewed for, "
            "even though it deliberately prices off a later bar's Evidence"
        )
    if portfolio.symbol != fill_evidence.symbol:
        raise PaperExecutionError(
            f"portfolio.symbol ({portfolio.symbol!r}) does not match fill_evidence.symbol "
            f"({fill_evidence.symbol!r})"
        )
    if quantity <= 0:
        raise PaperExecutionError(f"quantity must be positive, got {quantity}")
    ref = proposal.reasoning_ref
    if ref.stop_loss is None or ref.take_profit is None:
        raise PaperExecutionError(
            "proposal.reasoning_ref has no stop_loss/take_profit; an APPROVED verdict "
            "should never reach execute_delayed() without coherent trade levels"
        )
    if portfolio.open_position is not None:
        raise PaperExecutionError(
            "portfolio already holds an open position; Mission 4 v1 is single-position-at-a-time"
        )
    if portfolio_state_fingerprint(portfolio) != verdict.portfolio_ref.content_hash:
        raise PaperExecutionError(
            "portfolio does not match verdict.portfolio_ref; cannot establish that this "
            "portfolio is the exact PortfolioState orion.risk.guardian.review actually "
            "reviewed when approving this verdict -- a different, stale, or substituted "
            "account must never be executed against an approval computed for another one"
        )

    fingerprint = risk_verdict_fingerprint(verdict)
    fill_price = _fill_price_for_opening(fill_evidence.last_price, verdict.direction, policy)
    notional = quantity * fill_price
    fee = _fee(notional, policy)
    if notional + fee > portfolio.cash:
        raise PaperExecutionError(
            f"notional+fee ({notional + fee:.2f}) exceeds available cash "
            f"({portfolio.cash:.2f}); this should already have been vetoed by "
            "orion.risk.controls.position_size_within_limits upstream"
        )

    fill = Fill(
        symbol=fill_evidence.symbol,
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
        equity_peak=max(portfolio.equity_peak, new_cash),
        daily_period_start=portfolio.daily_period_start,
        daily_opening_equity=portfolio.daily_opening_equity,
        weekly_period_start=portfolio.weekly_period_start,
        weekly_opening_equity=portfolio.weekly_opening_equity,
    )
    marked = mark_to_market(intermediate, now=now, last_price=fill_evidence.last_price)
    return ExecutionResult(fill=fill, portfolio=marked)


def resolve_stop_take_profit_ohlc(
    portfolio: PortfolioState,
    bar: OHLCVBar,
    *,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> tuple[PortfolioState, ClosedPosition | None]:
    """Mechanically close the open position if `bar`'s `[low, high]` range
    reached its stop_loss or take_profit — intrabar-aware, unlike `orion.
    paper.execution.resolve_stop_take_profit`'s last-price-only check.

    Returns `(new_portfolio, None)` — still marked-to-market against
    `bar.close` — if there is no open position or neither level was
    reached. Returns `(new_portfolio, closed_position)` if a level was
    reached; if *both* were reachable within this one bar, resolves
    stop-loss first (see the module docstring — the conservative,
    documented tie-break, never inferred from OHLC data because OHLC data
    cannot actually answer which happened first).

    Pure function: no network, filesystem, randomness, or internal clock —
    `now` is required and caller-supplied, matching every other function in
    this codebase's determinism discipline.
    """
    position = portfolio.open_position
    if position is None:
        return mark_to_market(portfolio, now=now, last_price=bar.close), None

    if position.direction is Direction.LONG:
        hit_stop = bar.low <= position.stop_loss
        hit_take_profit = bar.high >= position.take_profit
    else:
        hit_stop = bar.high >= position.stop_loss
        hit_take_profit = bar.low <= position.take_profit

    if not hit_stop and not hit_take_profit:
        return mark_to_market(portfolio, now=now, last_price=bar.close), None

    # Stop-loss first whenever it is reachable at all -- including the
    # ambiguous both-hit case (M6-Q2): this is not "stop-loss wins a
    # priority contest", it is "stop-loss is assumed to have already
    # happened by the time take-profit would have been reached".
    if hit_stop:
        reason = CloseReason.STOP_LOSS_HIT
        trigger_price = position.stop_loss
    else:
        reason = CloseReason.TAKE_PROFIT_HIT
        trigger_price = position.take_profit

    exit_price = _fill_price_for_closing(trigger_price, position.direction, policy)
    notional = position.quantity * exit_price
    fee = _fee(notional, policy)
    # Net of the closing fee only, matching orion.paper.execution.
    # resolve_stop_take_profit's identical cash-conservation derivation.
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
        equity_peak=max(portfolio.equity_peak, new_cash),
        daily_period_start=portfolio.daily_period_start,
        daily_opening_equity=portfolio.daily_opening_equity,
        weekly_period_start=portfolio.weekly_period_start,
        weekly_opening_equity=portfolio.weekly_opening_equity,
    )
    marked = mark_to_market(intermediate, now=now, last_price=bar.close)
    return marked, closed
