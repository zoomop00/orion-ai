"""Strict shapes for what the AI reasoning layer is allowed to say.

Everything the model can express is a closed enum or a bounded value — an AI
proposal that cannot be represented here cannot enter ORION at all. That is the
point: downstream code never parses free-form model prose, it reads validated
fields.

Two models, one on purpose:

* `ReasoningVerdict` is exactly the surface the model authors. Nothing here is
  provenance, so the model is never asked to name itself or version its own
  prompt — values it has no reliable way to know and every incentive to guess.
* `ReasoningOutput` is what leaves the reasoning layer: the verdict plus
  provenance the provider observed (`model`, `prompt_version`). Splitting them
  is what makes the audit trail trustworthy rather than self-reported.

None of this is a decision. A `ReasoningOutput` is a *proposal*, subordinate to
the deterministic Risk Guardian — see `orion.reasoning.provider_base`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Action(StrEnum):
    """What the model proposes doing right now.

    WAIT is a first-class result, not an error or a fallback — most snapshots
    genuinely do not contain a tradeable setup.
    """

    BUY = "buy"
    SELL = "sell"
    WAIT = "wait"


class Direction(StrEnum):
    """Directional bias.

    `BUY` requires `LONG` and `SELL` requires `SHORT` (enforced by
    `ReasoningVerdict._check_reasoning_invariants`). `WAIT` is unconstrained:
    `WAIT` + `LONG` or `WAIT` + `SHORT` is legitimate and expected — biased,
    no entry yet. `NONE` means no bias is held, which is different from
    holding a bias and declining to act on it.
    """

    LONG = "long"
    SHORT = "short"
    NONE = "none"


class RiskLevel(StrEnum):
    """The AI's own opinion of how risky the setup looks.

    Advisory only. It is not a risk limit, not a position size, and never
    overrides or substitutes for the deterministic Risk Guardian.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReasonCode(StrEnum):
    """Closed vocabulary of *why* a proposal was made.

    Deliberately an enum rather than free text so reasons are countable,
    groupable, and reviewable across thousands of past decisions — "how often
    did INSUFFICIENT_RR precede a skipped winner" is a query, not a text search.
    Codes describe evidence, never actions.
    """

    # Trend
    BULLISH_TREND = "bullish_trend"
    BEARISH_TREND = "bearish_trend"
    SIDEWAYS_TREND = "sideways_trend"
    TREND_UNKNOWN = "trend_unknown"

    # Momentum
    MOMENTUM_CONFIRMS = "momentum_confirms"
    MOMENTUM_DIVERGES = "momentum_diverges"
    MOMENTUM_NEUTRAL = "momentum_neutral"
    OVERBOUGHT = "overbought"
    OVERSOLD = "oversold"

    # Volatility regime
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    VOLATILITY_UNKNOWN = "volatility_unknown"

    # Structure / levels
    SUPPORT_NEARBY = "support_nearby"
    RESISTANCE_NEARBY = "resistance_nearby"
    ROOM_TO_TARGET = "room_to_target"
    INSUFFICIENT_RR = "insufficient_rr"
    NO_CLEAR_LEVELS = "no_clear_levels"

    # Evidence quality / abstention
    INSUFFICIENT_DATA = "insufficient_data"
    INDICATORS_UNAVAILABLE = "indicators_unavailable"
    THIN_HISTORY = "thin_history"
    CONFLICTING_SIGNALS = "conflicting_signals"
    NO_CLEAR_SIGNAL = "no_clear_signal"


class ReasoningVerdict(BaseModel):
    """The model-authored half of a proposal — the JSON schema sent to the API.

    Immutable and closed (`extra="forbid"`): an unexpected key is a failed call,
    not a field to ignore. Every constraint here is enforced *before* any caller
    sees the value, because the SDK validates the model's raw text against this
    schema and raises on mismatch rather than returning something partial.

    `stop_loss`/`take_profit` split validation across two layers, because this
    schema has no `last_price` to check against: the invariant here enforces
    only what is checkable without a price (both present for BUY/SELL, and
    ordered correctly relative to each other). Whether the levels actually
    sit on the right side of the current price is `orion.risk`'s job — see
    `orion.risk.controls.classify_trade_levels` — and the AI never reports a
    risk/reward ratio itself; the Risk Guardian always computes it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action = Field(description="Proposed action. WAIT is always a valid answer.")
    direction: Direction = Field(
        description="Directional bias. BUY requires LONG; SELL requires SHORT. WAIT may "
        "pair with LONG, SHORT, or NONE — WAIT+LONG means biased long with no entry yet."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Calibrated belief in this specific proposal, 0.0-1.0. Independent "
        "of insufficient_data: ample data can still yield low confidence.",
    )
    risk_level: RiskLevel = Field(
        description="The AI's own read of setup risk. Advisory; never authoritative."
    )
    reason_codes: list[ReasonCode] = Field(
        min_length=1,
        max_length=6,
        description="1-6 distinct codes from the fixed vocabulary, most important first. "
        "Only codes the evidence actually supports.",
    )
    insufficient_data: bool = Field(
        default=False,
        description="True when the evidence cannot support any directional view. "
        "Requires action=WAIT.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=400,
        description="Concise plain-language explanation of what in the evidence drove "
        "this call. No hedging filler, no restating the snapshot.",
    )
    stop_loss: float | None = Field(
        default=None,
        gt=0,
        description="Required for BUY/SELL: the price that invalidates this proposal. "
        "BUY: below take_profit. SELL: above take_profit. Always null for WAIT. Never "
        "self-report a risk/reward ratio — the Risk Guardian computes it from these "
        "levels and last_price, and may reject the proposal on that basis.",
    )
    take_profit: float | None = Field(
        default=None,
        gt=0,
        description="Required for BUY/SELL: the target price. BUY: above stop_loss. "
        "SELL: below stop_loss. Always null for WAIT.",
    )

    @model_validator(mode="after")
    def _check_reasoning_invariants(self) -> ReasoningVerdict:
        if self.insufficient_data and self.action is not Action.WAIT:
            raise ValueError(
                f"insufficient_data is True but action is {self.action.value!r}; "
                "data flagged as insufficient can only support WAIT"
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
        if self.action in (Action.BUY, Action.SELL):
            if self.stop_loss is None or self.take_profit is None:
                raise ValueError(
                    f"action is {self.action.value!r} but stop_loss/take_profit are "
                    f"{self.stop_loss!r}/{self.take_profit!r}; BUY/SELL require both"
                )
            if self.action is Action.BUY and self.stop_loss >= self.take_profit:
                raise ValueError(
                    f"action is BUY but stop_loss ({self.stop_loss}) is not below "
                    f"take_profit ({self.take_profit})"
                )
            if self.action is Action.SELL and self.stop_loss <= self.take_profit:
                raise ValueError(
                    f"action is SELL but stop_loss ({self.stop_loss}) is not above "
                    f"take_profit ({self.take_profit})"
                )
        elif self.stop_loss is not None or self.take_profit is not None:
            raise ValueError(
                f"action is {self.action.value!r} but stop_loss/take_profit are "
                f"{self.stop_loss!r}/{self.take_profit!r}; WAIT requires both to be null"
            )
        return self


class ReasoningOutput(ReasoningVerdict):
    """A validated AI proposal plus the provenance needed to audit it later.

    `model` is the identifier the API reported as having actually produced the
    output, not the one the request asked for — those can diverge (aliases,
    routing, fallbacks) and only the former is worth journaling.
    `prompt_version` pins which prompt template produced it, so a shift in
    decision quality can be traced to a prompt change.
    `evidence_fingerprint` is a SHA-256 hash (`orion.analysis.evidence.
    evidence_fingerprint`) of the exact Evidence the provider reasoned over.
    Like `model` and `prompt_version`, it is provider-observed, not
    model-authored: `ClaudeReasoningProvider` computes it from the Evidence
    object it was called with and stamps it here — the model is never asked
    to generate or self-report it, since it has no reliable way to and every
    incentive to guess. It exists so `orion.decision.engine` can verify a
    ReasoningOutput actually belongs to the Evidence it is being paired with,
    rather than trusting that pairing by construction.

    This is still a proposal. Nothing downstream may treat it as a decision.
    """

    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    evidence_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
