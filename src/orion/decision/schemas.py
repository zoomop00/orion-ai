"""Strict shapes for what the Decision Engine is allowed to say.

A `ProposedDecision` answers "what proposed decision follows from the
evidence + reasoning?" — nothing here evaluates whether that proposal is
allowed. See `orion.decision.engine` for the full Decision Engine / Risk
Guardian boundary; the Risk Guardian is a separate, not-yet-built layer that
gets the final say.

Provenance without a persistence layer: neither `Evidence` nor
`ReasoningOutput` carries its own id, and no orchestrator exists yet to mint
one. `EvidenceRef`/`ReasoningRef` reference their source objects with a
SHA-256 content hash instead of an id — same content always hashes the same,
so identical inputs are verifiably identical, which is what the Decision
Engine's determinism requirement needs. A hash is tamper-evidence and
cycle-linkage, not a stable long-term identifier: a schema change to
`Evidence`/`ReasoningOutput` changes the hash even though nothing was
tampered with.

`EvidenceRef.content_hash` is computed with `orion.analysis.evidence.
evidence_fingerprint`, and `ReasoningRef`/`decision_fingerprint` below with
`orion.provenance.canonical_hash` directly — the same underlying mechanism
every provenance hash in ORION uses, never a second independent
implementation. Reusing one function is what lets `orion.decision.engine`
verify a ReasoningOutput belongs to the Evidence it is paired with (and later,
`orion.risk.guardian` verify a ProposedDecision belongs to the Evidence it is
paired with) by simple equality, instead of trusting the pairing by
construction.

`reason_codes`/`rule_outcomes` are `tuple`, not `list`, unlike
`ReasoningVerdict.reason_codes`. `frozen=True` blocks attribute rebinding but
not `list.append`, and `ProposedDecision` is an audit record, never a
model-facing JSON schema, so genuine immutability wins here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.data.models import Timeframe
from orion.provenance import canonical_hash
from orion.reasoning.schemas import Action, Direction, ReasoningOutput, RiskLevel


class DecisionRuleName(StrEnum):
    """Stable identifier for each deterministic rule the engine evaluates.

    An enum, not the rule function's name, so a rule can be renamed or
    refactored without silently changing what a journaled `RuleOutcome`
    means.
    """

    AI_INSUFFICIENT_DATA_REQUIRES_WAIT = "ai_insufficient_data_requires_wait"
    PROPOSAL_IS_INTERNALLY_CONSISTENT = "proposal_is_internally_consistent"
    TRADE_REQUIRES_MINIMUM_HISTORY = "trade_requires_minimum_history"
    TRADE_REQUIRES_KNOWN_TREND = "trade_requires_known_trend"
    TRADE_REQUIRES_MOMENTUM_AND_VOLATILITY_EVIDENCE = (
        "trade_requires_momentum_and_volatility_evidence"
    )


class RuleStatus(StrEnum):
    """Three states, not a bool.

    Most rules only gate trade proposals. Recording a WAIT proposal as
    "passed" on a rule that never evaluated it would corrupt any future
    analysis of pass/fail rates — `NOT_APPLICABLE` keeps that honest.
    """

    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class DecisionReasonCode(StrEnum):
    """Closed vocabulary of *why* the engine reached this decision.

    Deliberately separate from `orion.reasoning.schemas.ReasonCode`: that
    enum describes what the evidence shows and is a closed contract with the
    LLM (it is the option list in the JSON schema sent to Claude) — engine
    outcomes like DOWNGRADED_TO_WAIT have no business appearing there, and
    would invite the model to emit them. Members here are prefixed by origin
    (`AI_*` vs `EVIDENCE_*`) so "did the AI say so, or did the engine detect
    it?" is never ambiguous in a journaled record.
    """

    # Aggregate outcome — always exactly one of these two is present.
    ALL_RULES_PASSED = "all_rules_passed"
    DOWNGRADED_TO_WAIT = "downgraded_to_wait"

    # Context — explanatory, derived from the inputs, never action-changing.
    AI_PROPOSED_WAIT = "ai_proposed_wait"
    AI_FLAGGED_INSUFFICIENT_DATA = "ai_flagged_insufficient_data"

    # Rule failures.
    INSUFFICIENT_DATA_WITH_TRADE_PROPOSAL = "insufficient_data_with_trade_proposal"
    REASONING_SELF_CONTRADICTORY = "reasoning_self_contradictory"
    EVIDENCE_HISTORY_TOO_THIN = "evidence_history_too_thin"
    EVIDENCE_TREND_UNKNOWN = "evidence_trend_unknown"
    EVIDENCE_INDICATORS_UNAVAILABLE = "evidence_indicators_unavailable"


class RuleOutcome(BaseModel):
    """One deterministic rule's verdict on one Evidence + ReasoningOutput pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: DecisionRuleName
    status: RuleStatus
    detail: str = Field(
        min_length=1,
        description="Short, deterministic description of the values that decided this "
        "outcome. Never a timestamp, object repr, or anything that varies run-to-run "
        "for identical inputs.",
    )
    reason_code: DecisionReasonCode | None = Field(
        default=None, description="The code this rule contributes when it fails."
    )

    @model_validator(mode="after")
    def _check_reason_code_matches_status(self) -> RuleOutcome:
        has_code = self.reason_code is not None
        if (self.status is RuleStatus.FAILED) != has_code:
            raise ValueError(
                f"status is {self.status.value!r} but reason_code is "
                f"{self.reason_code!r}; a failed rule must carry a reason_code and "
                "no other status may"
            )
        return self


class EvidenceRef(BaseModel):
    """A tamper-evident reference to the Evidence a decision was based on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    symbol: str
    timeframe: Timeframe
    as_of: datetime
    last_price: float
    bar_count: int

    @classmethod
    def from_evidence(cls, evidence: Evidence) -> EvidenceRef:
        return cls(
            content_hash=evidence_fingerprint(evidence),
            symbol=evidence.symbol,
            timeframe=evidence.timeframe,
            as_of=evidence.as_of,
            last_price=evidence.last_price,
            bar_count=evidence.bar_count,
        )


class ReasoningRef(BaseModel):
    """A tamper-evident reference to the ReasoningOutput a decision was based on.

    `rationale` is deliberately not carried here — excluding it keeps this
    record a closed object of enums, numbers, and hashes only, so no future
    reader can mistake model prose for a decision input. The prose is still
    covered by `content_hash` (computed over the full ReasoningOutput), so
    tampering with it is still detectable even though it is not displayed.

    `stop_loss`/`take_profit` are carried here (not excluded like `rationale`)
    because they are the one piece of the AI's proposal a downstream
    deterministic layer — `orion.risk`'s minimum risk/reward control — must
    actually read. `orion.risk.guardian` never accepts a `ReasoningOutput`
    directly, so without them here it would have no path to the levels at
    all; see `orion.risk.controls` for how they are validated (never trusted)
    and turned into a risk/reward ratio the Risk Guardian computes itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    action: Action
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel
    insufficient_data: bool
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)

    @classmethod
    def from_reasoning(cls, reasoning: ReasoningOutput) -> ReasoningRef:
        return cls(
            content_hash=canonical_hash(reasoning),
            model=reasoning.model,
            prompt_version=reasoning.prompt_version,
            action=reasoning.action,
            direction=reasoning.direction,
            confidence=reasoning.confidence,
            risk_level=reasoning.risk_level,
            insufficient_data=reasoning.insufficient_data,
            stop_loss=reasoning.stop_loss,
            take_profit=reasoning.take_profit,
        )


class ProposedDecision(BaseModel):
    """The Decision Engine's output: a proposal, not an authorization.

    Nothing downstream may execute on this directly — see the Decision
    Engine / Risk Guardian boundary in `orion.decision.engine`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(min_length=1)
    decided_at: datetime
    action: Action
    direction: Direction
    reason_codes: tuple[DecisionReasonCode, ...] = Field(min_length=1)
    rule_outcomes: tuple[RuleOutcome, ...] = Field(min_length=1)
    evidence_ref: EvidenceRef
    reasoning_ref: ReasoningRef
    engine_version: str = Field(min_length=1)

    @property
    def is_trade_proposal(self) -> bool:
        return self.action is not Action.WAIT

    @model_validator(mode="after")
    def _check_decision_invariants(self) -> ProposedDecision:
        any_failed = any(o.status is RuleStatus.FAILED for o in self.rule_outcomes)

        if any_failed and self.action is not Action.WAIT:
            raise ValueError(
                f"a rule failed but action is {self.action.value!r}; "
                "any failed rule requires action=WAIT"
            )
        if self.action is not self.reasoning_ref.action and not any_failed:
            raise ValueError(
                f"action ({self.action.value!r}) differs from the AI's proposal "
                f"({self.reasoning_ref.action.value!r}) but no rule failed to justify it; "
                "the engine may only narrow a proposal to WAIT, never invent one"
            )
        if self.action is Action.BUY and self.direction is not Direction.LONG:
            raise ValueError(
                f"action is BUY but direction is {self.direction.value!r}; BUY requires LONG"
            )
        if self.action is Action.SELL and self.direction is not Direction.SHORT:
            raise ValueError(
                f"action is SELL but direction is {self.direction.value!r}; SELL requires SHORT"
            )
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError(f"reason_codes contains duplicates: {self.reason_codes}")
        if len({o.rule for o in self.rule_outcomes}) != len(self.rule_outcomes):
            raise ValueError(f"rule_outcomes contains a duplicate rule: {self.rule_outcomes}")
        return self


def decision_fingerprint(decision: ProposedDecision) -> str:
    """A deterministic fingerprint of `decision`'s full content.

    Thin, domain-named wrapper around `orion.provenance.canonical_hash` —
    mirrors `orion.analysis.evidence.evidence_fingerprint` exactly, for the
    same reason: `orion.risk.guardian` needs to reference the exact
    `ProposedDecision` it reviewed in its own audit record, and a future
    journal (Mission 2) will need the identical value to join the two records
    without either object in memory.
    """
    return canonical_hash(decision)
