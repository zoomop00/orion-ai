"""Shared Evidence / ReasoningOutput builders for Decision Engine tests.

Kept out of the root `tests/conftest.py`, which only holds `Settings` and
clock fixtures — these builders are specific to this package's domain
objects.
"""

from __future__ import annotations

from datetime import UTC, datetime

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.analysis.indicators import IndicatorSnapshot
from orion.analysis.structure import StructureSnapshot, TrendDirection, VolatilityLevel
from orion.data.models import Timeframe
from orion.reasoning.schemas import Action, Direction, ReasonCode, ReasoningOutput, RiskLevel

BASE_TIME = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

# max(20, 50, 50, 14+1, 14+1) under the default indicator lengths below.
DEFAULT_REQUIRED_BARS = 50


def build_evidence(
    *,
    bar_count: int = 60,
    trend: TrendDirection = TrendDirection.BULLISH,
    rsi: float | None = 62.0,
    atr: float | None = 8.5,
    last_price: float = 2400.0,
) -> Evidence:
    return Evidence(
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        as_of=BASE_TIME,
        last_price=last_price,
        bar_count=bar_count,
        indicators=IndicatorSnapshot(
            ema_fast=2405.0,
            ema_slow=2390.0,
            sma_slow=2380.0,
            rsi=rsi,
            atr=atr,
            ema_fast_length=20,
            ema_slow_length=50,
            sma_length=50,
            rsi_length=14,
            atr_length=14,
        ),
        structure=StructureSnapshot(
            trend=trend,
            volatility=VolatilityLevel.MEDIUM,
            nearest_support=2385.0,
            nearest_resistance=2415.0,
            swing_high_count=3,
            swing_low_count=2,
        ),
    )


def build_reasoning(
    *,
    action: Action = Action.WAIT,
    direction: Direction = Direction.NONE,
    confidence: float = 0.6,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    insufficient_data: bool = False,
    reason_codes: list[ReasonCode] | None = None,
    rationale: str = "No clear edge in the current evidence.",
    model: str = "claude-opus-5-20260101",
    prompt_version: str = "2026-08-23.1",
    evidence: Evidence | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> ReasoningOutput:
    """`evidence` is the Evidence this reasoning claims to be about — defaults
    to the standard `build_evidence()` so a caller who doesn't pass one gets a
    ReasoningOutput that matches `build_evidence()` by construction. Pass a
    *different* Evidence than the one later given to `decide()`/`review()` to
    deliberately build an evidence/reasoning mismatch scenario.

    `stop_loss`/`take_profit` default to a comfortably-valid RR=2.0 geometry
    around `build_evidence()`'s default `last_price=2400.0` when the action
    is BUY/SELL and neither is explicitly given, so callers whose test isn't
    actually about trade levels don't have to think about them. Pass explicit
    values to test level-specific scenarios (invalid geometry, low R:R, a
    degenerate stop, etc.).
    """
    if action in (Action.BUY, Action.SELL) and stop_loss is None and take_profit is None:
        if direction is Direction.LONG:
            stop_loss, take_profit = 2385.0, 2430.0
        elif direction is Direction.SHORT:
            stop_loss, take_profit = 2415.0, 2370.0
    return ReasoningOutput(
        action=action,
        direction=direction,
        confidence=confidence,
        risk_level=risk_level,
        reason_codes=reason_codes or [ReasonCode.NO_CLEAR_SIGNAL],
        insufficient_data=insufficient_data,
        rationale=rationale,
        stop_loss=stop_loss,
        take_profit=take_profit,
        model=model,
        prompt_version=prompt_version,
        evidence_fingerprint=evidence_fingerprint(
            evidence if evidence is not None else build_evidence()
        ),
    )
