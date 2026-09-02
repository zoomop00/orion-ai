"""Deterministic paper-order execution.

`execute` is the only place in ORION that turns an APPROVED `RiskVerdict`
into a simulated fill and a new `PortfolioState`. It never accepts a VETOED
or NO_TRADE_PROPOSED verdict — there is nothing to execute, and treating
either as executable would be exactly the "the guardian may only narrow,
never invent" violation `orion.risk.guardian`/`orion.decision.engine`
already refuse one layer up, mirrored here at the execution boundary.

`resolve_stop_take_profit` mechanically closes the open position when the
observed last price reaches its stop_loss or take_profit — last-price-only
resolution (Mission 4 architecture decision B6; no intrabar high/low, no
latency realism, that is Mission 6's scope). This is never gated by the
Risk Guardian: a stop/take-profit level is a pre-committed exit the AI
already proposed and the Risk Guardian already approved when the position
was opened, so reaching it needs no new approval — the same way a real
broker's resting stop order does not re-ask permission to fill.

IT ASKS FOR A PRICE, NOT FOR AN ANALYSIS (Mission 9). `resolve_stop_take_
profit` takes an `ObservedPrice` — the two fields it actually reads — rather
than the `Evidence` it used to demand. `Evidence` satisfies that contract
structurally and every existing caller is unchanged, but the requirement is
now the honest one: checking whether a stop was reached needs a price, and
demanding an `Evidence` for it made a mechanical exit depend on indicators,
market structure and, through the pipeline that builds them, a model call. See
`orion.data.models.PriceObservation` for the concrete value type the
monitoring path supplies instead.

`resolve_stop_take_profit_bars` (Mission 9 Phase 2) is the intrabar-aware
sibling, added rather than substituted: it scans the bars since the position
opened and closes on the first whose `[low, high]` reached a level, catching a
crossing that happened *between* two observations and left no trace in any
close. It is a sibling for the same reason `resolve_stop_take_profit_ohlc` is
one — the last-price function is a shipped, audited contract, and `run_cycle`
still calls it, so `orion paper` behaves exactly as before.

WHERE IT DELIBERATELY DIFFERS FROM THE BACKTEST'S BAR RESOLVER: the fill price
on a gap. The backtest always fills at the level; this fills at the level
*unless the bar opened through it*, in which case it fills at the open. See
`_trigger_price`. A backtest may model a resting order; a forward paper account
that did the same would report better results than the market gave it.

All of these are pure: no network, filesystem, randomness, or internal
clock. Spread/slippage/fees are deterministic functions of
`(PaperExecutionPolicy, price)` — identical arguments always produce an
identical result. `orion.risk` never imports from this module; `orion.
paper` depends on `orion.risk`, never the other way around, so the Risk
Guardian remains pure and side-effect-free regardless of what this module
does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.data.models import MarketSnapshot, OHLCVBar
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


@runtime_checkable
class ObservedPrice(Protocol):
    """What `resolve_stop_take_profit` requires: a symbol, and its last price.

    A Protocol rather than a concrete type, for the same reason
    `orion.paper.session.JournalWriter` is one — it restates a *surface*, so
    two unrelated types can satisfy it without either importing the other or a
    common base being invented for them. `orion.data.models.PriceObservation`
    is the value type the Mission 9 monitoring path builds; `orion.analysis.
    evidence.Evidence` satisfies it too, which is why every pre-Mission-9
    caller of the resolver is unchanged and no test of one had to be edited.

    THE TWO MEMBERS ARE THE TWO THE RESOLVER READS, AND THAT IS THE POINT.
    `symbol` is compared against the account's own, and `last_price` is
    compared against the position's stop and target. `as_of` is deliberately
    absent: the resolver stamps a close with the caller-supplied `now`, so
    declaring a timestamp here would claim a dependency this function does not
    have. Widening this Protocol is how the narrowing gets quietly undone, so
    a member should be added only alongside a use of it.

    Read-only properties, not plain annotations, so a frozen model satisfies it
    as readily as a mutable one — the requirement is that the value can be
    read, never that it can be assigned.
    """

    @property
    def symbol(self) -> str:
        """The market this price is for."""
        ...

    @property
    def last_price(self) -> float:
        """The most recent traded price. Positive, by every producer's own
        validation — never re-checked here, and never defaulted."""
        ...


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
    price: ObservedPrice,
    *,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> tuple[PortfolioState, ClosedPosition | None]:
    """Mechanically close the open position if `price.last_price` has
    reached its stop_loss or take_profit.

    Returns `(new_portfolio, None)` — still marked-to-market — if there is
    no open position or neither level has been reached. Returns
    `(new_portfolio, closed_position)` if a level was reached; the returned
    portfolio has no open position and reflects the realized P&L.

    Raises PaperExecutionError if `portfolio.symbol` does not match
    `price.symbol`.

    `price` is an `ObservedPrice` — see that Protocol and the module docstring.
    An `Evidence` still satisfies it, so this signature is source-compatible
    with every caller that predates Mission 9; what changed is that a caller
    holding only a price no longer has to manufacture an analysis to use it.
    """
    if portfolio.symbol != price.symbol:
        raise PaperExecutionError(
            f"portfolio.symbol ({portfolio.symbol!r}) does not match price.symbol "
            f"({price.symbol!r})"
        )
    position = portfolio.open_position
    last_price = price.last_price
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
    return _close_at(
        portfolio,
        position,
        trigger_price=last_price,
        reason=reason,
        mark_price=last_price,
        policy=policy,
        now=now,
    )


def _close_at(
    portfolio: PortfolioState,
    position: Position,
    *,
    trigger_price: float,
    reason: CloseReason,
    mark_price: float,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> tuple[PortfolioState, ClosedPosition]:
    """The close arithmetic both forward resolvers share.

    Extracted rather than copied a second time when Mission 9 Phase 2 added
    `resolve_stop_take_profit_bars`: the cash-conservation derivation below is
    the delicate part of this module, and two copies of it could drift. The
    last-price resolver passes `trigger_price == mark_price == last_price`,
    which is byte-for-byte what it did before the extraction.

    `trigger_price` is the price the exit is *filled* from, before spread and
    slippage. `mark_price` is the price the resulting flat account is marked to.
    They differ only for the bar-aware resolver, where the fill comes from a
    level (or a gapped open) and the mark comes from the bar's close.
    """
    exit_price = _fill_price_for_closing(trigger_price, position.direction, policy)
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
    marked = mark_to_market(intermediate, now=now, last_price=mark_price)
    return marked, closed


def _levels_reached(position: Position, bar: OHLCVBar) -> tuple[bool, bool]:
    """Whether `bar`'s `[low, high]` range reached the stop and/or the target.

    The Mission 6 detection rule, restated for the forward path rather than
    re-decided: a LONG stops out when the bar traded at or below `stop_loss`
    and targets out when it traded at or above `take_profit`; a SHORT is the
    mirror. Reading a bar's extremes is not look-ahead — those prices already
    happened, which is precisely why last-price-only checking missed them.
    """
    if position.direction is Direction.LONG:
        return bar.low <= position.stop_loss, bar.high >= position.take_profit
    return bar.high >= position.stop_loss, bar.low <= position.take_profit


def _trigger_price(position: Position, bar: OHLCVBar, reason: CloseReason) -> float:
    """The price a triggered exit fills from, before spread and slippage.

    THE WORST-OF GAP RULE, AND WHY FORWARD DIFFERS FROM BACKTEST.
    `orion.backtest.execution.resolve_stop_take_profit_ohlc` always fills at
    the level itself, so a bar that gapped straight through a stop still books
    the loss as if a resting order had filled exactly there. That is a
    defensible *backtest* convention and it stays untouched; adopting it
    forward would make paper results quietly better than reality on every
    adverse gap, in a module whose own fill helpers are documented as never
    being more favourable than the real cost of trading.

    So: fill at the level, **unless the bar had already opened through it**, in
    which case fill at the open. Stated once and symmetric in all four cases —
    if `bar.open` on its own already satisfies the trigger condition, the
    position could not have filled at the level, because the level was never
    available after the bar began.

        LONG  stop   trigger is price <= stop_loss     -> open below it wins
        LONG  target trigger is price >= take_profit   -> open above it wins
        SHORT stop   trigger is price >= stop_loss     -> open above it wins
        SHORT target trigger is price <= take_profit   -> open below it wins

    Deterministic, computed only from `open` and the position's own levels, and
    never more favourable than the level on the adverse side. It uses no data
    the bar does not already carry and invents no vendor behaviour.
    """
    long_position = position.direction is Direction.LONG
    if reason is CloseReason.STOP_LOSS_HIT:
        level = position.stop_loss
        # A LONG stop triggers downward, a SHORT stop upward.
        crossed = bar.open <= level if long_position else bar.open >= level
    else:
        level = position.take_profit
        # A LONG target triggers upward, a SHORT target downward.
        crossed = bar.open >= level if long_position else bar.open <= level
    return bar.open if crossed else level


def resolve_stop_take_profit_bars(
    portfolio: PortfolioState,
    snapshot: MarketSnapshot,
    *,
    policy: PaperExecutionPolicy,
    now: datetime,
) -> tuple[PortfolioState, ClosedPosition | None, OHLCVBar | None]:
    """Close the open position on the first bar whose range reached a level.

    MISSION 9 PHASE 2: WHAT LAST-PRICE CHECKING CANNOT SEE. A price that
    crosses a stop and comes back before the next observation leaves no trace
    in `close`, so `resolve_stop_take_profit` reports HOLDING for a position
    that really did stop out. The bar's `low`/`high` do carry that trace, and
    they were already being fetched and thrown away.

    Returns `(new_portfolio, closed_position, triggering_bar)`. The third
    element is the bar that caused the close, for provenance and rendering
    only — `ClosedPosition` is deliberately unchanged by this phase, and
    `closed_at` remains the caller-supplied `now`, never a bar timestamp.

    BARS AT OR BEFORE `Position.opened_at` ARE EXCLUDED, CONSERVATIVELY. The
    bar the position was opened from had already traded its range before the
    entry existed, so honouring its extremes would close a position on prices
    that predate it. Excluding the whole bar containing `opened_at` also
    discards the part of it that came *after* entry — deliberately: that is a
    missed exit, never an invented one, and the alternative needs intrabar
    timing this data cannot supply. `orion.backtest.engine` achieves the same
    exclusion positionally, by resolving from the bar after the entry.

    SCANNED OLDEST-FIRST, CLOSING ON THE FIRST TRIGGER. Later bars are never
    examined: a position closes once, and anything after that close belongs to
    an account that no longer holds it.

    BOTH LEVELS IN ONE BAR IS A STOP (M6-Q2). Already an approved ORION
    convention; restated, not re-decided. OHLC cannot say which came first, so
    the stop is assumed to have happened by the time the target was reached.

    THE NEWEST BAR MAY STILL BE FORMING, and the vendor exposes no flag saying
    so. A hit seen in it is real and closes the position — those prices traded.
    The *absence* of a hit in it proves nothing about the rest of that bar,
    which is why "did nothing happen?" is answered by coverage reporting in
    `orion.paper.session`, never by this function's `None`.

    Pure: no network, filesystem, randomness or internal clock. Raises
    `PaperExecutionError` if the snapshot is for a different symbol than the
    account — the check the backtest's bar resolver structurally cannot make,
    since a bare `OHLCVBar` does not know what it is a bar of.
    """
    if portfolio.symbol != snapshot.source.symbol:
        raise PaperExecutionError(
            f"portfolio.symbol ({portfolio.symbol!r}) does not match snapshot symbol "
            f"({snapshot.source.symbol!r})"
        )

    position = portfolio.open_position
    eligible = eligible_bars(snapshot, position)
    if position is None or not eligible:
        # Nothing to resolve, or nothing that happened after the entry. The
        # account is returned untouched rather than marked: with no eligible
        # bar there is no price this function is entitled to value it at, and
        # marking it against a pre-entry bar would be a fabricated valuation.
        return portfolio, None, None

    for bar in eligible:
        hit_stop, hit_take_profit = _levels_reached(position, bar)
        if not hit_stop and not hit_take_profit:
            continue
        reason = CloseReason.STOP_LOSS_HIT if hit_stop else CloseReason.TAKE_PROFIT_HIT
        marked, closed = _close_at(
            portfolio,
            position,
            trigger_price=_trigger_price(position, bar, reason),
            reason=reason,
            mark_price=bar.close,
            policy=policy,
            now=now,
        )
        return marked, closed, bar

    return mark_to_market(portfolio, now=now, last_price=eligible[-1].close), None, None


def eligible_bars(snapshot: MarketSnapshot, position: Position | None) -> tuple[OHLCVBar, ...]:
    """The bars a resolution may act on: those strictly after `opened_at`.

    Exported because coverage reporting must reason about exactly the same set
    this resolver acts on — two independent notions of "which bars counted"
    would eventually disagree, and the one a user is shown would be the wrong
    one. See `resolve_stop_take_profit_bars` for why the boundary is strict.
    """
    if position is None:
        return ()
    return tuple(bar for bar in snapshot.bars if bar.timestamp > position.opened_at)
