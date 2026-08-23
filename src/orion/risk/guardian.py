"""The Risk Guardian: the final deterministic safety authority.

Boundary with the Decision Engine: the Decision Engine answers "does a
well-formed trade proposal follow from this evidence and this reasoning?"
The Risk Guardian answers a different question — "is this well-formed
proposal permitted to be acted on, at this moment, under these limits?" The
Decision Engine narrows on epistemic grounds (the evidence does not support
the claim); the Risk Guardian narrows on prudential grounds (even granting
the claim, we will not act on it right now). Both can only ever narrow to
WAIT. Neither authorizes execution; there is no executor anywhere in this
codebase.

Max daily/weekly loss, max drawdown, max exposure, position size, and max
risk/trade all needed portfolio/account state that did not exist before
Mission 4. That state now exists (`PortfolioState`, `orion.risk.portfolio`)
and is a required parameter of `review()` below — never `Optional`, never
`None`-defaulted, matching every other precedent in this file (`policy`,
`now`). Correlation and liquidity controls remain deliberately not
implemented: they need multiple simultaneously-tracked instruments (ORION
trades one symbol) or real order-book depth data (not available from the
current provider), neither of which Mission 4 changes. Per the precedent in
`docs/DECISIONS.md` ("not building the code path at all is a stronger
guarantee than building it disabled"), these are not stubbed, not given a
`None`-defaulted parameter, and not given `RiskControlName` members here.

The guardian never accepts a `ReasoningOutput` directly — only a
`ProposedDecision`. Its frozen `reasoning_ref` (a `ReasoningRef`, see
`orion.decision.schemas`) carries the AI's action/direction/stop_loss/
take_profit, and — unlike `rationale` — also `confidence` and `risk_level`:
those two fields are present and directly readable as
`proposal.reasoning_ref.confidence`/`.risk_level` by any control function,
not structurally absent. `rationale` is the one field that genuinely has no
path into `ProposedDecision` at all — `ReasoningRef` never carries it, so
"Claude rationale must not be used as a risk signal" *is* a structural
guarantee. "AI confidence must never override a failed risk control" and
"AI risk_level is advisory only" are not: they hold only because no control
in `orion.risk.controls` reads either field today, a property enforced by
`tests/risk/test_guardian.py`'s `test_confidence_cannot_change_the_outcome`/
`test_risk_level_cannot_change_the_outcome`, not by this module's types. A
future control could read `proposal.reasoning_ref.confidence` and nothing
here would stop it at construction time — this remains a behavioral
invariant of this package's controls, not a structural one of its input.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.data.validation import TIMEFRAME_INTERVAL
from orion.decision.schemas import ProposedDecision
from orion.reasoning.schemas import Action, Direction
from orion.risk.controls import context_codes, evaluate_all
from orion.risk.policy import RiskPolicy
from orion.risk.portfolio import PortfolioState, expected_equity
from orion.risk.schemas import (
    ControlStatus,
    DecisionRef,
    ExecutionMode,
    PortfolioStateRef,
    RiskOutcome,
    RiskPolicyRef,
    RiskReasonCode,
    RiskVerdict,
)

GUARDIAN_VERSION = "2026-08-23.2"


class RiskGuardianError(RuntimeError):
    """Raised when the guardian cannot meaningfully evaluate a proposal.

    Distinct from a returned `RiskVerdict`: a verdict means the guardian
    evaluated the question and the answer is APPROVED, VETOED, or
    NO_TRADE_PROPOSED. This error means the guardian could not evaluate the
    question at all — a missing or malformed input, not market or portfolio
    caution. "I could not decide" is not "the verdict is a veto." Never
    caught internally and converted to a veto or a synthetic WAIT verdict —
    that would hide a broken input behind a stream of plausible-looking
    refusals, exactly what `orion.decision.engine.DecisionEngineError`
    already refuses to do one layer down.
    """


class EvidenceLinkageError(RiskGuardianError):
    """Raised when `evidence_fingerprint(evidence)` does not match
    `proposal.evidence_ref.content_hash`.

    The exact analogue of `orion.decision.engine.EvidenceMismatchError`, one
    layer up: nothing about `proposal` or `evidence` individually is invalid,
    but there is no basis to believe `proposal` concerns this `evidence`, so
    evaluating a control against them would be evaluating a coincidence, not
    a proposal. Deliberately not a subclass of `DecisionEngineError` — each
    layer in ORION owns its own top-level `RuntimeError`
    (`MarketDataProviderError`, `ReasoningProviderError`,
    `DecisionEngineError`, `RiskGuardianError`), never inheriting another
    layer's error hierarchy.
    """


class PortfolioLinkageError(RiskGuardianError):
    """Raised when `portfolio.symbol` does not match `evidence.symbol`.

    Mission 4's analogue of `EvidenceLinkageError`: nothing about
    `portfolio` or `evidence` individually is invalid, but there is no
    basis to believe this portfolio concerns this symbol, so evaluating a
    portfolio-dependent control against them would be evaluating a
    coincidence, not a proposal. This is an *integrity* mismatch and always
    raises (Mission 4 architecture decision B7) — distinct from ordinary
    portfolio *staleness* (`orion.risk.controls.portfolio_state_is_fresh`),
    which is a plausible-but-old input and fails closed as an auditable
    VETOED verdict, never a raise.
    """


class PortfolioIntegrityError(RiskGuardianError):
    """Raised when `portfolio.equity` does not equal `orion.risk.portfolio.
    expected_equity(portfolio.cash, portfolio.open_position, evidence.
    last_price)`.

    `PortfolioState.equity` is a plain frozen field — Pydantic has no way to
    validate it against `evidence.last_price` at construction time, since
    `PortfolioState` alone never carries a market price. That does not make
    it trusted caller-supplied truth: this check is what actually enforces
    "equity must not be silently treated as trusted caller-supplied truth"
    at the one point that *does* have both `portfolio` and `evidence` in
    hand — before any portfolio-dependent control (`exposure_within_limit`,
    `daily_loss_limit_not_breached`, `weekly_loss_limit_not_breached`,
    `max_drawdown_not_breached`, `position_size_within_limits`) ever reads
    `portfolio.equity`. A mismatch means either `portfolio` was never
    produced by `orion.risk.portfolio.mark_to_market`/`initial_portfolio_
    state`, or it was marked to a different price than `evidence.
    last_price` — either way, there is no basis to trust the equity number
    those controls would otherwise rely on, so this raises rather than
    laundering a fabricated figure into a plausible-looking veto or
    approval. The same integrity-vs-staleness split as `PortfolioLinkageError`:
    this is *always* a raise, never a control-level veto.
    """


def review(
    proposal: ProposedDecision,
    evidence: Evidence,
    *,
    policy: RiskPolicy,
    portfolio: PortfolioState,
    now: datetime,
) -> RiskVerdict:
    """Decide whether `proposal` is permitted to be acted on, right now,
    under `policy` and `portfolio`.

    Pure function: no network, filesystem, or randomness, and no clock or
    configuration beyond what is explicitly passed in. Identical arguments
    always produce an identical (save for object identity) `RiskVerdict`.

    `policy`, `portfolio`, and `now` are required, caller-supplied values —
    never read from a module-level singleton or minted internally — for the
    same reason `orion.decision.engine.decide` requires `analysis_id`/`now`:
    internal state or an internal clock would make two calls with the same
    proposal produce different verdicts depending on unobservable prior
    state, breaking the determinism guarantee above and making a journaled
    verdict unreproducible from its own recorded inputs. Build a fresh
    `RiskPolicy` once per analysis cycle
    (`RiskPolicy.from_settings(get_settings())`) — `get_settings()` is
    deliberately uncached — rather than caching one for the process
    lifetime; that is what gives an operator editing `.env` near-real-time
    kill-switch response with zero new state. `portfolio` is never
    `Optional` and never `None`-defaulted: a missing portfolio snapshot is
    a caller error, not a "flat account" default (Mission 4 architecture
    decision B7 in `docs/DECISIONS.md`).

    Raises RiskGuardianError if `proposal`/`evidence`/`policy`/`portfolio`
    is not a valid instance of its expected type, if `now`/`proposal.
    decided_at`/`evidence.as_of`/`portfolio.as_of`/`portfolio.
    daily_period_start`/`portfolio.weekly_period_start` is a naive
    (timezone-unaware) datetime, if `proposal.analysis_id` is blank, if
    `evidence.last_price` is not positive, or if `evidence.timeframe` has
    no known bar interval. Raises the more specific EvidenceLinkageError if
    `proposal.evidence_ref` does not match `evidence`, PortfolioLinkageError
    if `portfolio.symbol` does not match `evidence.symbol`, or
    PortfolioIntegrityError if `portfolio.equity` does not equal `cash` plus
    the unrealized P&L of `open_position` at `evidence.last_price` — the
    check that keeps every portfolio-dependent control from ever trusting a
    fabricated or evidence-stale equity figure. Otherwise always returns a
    RiskVerdict, including when every
    control fails: that case is an auditable VETOED verdict, never an
    exception, because a veto must remain a first-class, fully auditable
    outcome — see the module docstring's "I could not decide" distinction.
    Ordinary portfolio staleness is one such control failure, not a raise —
    see `orion.risk.controls.portfolio_state_is_fresh`.
    """
    if not isinstance(proposal, ProposedDecision):
        raise RiskGuardianError(
            f"proposal must be a ProposedDecision instance, got {type(proposal)!r}"
        )
    if not isinstance(evidence, Evidence):
        raise RiskGuardianError(f"evidence must be an Evidence instance, got {type(evidence)!r}")
    if not isinstance(policy, RiskPolicy):
        raise RiskGuardianError(f"policy must be a RiskPolicy instance, got {type(policy)!r}")
    if not isinstance(portfolio, PortfolioState):
        raise RiskGuardianError(
            f"portfolio must be a PortfolioState instance, got {type(portfolio)!r}"
        )
    if now.tzinfo is None:
        raise RiskGuardianError("now must be a timezone-aware datetime")
    if proposal.decided_at.tzinfo is None:
        raise RiskGuardianError("proposal.decided_at must be a timezone-aware datetime")
    if evidence.as_of.tzinfo is None:
        raise RiskGuardianError("evidence.as_of must be a timezone-aware datetime")
    if not proposal.analysis_id.strip():
        raise RiskGuardianError("proposal.analysis_id must be a non-empty string")
    if evidence.last_price <= 0:
        raise RiskGuardianError(f"evidence.last_price must be positive, got {evidence.last_price}")
    if evidence.timeframe not in TIMEFRAME_INTERVAL:
        raise RiskGuardianError(f"no known bar interval for timeframe {evidence.timeframe!r}")
    if evidence_fingerprint(evidence) != proposal.evidence_ref.content_hash:
        raise EvidenceLinkageError(
            "proposal.evidence_ref does not match the Evidence passed to review(); "
            "cannot establish that this proposal was produced from this evidence"
        )
    if portfolio.as_of.tzinfo is None:
        raise RiskGuardianError("portfolio.as_of must be a timezone-aware datetime")
    if portfolio.daily_period_start.tzinfo is None:
        raise RiskGuardianError("portfolio.daily_period_start must be a timezone-aware datetime")
    if portfolio.weekly_period_start.tzinfo is None:
        raise RiskGuardianError("portfolio.weekly_period_start must be a timezone-aware datetime")
    if portfolio.symbol != evidence.symbol:
        raise PortfolioLinkageError(
            f"portfolio.symbol ({portfolio.symbol!r}) does not match evidence.symbol "
            f"({evidence.symbol!r}); cannot establish that this portfolio concerns this symbol"
        )
    verified_equity = expected_equity(portfolio.cash, portfolio.open_position, evidence.last_price)
    if portfolio.equity != verified_equity:
        raise PortfolioIntegrityError(
            f"portfolio.equity ({portfolio.equity}) does not equal cash + unrealized P&L at "
            f"evidence.last_price ({verified_equity}); portfolio was not produced by "
            "mark_to_market against this evidence, or its equity was tampered with"
        )

    outcomes = evaluate_all(proposal, evidence, policy, portfolio, now)
    any_failed = any(o.status is ControlStatus.FAILED for o in outcomes)
    decision_is_wait = proposal.action is Action.WAIT

    if decision_is_wait:
        action, direction = Action.WAIT, Direction.NONE
        outcome = RiskOutcome.NO_TRADE_PROPOSED
    elif any_failed:
        action, direction = Action.WAIT, Direction.NONE
        outcome = RiskOutcome.VETOED
    else:
        action, direction = proposal.action, proposal.direction
        outcome = RiskOutcome.APPROVED

    approved_execution_mode = (
        ExecutionMode.PAPER if outcome is RiskOutcome.APPROVED else ExecutionMode.NONE
    )

    reason_codes: list[RiskReasonCode] = [
        {
            RiskOutcome.APPROVED: RiskReasonCode.ALL_CONTROLS_PASSED,
            RiskOutcome.VETOED: RiskReasonCode.VETOED_BY_RISK_CONTROL,
            RiskOutcome.NO_TRADE_PROPOSED: RiskReasonCode.NO_TRADE_PROPOSED,
        }[outcome]
    ]
    for o in outcomes:
        if o.reason_code is not None and o.reason_code not in reason_codes:
            reason_codes.append(o.reason_code)
    for code in context_codes(proposal):
        if code not in reason_codes:
            reason_codes.append(code)

    return RiskVerdict(
        analysis_id=proposal.analysis_id,
        reviewed_at=now,
        outcome=outcome,
        action=action,
        direction=direction,
        approved_execution_mode=approved_execution_mode,
        reason_codes=tuple(reason_codes),
        control_outcomes=outcomes,
        decision_ref=DecisionRef.from_proposal(proposal),
        policy_ref=RiskPolicyRef.from_policy(policy),
        portfolio_ref=PortfolioStateRef.from_portfolio(portfolio),
        guardian_version=GUARDIAN_VERSION,
    )
