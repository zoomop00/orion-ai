"""Objective, deterministic facts about one closed paper trade: what was
predicted, what actually happened, and the raw deltas between them.

`orion.reflection` deliberately never computes a quality score, a good/bad
verdict, or anything a caller could wire up to automatically adjust
`RiskPolicy`, reasoning prompts, or trading behavior — see
`orion.reflection.analysis`'s module docstring and `docs/DECISIONS.md`'s
Mission 5 architecture entry for why. `TradeReflection` is the complete
public surface: every field here is a fact recomputed or copied directly
from an already-journaled, already-fingerprinted record — nothing here is
this module's own judgment.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.paper.schemas import CloseReason
from orion.reasoning.schemas import Action, Direction, RiskLevel
from orion.thesis.schemas import MarketThesis

_FINGERPRINT_FIELD = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class TradeReflection(BaseModel):
    """The prediction-vs-outcome facts for one closed trade, traced back
    through `orion.memory.journal.DecisionJournal` to the exact
    `ClosedPosition` / `RiskVerdict` / `ProposedDecision` it was computed
    from. Frozen and closed (`extra="forbid"`), matching every other schema
    in this codebase: an unexpected field is a construction bug, not
    something to silently ignore. Never persisted by `orion.reflection`
    itself in this mission — see `docs/DECISIONS.md`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- provenance: every fingerprint this reflection was derived from,
    # so any consumer can independently re-fetch and re-verify each one via
    # DecisionJournal.get_closed_position/get_verdict/get_decision ---
    closed_position_fingerprint: str = _FINGERPRINT_FIELD
    opened_by_verdict_fingerprint: str = _FINGERPRINT_FIELD
    decision_fingerprint: str = _FINGERPRINT_FIELD

    # --- prediction, from ProposedDecision.reasoning_ref/evidence_ref ---
    predicted_action: Action
    predicted_direction: Direction
    predicted_confidence: float = Field(ge=0.0, le=1.0)
    predicted_risk_level: RiskLevel
    predicted_stop_loss: float = Field(gt=0)
    predicted_take_profit: float = Field(gt=0)
    predicted_risk_reward: float = Field(
        gt=0,
        description="reward/risk computed against evidence_ref.last_price (the reference "
        "price orion.risk.controls.risk_reward_meets_minimum itself evaluated at approval "
        "time) — not the actual fill price below, which includes spread/slippage.",
    )

    # --- policy context, from RiskVerdict.policy_ref ---
    policy_min_risk_reward: float
    approval_margin: float = Field(
        description="predicted_risk_reward - policy_min_risk_reward: how far above the "
        "approval bar this trade's predicted geometry actually was. A fact, not a verdict — "
        "a thin margin does not mean the decision was wrong, and a wide one does not mean "
        "it was right; it is one input a human weighs alongside the others here."
    )

    # --- actual outcome, from ClosedPosition, optionally enriched by Fill ---
    close_reason: CloseReason
    entry_price: float = Field(gt=0, description="the actual fill price that opened the position.")
    exit_price: float = Field(gt=0)
    realized_pnl: float
    closing_fee: float = Field(ge=0)
    opening_fee: float | None = Field(
        default=None,
        ge=0,
        description="the opening Fill's fee, when exactly one Fill could be matched to this "
        "ClosedPosition; None if no Fill was found. Optional enrichment, never required for "
        "a valid TradeReflection.",
    )

    # --- difference ---
    exit_matched_predicted_take_profit: bool
    exit_matched_predicted_stop_loss: bool

    # --- what the specialists said, if it was recorded ---
    thesis: MarketThesis | None = Field(
        default=None,
        description="The MarketThesis journaled for the same analysis this trade came "
        "from, when exactly one was found. None when none was recorded -- every trade "
        "opened before Mission 8 v2.2 is permanently in that state, and so is one whose "
        "analysis formed no thesis. Optional enrichment, never required for a valid "
        "TradeReflection, exactly like `opening_fee`.\n\n"
        "IT IS EVIDENCE, NOT A SCORE. Reflection reports what the specialists observed "
        "beside what happened and draws no conclusion from the pair -- the same rule that "
        "keeps every other field here a fact. A thesis that read bullish before a losing "
        "trade says nothing on its own about whether the decision was wrong.",
    )

    reflected_at: datetime = Field(
        description="when this TradeReflection was computed — caller-supplied, never "
        "datetime.now(). Distinct from every other timestamp on this record, which all "
        "describe when something in the trade itself happened."
    )

    @model_validator(mode="after")
    def _check_exit_match_matches_close_reason(self) -> TradeReflection:
        # Restated defense-in-depth: reflect_on_closed_position always
        # derives both booleans directly from close_reason, so this can
        # only fail if TradeReflection is constructed some other way with
        # inconsistent fields — the same category of check this codebase
        # already applies pervasively (RuleOutcome, ControlOutcome).
        if self.exit_matched_predicted_take_profit != (
            self.close_reason is CloseReason.TAKE_PROFIT_HIT
        ):
            raise ValueError("exit_matched_predicted_take_profit does not agree with close_reason")
        if self.exit_matched_predicted_stop_loss != (
            self.close_reason is CloseReason.STOP_LOSS_HIT
        ):
            raise ValueError("exit_matched_predicted_stop_loss does not agree with close_reason")
        return self


class ReflectionBatch(BaseModel):
    """Every `TradeReflection` that could be computed for one symbol's most
    recent closed trades, plus an honest account of the ones that could not.

    THE SECOND TUPLE IS THE POINT. A `ClosedPosition` whose `RiskVerdict` or
    `ProposedDecision` is not in the journal cannot be reflected on — Mission 2
    deliberately does not enforce cross-record write ordering, and every
    position opened before the forward cycle began journaling its authorising
    chain is permanently in that state. Silently dropping those would make a
    journal with ten closed trades and two reflectable ones look like a journal
    with two trades. Raising on the first one would make a single legacy trade
    hide every later reflection. So they are listed by fingerprint, which is
    enough for a reader to fetch the record and see for themselves.

    `DecisionJournalIntegrityError` is never collected here — a corrupted
    record is not an incomplete chain, and it propagates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    reflections: tuple[TradeReflection, ...] = Field(
        description="oldest closed trade first, matching the journal's own ordering."
    )
    unreflectable_fingerprints: tuple[str, ...] = Field(
        description="ClosedPositions in the requested window whose chain could not be "
        "walked. Fingerprints only: this schema reports that a reflection is impossible, "
        "never a guess at what it would have said."
    )
    total_closed_positions: int = Field(
        ge=0,
        description="how many ClosedPositions the journal holds for this symbol in total, "
        "before any limit was applied — so a caller can tell a short answer from an "
        "exhausted one.",
    )
    reflected_at: datetime = Field(
        description="caller-supplied, never datetime.now(); the same instant stamped on "
        "every TradeReflection above."
    )

    @model_validator(mode="after")
    def _check_window_is_accounted_for(self) -> ReflectionBatch:
        # Every ClosedPosition examined ends up in exactly one of the two
        # tuples, so a future change that drops one on the floor fails here
        # rather than producing a quietly short answer.
        examined = len(self.reflections) + len(self.unreflectable_fingerprints)
        if examined > self.total_closed_positions:
            raise ValueError(
                f"{examined} closed positions were examined but the journal holds only "
                f"{self.total_closed_positions} for {self.symbol!r}"
            )
        return self
