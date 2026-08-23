"""The Risk Guardian's deterministic controls.

Every control is a pure function of `(ProposedDecision, Evidence,
RiskPolicy, PortfolioState, datetime)` — already-validated objects plus
operator configuration, account state, and the wall clock. This argument
list is the mechanical test for what belongs here versus in
`orion.decision.rules`: a Decision Engine rule needs only Evidence +
ReasoningOutput; a Risk Guardian control needs at least one of the clock,
policy, or portfolio state. No control in this module reads
`ProposedDecision.reasoning_ref.confidence` or `.risk_level` — both are
present on `reasoning_ref` (a `ReasoningRef`) and could be read by a
control's code, so this is a property this file's functions maintain, not
a structural impossibility; `tests/risk/test_guardian.py` enforces it
behaviorally. Rationale text is the one thing genuinely unreachable here:
`ReasoningRef` never carries it at all, and `orion.risk.guardian.review`
does not even accept a `ReasoningOutput` as an argument, so there is no path
to the model's prose anywhere in this package.

All controls always run; the guardian never short-circuits — a veto record
showing every check beats one showing only the first failure, and with a
kill switch present, short-circuiting would hide what else would have
blocked the trade before it's disengaged.

Every control except `proposal_was_not_upgraded` applies only to a proposed
trade (`proposal.is_trade_proposal`) — there is nothing to gate on a WAIT,
so they report `NOT_APPLICABLE` instead of a vacuous pass.
`proposal_was_not_upgraded` is the one control that always applies,
including to a WAIT.

Mission 4 added `portfolio_state_is_fresh`, `no_conflicting_open_position`,
`position_size_within_limits`, `exposure_within_limit`,
`daily_loss_limit_not_breached`, `weekly_loss_limit_not_breached`, and
`max_drawdown_not_breached` — the controls Mission 3 explicitly deferred
because the portfolio/account state they need did not exist yet (see
`docs/DECISIONS.md`). Correlation and liquidity controls remain deferred:
ORION trades one symbol and has no order-book depth data, and Mission 4
does not change either fact.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from orion.analysis.evidence import Evidence
from orion.analysis.structure import VolatilityLevel
from orion.data.validation import TIMEFRAME_INTERVAL
from orion.decision.schemas import ProposedDecision
from orion.reasoning.schemas import Action, Direction
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import PortfolioState, compute_position_size
from orion.risk.schemas import ControlOutcome, ControlStatus, RiskControlName, RiskReasonCode

Control = Callable[
    [ProposedDecision, Evidence, RiskPolicy, PortfolioState, datetime], ControlOutcome
]


def _not_applicable(control: RiskControlName) -> ControlOutcome:
    return ControlOutcome(
        control=control,
        status=ControlStatus.NOT_APPLICABLE,
        detail="action=wait; control applies to trade proposals only",
    )


def classify_trade_levels(
    direction: Direction, entry: float, stop_loss: float | None, take_profit: float | None
) -> RiskReasonCode | None:
    """Returns None if `stop_loss`/`take_profit` form a coherent trade around
    `entry`, else the specific reason they do not.

    Shared by `trade_levels_are_coherent_with_price`, `risk_reward_meets_
    minimum`, `position_size_within_limits`, and `exposure_within_limit` so
    none of them can ever disagree about what "coherent" means. Checks only
    geometry — never a threshold, never a computed ratio.
    """
    if stop_loss is None or take_profit is None:
        return RiskReasonCode.TRADE_LEVELS_MISSING
    if stop_loss == entry:
        return RiskReasonCode.DEGENERATE_STOP_DISTANCE
    if direction is Direction.LONG:
        if not (stop_loss < entry < take_profit):
            return RiskReasonCode.TRADE_LEVELS_INCOHERENT
    elif direction is Direction.SHORT:
        if not (take_profit < entry < stop_loss):
            return RiskReasonCode.TRADE_LEVELS_INCOHERENT
    else:
        # direction is NONE on a trade proposal — ProposedDecision's own
        # BUY=>LONG/SELL=>SHORT invariant already forbids this for a normally
        # constructed object; treated defensively as incoherent for a
        # reconstructed/journaled one, matching this package's established
        # defense-in-depth pattern.
        return RiskReasonCode.TRADE_LEVELS_INCOHERENT
    return None


def proposal_was_not_upgraded(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Restated defense-in-depth: `ProposedDecision`'s own validator already
    enforces that its action can only be WAIT or the AI's proposed action,
    but a proposal reaching this guardian may have been reconstructed from
    journaled JSON (Mission 2) under a relaxed or older schema, or produced
    by a future Decision Engine version — this guardian's own guarantee must
    not depend on another layer's diligence. Always applicable, including to
    a WAIT proposal.
    """
    control = RiskControlName.PROPOSAL_WAS_NOT_UPGRADED
    ai_action = proposal.reasoning_ref.action
    mismatched_action = proposal.action not in (Action.WAIT, ai_action)
    mismatched_direction = (
        proposal.action is Action.BUY and proposal.direction is not Direction.LONG
    ) or (proposal.action is Action.SELL and proposal.direction is not Direction.SHORT)
    if mismatched_action or mismatched_direction:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=(
                f"proposal.action={proposal.action.value!r} direction="
                f"{proposal.direction.value!r} ai_action={ai_action.value!r}"
            ),
            reason_code=RiskReasonCode.PROPOSAL_NOT_AUTHORIZED_BY_ENGINE,
        )
    return ControlOutcome(
        control=control, status=ControlStatus.PASSED, detail="proposal action/direction authorized"
    )


def kill_switch_disengaged(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    control = RiskControlName.KILL_SWITCH_DISENGAGED
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    if policy.kill_switch is KillSwitchState.ENGAGED:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail="policy.kill_switch=engaged",
            reason_code=RiskReasonCode.KILL_SWITCH_ENGAGED,
        )
    return ControlOutcome(
        control=control, status=ControlStatus.PASSED, detail="kill switch disengaged"
    )


def evidence_is_fresh(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Distinct from `orion.data.validation.validate_snapshot`'s staleness
    check, which runs upstream at fetch time. This measures staleness at
    review time — after the entire evidence-fetch-to-reasoning round-trip —
    so evidence that was fresh when validated can still be caught here if it
    went stale while Claude was thinking.
    """
    control = RiskControlName.EVIDENCE_IS_FRESH
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    if evidence.as_of > now:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"evidence.as_of={evidence.as_of.isoformat()} is after now={now.isoformat()}",
            reason_code=RiskReasonCode.EVIDENCE_TIMESTAMP_IN_FUTURE,
        )
    interval = TIMEFRAME_INTERVAL[evidence.timeframe]
    age = now - evidence.as_of
    allowed = interval * policy.max_staleness_bars
    if age > allowed:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"evidence age {age} exceeds {allowed} ({policy.max_staleness_bars} bars)",
            reason_code=RiskReasonCode.EVIDENCE_STALE,
        )
    return ControlOutcome(
        control=control, status=ControlStatus.PASSED, detail=f"evidence age {age}"
    )


def decision_is_current(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Measures a different quantity than `evidence_is_fresh`: proposal age,
    not evidence age. A proposal replayed from a queue or journal can be
    stale even when the evidence it references was fresh the moment it was
    computed.
    """
    control = RiskControlName.DECISION_IS_CURRENT
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    if proposal.decided_at > now:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"decided_at={proposal.decided_at.isoformat()} is after now={now.isoformat()}",
            reason_code=RiskReasonCode.DECISION_TIMESTAMP_IN_FUTURE,
        )
    interval = TIMEFRAME_INTERVAL[evidence.timeframe]
    age = now - proposal.decided_at
    allowed = interval * policy.max_decision_age_bars
    if age > allowed:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"decision age {age} exceeds {allowed} ({policy.max_decision_age_bars} bars)",
            reason_code=RiskReasonCode.DECISION_EXPIRED,
        )
    return ControlOutcome(
        control=control, status=ControlStatus.PASSED, detail=f"decision age {age}"
    )


def portfolio_state_is_fresh(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """The portfolio-state analogue of `evidence_is_fresh`/`decision_is_
    current`: a `PortfolioState` computed hours before this review is
    exactly the "evaluating a coincidence" staleness this codebase already
    treats as a control failure rather than a silent default. A missing or
    stale portfolio snapshot must never be read as "flat, no exposure" —
    that would let a broken input pass every portfolio control by accident
    (Mission 4 architecture decision B7).
    """
    control = RiskControlName.PORTFOLIO_STATE_IS_FRESH
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    if portfolio.as_of > now:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"portfolio.as_of={portfolio.as_of.isoformat()} is after now={now.isoformat()}",
            reason_code=RiskReasonCode.PORTFOLIO_TIMESTAMP_IN_FUTURE,
        )
    interval = TIMEFRAME_INTERVAL[evidence.timeframe]
    age = now - portfolio.as_of
    allowed = interval * policy.max_portfolio_staleness_bars
    if age > allowed:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=(
                f"portfolio age {age} exceeds {allowed} "
                f"({policy.max_portfolio_staleness_bars} bars)"
            ),
            reason_code=RiskReasonCode.PORTFOLIO_STALE,
        )
    return ControlOutcome(
        control=control, status=ControlStatus.PASSED, detail=f"portfolio age {age}"
    )


def no_conflicting_open_position(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Mission 4 v1 is single-position, single-symbol (architecture decision
    B2): a new trade proposal is never approved while a position is already
    open. Nothing here decides how or when the existing position closes —
    that is `orion.paper`'s mechanical last-price stop/take-profit
    resolution, entirely independent of the Risk Guardian.
    """
    control = RiskControlName.NO_CONFLICTING_OPEN_POSITION
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    if portfolio.open_position is not None:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=(
                f"portfolio already holds an open {portfolio.open_position.direction.value} "
                "position"
            ),
            reason_code=RiskReasonCode.POSITION_ALREADY_OPEN,
        )
    return ControlOutcome(control=control, status=ControlStatus.PASSED, detail="no open position")


def volatility_regime_permitted(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Reads `structure.volatility` — an already-computed classification,
    never re-derived here. Its LOW/MEDIUM/HIGH cut points are documented
    (`orion.analysis.structure`, `docs/DECISIONS.md`) as uncalibrated
    placeholders; by default only UNKNOWN is blocked (total absence of a
    reading, matching the Decision Engine's `trade_requires_known_trend`
    precedent), and blocking HIGH is an explicit policy opt-in. The observed
    level is recorded in `detail` on every decision, approved or not,
    regardless of whether it was blocked — that record is exactly the
    dataset Mission 6 will need to calibrate the thresholds for real.
    """
    control = RiskControlName.VOLATILITY_REGIME_PERMITTED
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    level = evidence.structure.volatility
    if level in policy.blocked_volatility_levels:
        reason = (
            RiskReasonCode.VOLATILITY_UNKNOWN
            if level is VolatilityLevel.UNKNOWN
            else RiskReasonCode.VOLATILITY_REGIME_BLOCKED
        )
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"structure.volatility={level.value!r} is blocked by policy",
            reason_code=reason,
        )
    return ControlOutcome(
        control=control, status=ControlStatus.PASSED, detail=f"structure.volatility={level.value!r}"
    )


def trade_levels_are_coherent_with_price(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Validates the AI's proposed stop_loss/take_profit against
    `evidence.last_price` — the one check `ReasoningVerdict`'s own validator
    cannot make, since that schema has no price field. The AI proposes
    geometry; this control (and `risk_reward_meets_minimum`) is what actually
    trusts or rejects it.
    """
    control = RiskControlName.TRADE_LEVELS_ARE_COHERENT_WITH_PRICE
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    ref = proposal.reasoning_ref
    failure = classify_trade_levels(
        proposal.direction, evidence.last_price, ref.stop_loss, ref.take_profit
    )
    if failure is not None:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=(
                f"entry={evidence.last_price} stop_loss={ref.stop_loss!r} "
                f"take_profit={ref.take_profit!r} direction={proposal.direction.value!r}"
            ),
            reason_code=failure,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=(
            f"entry={evidence.last_price} stop_loss={ref.stop_loss} take_profit={ref.take_profit}"
        ),
    )


def risk_reward_meets_minimum(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Computes risk/reward itself from `evidence.last_price` and the AI's
    proposed levels — never trusts a self-reported ratio, because none
    exists: `ReasoningVerdict` has no such field. `NOT_APPLICABLE` whenever
    the levels are missing or incoherent (see `classify_trade_levels`):
    `trade_levels_are_coherent_with_price` already reports the specific
    reason in that case, and computing a "ratio" from geometrically
    nonsensical levels would be a meaningless, misleading number in the audit
    trail. `>=` is inclusive: a ratio exactly at the policy minimum passes.
    """
    control = RiskControlName.RISK_REWARD_MEETS_MINIMUM
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    ref = proposal.reasoning_ref
    entry = evidence.last_price
    failure = classify_trade_levels(proposal.direction, entry, ref.stop_loss, ref.take_profit)
    if failure is not None:
        return ControlOutcome(
            control=control,
            status=ControlStatus.NOT_APPLICABLE,
            detail="requires coherent, non-degenerate trade levels; see "
            "trade_levels_are_coherent_with_price",
        )
    assert (
        ref.stop_loss is not None and ref.take_profit is not None
    )  # narrowed by classify_trade_levels
    risk = abs(entry - ref.stop_loss)
    reward = abs(ref.take_profit - entry)
    rr = reward / risk
    if rr < policy.min_risk_reward:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"rr={rr:.4f} < min={policy.min_risk_reward:.4f}",
            reason_code=RiskReasonCode.RISK_REWARD_BELOW_MINIMUM,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=f"rr={rr:.4f} >= min={policy.min_risk_reward:.4f}",
    )


def position_size_within_limits(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """The "max risk/trade" control. Computes the deterministic position
    size (`orion.risk.portfolio.compute_position_size`) using the *same*
    formula `orion.paper.execution` will use to actually size the trade, so
    the two can never disagree — and fails closed if that size is
    non-positive (equity exhausted) or its notional would exceed available
    cash, since Mission 4 v1 is cash-covered only, with no leverage/margin
    (architecture decision B4). `NOT_APPLICABLE` whenever the levels are
    missing or incoherent, for the same reason `risk_reward_meets_minimum`
    is: computing a size from geometrically nonsensical levels would be
    meaningless, and `trade_levels_are_coherent_with_price` already reports
    the specific reason.
    """
    control = RiskControlName.POSITION_SIZE_WITHIN_LIMITS
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    ref = proposal.reasoning_ref
    entry = evidence.last_price
    failure = classify_trade_levels(proposal.direction, entry, ref.stop_loss, ref.take_profit)
    if failure is not None:
        return ControlOutcome(
            control=control,
            status=ControlStatus.NOT_APPLICABLE,
            detail="requires coherent, non-degenerate trade levels; see "
            "trade_levels_are_coherent_with_price",
        )
    assert ref.stop_loss is not None
    size = compute_position_size(
        portfolio.equity,
        entry,
        ref.stop_loss,
        risk_per_trade_fraction=policy.max_risk_per_trade_fraction,
    )
    if size <= 0:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"computed size={size} is non-positive (equity={portfolio.equity})",
            reason_code=RiskReasonCode.POSITION_SIZE_INVALID,
        )
    notional = size * entry
    if notional > portfolio.cash:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"computed notional={notional:.2f} exceeds available cash={portfolio.cash:.2f}",
            reason_code=RiskReasonCode.POSITION_SIZE_EXCEEDS_AVAILABLE_CASH,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=f"computed size={size:.6f} notional={notional:.2f}",
    )


def exposure_within_limit(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Bounds notional commitment to `policy.max_exposure_fraction *
    equity`, independent of stop distance — a distinct bound from
    `position_size_within_limits` (which bounds loss-on-stop): plenty of
    cash and a tight stop can still imply committing more of the account to
    one position than an operator wants, even with no risk of a margin call.
    Single-symbol/single-position, so "exposure" is simply this one
    position's notional value.
    """
    control = RiskControlName.EXPOSURE_WITHIN_LIMIT
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    ref = proposal.reasoning_ref
    entry = evidence.last_price
    failure = classify_trade_levels(proposal.direction, entry, ref.stop_loss, ref.take_profit)
    if failure is not None:
        return ControlOutcome(
            control=control,
            status=ControlStatus.NOT_APPLICABLE,
            detail="requires coherent, non-degenerate trade levels; see "
            "trade_levels_are_coherent_with_price",
        )
    if portfolio.equity <= 0:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"portfolio.equity={portfolio.equity} is non-positive; no exposure is permitted",
            reason_code=RiskReasonCode.EXPOSURE_LIMIT_EXCEEDED,
        )
    assert ref.stop_loss is not None
    size = compute_position_size(
        portfolio.equity,
        entry,
        ref.stop_loss,
        risk_per_trade_fraction=policy.max_risk_per_trade_fraction,
    )
    notional = size * entry
    exposure_fraction = notional / portfolio.equity
    if exposure_fraction > policy.max_exposure_fraction:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"exposure={exposure_fraction:.4f} > max={policy.max_exposure_fraction:.4f}",
            reason_code=RiskReasonCode.EXPOSURE_LIMIT_EXCEEDED,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=f"exposure={exposure_fraction:.4f} <= max={policy.max_exposure_fraction:.4f}",
    )


def daily_loss_limit_not_breached(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Percentage loss against `portfolio.daily_opening_equity` — an
    explicit portfolio-state field rolled forward only by
    `orion.risk.portfolio.mark_to_market`, never guessed or reconstructed
    here (Mission 4 architecture decision B3). `>` (not `>=`) so a loss
    exactly at the limit still passes, matching this codebase's established
    inclusive-boundary convention (`risk_reward_meets_minimum`).
    """
    control = RiskControlName.DAILY_LOSS_LIMIT_NOT_BREACHED
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    loss_fraction = (
        portfolio.daily_opening_equity - portfolio.equity
    ) / portfolio.daily_opening_equity
    if loss_fraction > policy.max_daily_loss_fraction:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"daily loss={loss_fraction:.4f} > max={policy.max_daily_loss_fraction:.4f}",
            reason_code=RiskReasonCode.DAILY_LOSS_LIMIT_BREACHED,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=f"daily loss={loss_fraction:.4f} <= max={policy.max_daily_loss_fraction:.4f}",
    )


def weekly_loss_limit_not_breached(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """The weekly analogue of `daily_loss_limit_not_breached`, against
    `portfolio.weekly_opening_equity`."""
    control = RiskControlName.WEEKLY_LOSS_LIMIT_NOT_BREACHED
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    loss_fraction = (
        portfolio.weekly_opening_equity - portfolio.equity
    ) / portfolio.weekly_opening_equity
    if loss_fraction > policy.max_weekly_loss_fraction:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"weekly loss={loss_fraction:.4f} > max={policy.max_weekly_loss_fraction:.4f}",
            reason_code=RiskReasonCode.WEEKLY_LOSS_LIMIT_BREACHED,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=f"weekly loss={loss_fraction:.4f} <= max={policy.max_weekly_loss_fraction:.4f}",
    )


def max_drawdown_not_breached(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> ControlOutcome:
    """Percentage drop from `portfolio.equity_peak` — the all-time
    high-water mark, distinct from the daily/weekly opening-equity
    baselines above (drawdown never resets on a period boundary)."""
    control = RiskControlName.MAX_DRAWDOWN_NOT_BREACHED
    if not proposal.is_trade_proposal:
        return _not_applicable(control)
    drawdown = (portfolio.equity_peak - portfolio.equity) / portfolio.equity_peak
    if drawdown > policy.max_drawdown_fraction:
        return ControlOutcome(
            control=control,
            status=ControlStatus.FAILED,
            detail=f"drawdown={drawdown:.4f} > max={policy.max_drawdown_fraction:.4f}",
            reason_code=RiskReasonCode.DRAWDOWN_LIMIT_BREACHED,
        )
    return ControlOutcome(
        control=control,
        status=ControlStatus.PASSED,
        detail=f"drawdown={drawdown:.4f} <= max={policy.max_drawdown_fraction:.4f}",
    )


CONTROLS: tuple[Control, ...] = (
    proposal_was_not_upgraded,
    kill_switch_disengaged,
    evidence_is_fresh,
    decision_is_current,
    portfolio_state_is_fresh,
    no_conflicting_open_position,
    volatility_regime_permitted,
    trade_levels_are_coherent_with_price,
    risk_reward_meets_minimum,
    position_size_within_limits,
    exposure_within_limit,
    daily_loss_limit_not_breached,
    weekly_loss_limit_not_breached,
    max_drawdown_not_breached,
)


def evaluate_all(
    proposal: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> tuple[ControlOutcome, ...]:
    """Run every control in `CONTROLS`, in order. Never short-circuits."""
    return tuple(control(proposal, evidence, policy, portfolio, now) for control in CONTROLS)


def context_codes(proposal: ProposedDecision) -> tuple[RiskReasonCode, ...]:
    """Explanatory codes derived from the inputs, distinct from control
    failures — mirrors `orion.decision.rules.context_codes`. Distinguishes
    two different origins of a WAIT the guardian receives: the AI itself
    proposed WAIT, versus the Decision Engine downgraded a BUY/SELL proposal
    to WAIT before the guardian ever saw it. Never changes `action`; only
    explains it.
    """
    codes: list[RiskReasonCode] = []
    if proposal.reasoning_ref.action is Action.WAIT:
        codes.append(RiskReasonCode.AI_PROPOSED_WAIT)
    elif proposal.action is Action.WAIT:
        codes.append(RiskReasonCode.DECISION_ENGINE_DOWNGRADED_TO_WAIT)
    return tuple(codes)
