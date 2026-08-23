"""Shared ProposedDecision / RiskPolicy builders for Risk Guardian tests.

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
from tests.decision.conftest import (
    BASE_TIME,
    DEFAULT_REQUIRED_BARS,
    build_evidence,
    build_reasoning,
)

__all__ = [
    "BASE_TIME",
    "DEFAULT_REQUIRED_BARS",
    "build_evidence",
    "build_reasoning",
    "build_proposal",
    "build_policy",
]


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
    """
    ev = evidence if evidence is not None else build_evidence()
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
) -> RiskPolicy:
    """Kill switch defaults to DISENGAGED here (unlike `RiskPolicy`'s own
    fail-safe ENGAGED default) so most tests can focus on the control under
    test without every one of them needing to explicitly disengage it — the
    fail-safe-default behavior itself is tested directly against a bare
    `RiskPolicy()`, not through this builder. `blocked_volatility_levels`
    defaults to `RiskPolicy`'s own default (only UNKNOWN blocked), not an
    empty tuple — a builder default that silently disagreed with the real
    default would make every test that omits this argument exercise a
    volatility policy no operator would actually run.
    """
    return RiskPolicy(
        kill_switch=kill_switch,
        max_staleness_bars=max_staleness_bars,
        max_decision_age_bars=max_decision_age_bars,
        min_risk_reward=min_risk_reward,
        blocked_volatility_levels=blocked_volatility_levels,
    )
