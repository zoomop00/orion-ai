"""Strict shapes for what the Risk Guardian is allowed to say.

A `RiskVerdict` answers "is this well-formed proposal permitted to be acted
on, at this moment, under these limits?" — see `orion.risk.guardian` for the
full Decision Engine / Risk Guardian boundary.

Own closed vocabulary, deliberately not reusing `orion.decision.schemas`'
`DecisionRuleName`/`DecisionReasonCode`/`RuleStatus`: each layer in ORION
owns its own reason-code/rule-name enum (`ReasonCode` for the AI,
`DecisionReasonCode` for the Decision Engine, `RiskReasonCode` here) so a
change to one layer's vocabulary can never silently alter what an
already-journaled record from another layer means.

`DecisionRef` deliberately does not carry `confidence` or `risk_level` from
the AI's proposal — those fields are simply not reachable from anything a
Risk Guardian control can read. This is the structural (not merely
conventional) enforcement of "AI confidence must never override a failed
risk control" and "AI risk_level is advisory only": a rule cannot read a
field this schema does not carry, no matter what the rule's code says.

`ExecutionMode` has exactly two members, `PAPER` and `NONE` — there is no
`LIVE` member. Adding live execution later requires a visible, reviewable
schema change, not a config flip.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.decision.schemas import ProposedDecision, decision_fingerprint
from orion.provenance import canonical_hash
from orion.reasoning.schemas import Action, Direction
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import PortfolioState


class RiskControlName(StrEnum):
    """Stable identifier for each deterministic control the guardian evaluates."""

    PROPOSAL_WAS_NOT_UPGRADED = "proposal_was_not_upgraded"
    KILL_SWITCH_DISENGAGED = "kill_switch_disengaged"
    EVIDENCE_IS_FRESH = "evidence_is_fresh"
    DECISION_IS_CURRENT = "decision_is_current"
    VOLATILITY_REGIME_PERMITTED = "volatility_regime_permitted"
    TRADE_LEVELS_ARE_COHERENT_WITH_PRICE = "trade_levels_are_coherent_with_price"
    RISK_REWARD_MEETS_MINIMUM = "risk_reward_meets_minimum"
    # --- Mission 4: portfolio-state-dependent controls ---
    PORTFOLIO_STATE_IS_FRESH = "portfolio_state_is_fresh"
    NO_CONFLICTING_OPEN_POSITION = "no_conflicting_open_position"
    POSITION_SIZE_WITHIN_LIMITS = "position_size_within_limits"
    EXPOSURE_WITHIN_LIMIT = "exposure_within_limit"
    DAILY_LOSS_LIMIT_NOT_BREACHED = "daily_loss_limit_not_breached"
    WEEKLY_LOSS_LIMIT_NOT_BREACHED = "weekly_loss_limit_not_breached"
    MAX_DRAWDOWN_NOT_BREACHED = "max_drawdown_not_breached"


class ControlStatus(StrEnum):
    """Three states, not a bool — matches `orion.decision.schemas.RuleStatus`'s
    reasoning exactly: most controls only gate trade proposals, and recording
    a WAIT proposal as "passed" on a control that never evaluated it would
    corrupt any future approval-rate analysis."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class RiskOutcome(StrEnum):
    """Three states, not a bool, for the same reason as `ControlStatus`:
    "a WAIT proposal was approved/vetoed" is a meaningless record that would
    corrupt approval-rate statistics. `NO_TRADE_PROPOSED` keeps it honest."""

    APPROVED = "approved"
    VETOED = "vetoed"
    NO_TRADE_PROPOSED = "no_trade_proposed"


class ExecutionMode(StrEnum):
    """No `LIVE` member exists. See the module docstring."""

    PAPER = "paper"
    NONE = "none"


class RiskReasonCode(StrEnum):
    """Closed vocabulary of *why* the guardian reached this verdict.

    Prefixed by origin, matching `DecisionReasonCode`'s convention, so a
    journal reader is never unsure who detected what.
    """

    # Aggregate outcome — always exactly one of these three is present.
    ALL_CONTROLS_PASSED = "all_controls_passed"
    VETOED_BY_RISK_CONTROL = "vetoed_by_risk_control"
    NO_TRADE_PROPOSED = "no_trade_proposed"

    # Context — explanatory, derived from the inputs, never action-changing.
    AI_PROPOSED_WAIT = "ai_proposed_wait"
    DECISION_ENGINE_DOWNGRADED_TO_WAIT = "decision_engine_downgraded_to_wait"

    # Control failures.
    PROPOSAL_NOT_AUTHORIZED_BY_ENGINE = "proposal_not_authorized_by_engine"
    KILL_SWITCH_ENGAGED = "kill_switch_engaged"
    EVIDENCE_STALE = "evidence_stale"
    EVIDENCE_TIMESTAMP_IN_FUTURE = "evidence_timestamp_in_future"
    DECISION_EXPIRED = "decision_expired"
    DECISION_TIMESTAMP_IN_FUTURE = "decision_timestamp_in_future"
    VOLATILITY_REGIME_BLOCKED = "volatility_regime_blocked"
    VOLATILITY_UNKNOWN = "volatility_unknown"
    TRADE_LEVELS_MISSING = "trade_levels_missing"
    DEGENERATE_STOP_DISTANCE = "degenerate_stop_distance"
    TRADE_LEVELS_INCOHERENT = "trade_levels_incoherent"
    RISK_REWARD_BELOW_MINIMUM = "risk_reward_below_minimum"
    # --- Mission 4: portfolio-state-dependent control failures ---
    PORTFOLIO_STALE = "portfolio_stale"
    PORTFOLIO_TIMESTAMP_IN_FUTURE = "portfolio_timestamp_in_future"
    POSITION_ALREADY_OPEN = "position_already_open"
    POSITION_SIZE_INVALID = "position_size_invalid"
    POSITION_SIZE_EXCEEDS_AVAILABLE_CASH = "position_size_exceeds_available_cash"
    EXPOSURE_LIMIT_EXCEEDED = "exposure_limit_exceeded"
    DAILY_LOSS_LIMIT_BREACHED = "daily_loss_limit_breached"
    WEEKLY_LOSS_LIMIT_BREACHED = "weekly_loss_limit_breached"
    DRAWDOWN_LIMIT_BREACHED = "drawdown_limit_breached"


class ControlOutcome(BaseModel):
    """One deterministic control's verdict on one ProposedDecision + Evidence
    + RiskPolicy triple."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    control: RiskControlName
    status: ControlStatus
    detail: str = Field(
        min_length=1,
        description="Short, deterministic description of the values that decided this "
        "outcome. Never a timestamp, object repr, or anything that varies run-to-run "
        "for identical inputs.",
    )
    reason_code: RiskReasonCode | None = Field(
        default=None, description="The code this control contributes when it fails."
    )

    @model_validator(mode="after")
    def _check_reason_code_matches_status(self) -> ControlOutcome:
        has_code = self.reason_code is not None
        if (self.status is ControlStatus.FAILED) != has_code:
            raise ValueError(
                f"status is {self.status.value!r} but reason_code is "
                f"{self.reason_code!r}; a failed control must carry a reason_code and "
                "no other status may"
            )
        return self


class DecisionRef(BaseModel):
    """A tamper-evident reference to the ProposedDecision a verdict was based on.

    Deliberately excludes `confidence` and `risk_level` — see the module
    docstring. `ai_action` (the AI's own proposed action, distinct from
    `action`/`direction` on `ProposedDecision`, which is the Decision
    Engine's already-possibly-narrowed action) is carried because a journal
    reader will constantly need to distinguish "the AI said WAIT" from "the
    AI proposed a trade and the Decision Engine downgraded it" — a question
    Mission 5's reflection loop asks routinely.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    analysis_id: str = Field(min_length=1)
    decided_at: datetime
    action: Action
    direction: Direction
    engine_version: str = Field(min_length=1)
    evidence_content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reasoning_content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    ai_action: Action

    @classmethod
    def from_proposal(cls, proposal: ProposedDecision) -> DecisionRef:
        return cls(
            content_hash=decision_fingerprint(proposal),
            analysis_id=proposal.analysis_id,
            decided_at=proposal.decided_at,
            action=proposal.action,
            direction=proposal.direction,
            engine_version=proposal.engine_version,
            evidence_content_hash=proposal.evidence_ref.content_hash,
            reasoning_content_hash=proposal.reasoning_ref.content_hash,
            ai_action=proposal.reasoning_ref.action,
        )


class RiskPolicyRef(BaseModel):
    """A tamper-evident record of which limits were in force for a verdict.

    Every threshold that governed the review, recorded on every single
    verdict — approved or not — so "what limits were in force when this was
    approved?" never requires guessing at what `.env` said at the time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    kill_switch: KillSwitchState
    max_staleness_bars: float
    max_decision_age_bars: float
    min_risk_reward: float
    blocked_volatility_levels: tuple[str, ...]

    @classmethod
    def from_policy(cls, policy: RiskPolicy) -> RiskPolicyRef:
        return cls(
            content_hash=canonical_hash(policy),
            kill_switch=policy.kill_switch,
            max_staleness_bars=policy.max_staleness_bars,
            max_decision_age_bars=policy.max_decision_age_bars,
            min_risk_reward=policy.min_risk_reward,
            blocked_volatility_levels=tuple(v.value for v in policy.blocked_volatility_levels),
        )


class PortfolioStateRef(BaseModel):
    """A tamper-evident reference to the `PortfolioState` a verdict was
    reviewed against.

    Mirrors `EvidenceRef`/`RiskPolicyRef` exactly. Every verdict carries
    one — approved or not — so "what did the account look like when this
    was decided?" never requires guessing at what `orion.paper` held at the
    time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    symbol: str
    as_of: datetime
    cash: float
    equity: float
    has_open_position: bool

    @classmethod
    def from_portfolio(cls, portfolio: PortfolioState) -> PortfolioStateRef:
        return cls(
            content_hash=canonical_hash(portfolio),
            symbol=portfolio.symbol,
            as_of=portfolio.as_of,
            cash=portfolio.cash,
            equity=portfolio.equity,
            has_open_position=portfolio.open_position is not None,
        )


class RiskVerdict(BaseModel):
    """The Risk Guardian's output: the final safety authority's answer.

    Nothing downstream may execute on this directly. `approved_execution_mode`
    is `PAPER`, never a live-trading signal — see the module docstring on
    `ExecutionMode`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(min_length=1)
    reviewed_at: datetime
    outcome: RiskOutcome
    action: Action
    direction: Direction
    approved_execution_mode: ExecutionMode
    reason_codes: tuple[RiskReasonCode, ...] = Field(min_length=1)
    control_outcomes: tuple[ControlOutcome, ...] = Field(min_length=1)
    decision_ref: DecisionRef
    policy_ref: RiskPolicyRef
    portfolio_ref: PortfolioStateRef
    guardian_version: str = Field(min_length=1)

    @property
    def approved(self) -> bool:
        return self.outcome is RiskOutcome.APPROVED

    @model_validator(mode="after")
    def _check_verdict_invariants(self) -> RiskVerdict:
        any_failed = any(c.status is ControlStatus.FAILED for c in self.control_outcomes)

        if any_failed and (self.action is not Action.WAIT or self.direction is not Direction.NONE):
            raise ValueError(
                f"a control failed but action/direction is "
                f"{self.action.value!r}/{self.direction.value!r}; any failed control "
                "requires action=WAIT and direction=NONE"
            )
        if self.decision_ref.action is Action.WAIT and self.action is not Action.WAIT:
            raise ValueError(
                f"decision_ref.action is WAIT but action is {self.action.value!r}; "
                "the guardian must never upgrade a WAIT proposal"
            )
        if self.action is not self.decision_ref.action and not any_failed:
            raise ValueError(
                f"action ({self.action.value!r}) differs from the Decision Engine's "
                f"proposal ({self.decision_ref.action.value!r}) but no control failed "
                "to justify it; the guardian may only narrow a proposal to WAIT, "
                "never invent one"
            )
        if self.action is Action.BUY and self.direction is not Direction.LONG:
            raise ValueError(
                f"action is BUY but direction is {self.direction.value!r}; BUY requires LONG"
            )
        if self.action is Action.SELL and self.direction is not Direction.SHORT:
            raise ValueError(
                f"action is SELL but direction is {self.direction.value!r}; SELL requires SHORT"
            )

        is_trade = self.decision_ref.action is not Action.WAIT
        if self.outcome is RiskOutcome.NO_TRADE_PROPOSED and is_trade:
            raise ValueError("outcome is NO_TRADE_PROPOSED but decision_ref.action is a trade")
        if self.outcome is not RiskOutcome.NO_TRADE_PROPOSED and not is_trade:
            raise ValueError(
                f"outcome is {self.outcome.value!r} but decision_ref.action is WAIT; "
                "a WAIT proposal can only ever be reviewed as NO_TRADE_PROPOSED"
            )
        if self.outcome is RiskOutcome.APPROVED and any_failed:
            raise ValueError("outcome is APPROVED but a control failed")
        if self.outcome is RiskOutcome.VETOED and not (is_trade and any_failed):
            raise ValueError("outcome is VETOED but no control failed on a trade proposal")

        expected_mode = (
            ExecutionMode.PAPER if self.outcome is RiskOutcome.APPROVED else ExecutionMode.NONE
        )
        if self.approved_execution_mode is not expected_mode:
            raise ValueError(
                f"approved_execution_mode is {self.approved_execution_mode.value!r} but "
                f"outcome is {self.outcome.value!r}; execution mode must be PAPER iff "
                "outcome is APPROVED, else NONE"
            )

        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError(f"reason_codes contains duplicates: {self.reason_codes}")
        if len({c.control for c in self.control_outcomes}) != len(self.control_outcomes):
            raise ValueError(
                f"control_outcomes contains a duplicate control: {self.control_outcomes}"
            )
        return self


def risk_verdict_fingerprint(verdict: RiskVerdict) -> str:
    """A deterministic fingerprint of `verdict`'s full content.

    Thin, domain-named wrapper around `orion.provenance.canonical_hash` —
    mirrors `orion.analysis.evidence.evidence_fingerprint` and
    `orion.decision.schemas.decision_fingerprint` exactly, per this
    codebase's rule that provenance hashing lives in one shared mechanism.
    Computed by the caller after `review()` returns; never stored as a
    field on `RiskVerdict` itself — a self-referencing hash field would
    need to exclude itself from its own input, which `canonical_hash` does
    not special-case for any model. `orion.paper` uses this to record, on
    every simulated `Order`/`Fill`, exactly which approved `RiskVerdict`
    authorized it — never an ad-hoc composite of `decision_ref`/
    `policy_ref`/`portfolio_ref` fields reassembled at the call site
    (Mission 4 architecture decision B8).
    """
    return canonical_hash(verdict)
