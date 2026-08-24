"""Shared builders for `orion.backtest` tests.

Reuses `tests.risk.conftest.build_policy` for `RiskPolicy` (same rationale
as `tests.paper.conftest`: a builder default that silently disagreed with
the real default would make every test that omits an argument exercise a
policy no operator would actually run) and `orion.analysis.evidence.
evidence_fingerprint` to stamp real, verifiable provenance on every scripted
`ReasoningOutput` -- these test doubles are otherwise the only place in this
suite that hand-builds a `ReasoningOutput`, since every other package's
tests build one through `tests.decision.conftest.build_reasoning`, which
assumes an already-known `Evidence` rather than one built from a rolling
window of real bars.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.data.models import OHLCVBar, Timeframe
from orion.reasoning.provider_base import ReasoningProvider, ReasoningProviderError
from orion.reasoning.schemas import Action, Direction, ReasonCode, ReasoningOutput, RiskLevel
from tests.risk.conftest import build_policy

__all__ = [
    "BASE_TIME",
    "H1",
    "SYMBOL",
    "build_policy",
    "make_uptrend_bars",
    "AlwaysWaitReasoningProvider",
    "FixedBuyReasoningProvider",
    "RaisingReasoningProvider",
]

BASE_TIME = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # a Monday -- no UTC week-boundary surprises
H1 = timedelta(hours=1)
SYMBOL = "XAUUSD"


def make_uptrend_bars(
    n: int,
    *,
    start: datetime = BASE_TIME,
    start_price: float = 2400.0,
    step: float = 2.0,
    interval: timedelta = H1,
) -> list[OHLCVBar]:
    """`n` strictly increasing, monotonically-timestamped H1 bars, `step`
    apart. Real bars run through the real `compute_indicators`/
    `compute_structure` -- deterministic, generous EMA/RSI/ATR separation
    (a persistent linear ramp reliably yields `TrendDirection.BULLISH` and
    low, stable `VolatilityLevel.LOW` volatility once >=50 bars exist), not
    hand-picked `IndicatorSnapshot`/`StructureSnapshot` values.
    """
    bars: list[OHLCVBar] = []
    prev_close = start_price - step
    for i in range(n):
        close = start_price + i * step
        open_ = prev_close
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        bars.append(
            OHLCVBar(
                timestamp=start + i * interval,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1_000.0,
            )
        )
        prev_close = close
    return bars


class AlwaysWaitReasoningProvider(ReasoningProvider):
    """Never proposes a trade -- every decision cycle in a backtest driven
    by this provider is a `NO_TRADE_PROPOSED` verdict, so `resolve_stop_
    take_profit_ohlc`/`execute`/`execute_delayed` are never reached and
    every processed bar goes through a genuine decision cycle (no position
    ever opens to skip one). Used by the no-look-ahead test, where every
    bar's decision cycle running independently -- never short-circuited by
    an open position -- is exactly the property under test.
    """

    def __init__(self) -> None:
        self.calls: list[Evidence] = []

    def reason(self, evidence: Evidence) -> ReasoningOutput:
        self.calls.append(evidence)
        return ReasoningOutput(
            action=Action.WAIT,
            direction=Direction.NONE,
            confidence=0.5,
            risk_level=RiskLevel.LOW,
            reason_codes=[ReasonCode.NO_CLEAR_SIGNAL],
            insufficient_data=False,
            rationale="Deterministic test double: never proposes a trade.",
            stop_loss=None,
            take_profit=None,
            model="test-always-wait-provider",
            prompt_version="test-1",
            evidence_fingerprint=evidence_fingerprint(evidence),
        )


class FixedBuyReasoningProvider(ReasoningProvider):
    """Always proposes a LONG entry `stop_offset`/`take_offset` away from
    `evidence.last_price` -- deterministic and evidence-derived (never a
    hardcoded price), so it stays coherent regardless of which bar it is
    called against. Whether the proposal actually becomes a trade is left
    entirely to the real Decision Engine/Risk Guardian (thin history is
    downgraded to WAIT, an unaffordable size is vetoed, etc.) -- this
    double only ever supplies the AI's opinion, never bypasses a
    deterministic gate.
    """

    def __init__(self, *, stop_offset: float = 30.0, take_offset: float = 60.0) -> None:
        self._stop_offset = stop_offset
        self._take_offset = take_offset
        self.calls: list[Evidence] = []

    def reason(self, evidence: Evidence) -> ReasoningOutput:
        self.calls.append(evidence)
        return ReasoningOutput(
            action=Action.BUY,
            direction=Direction.LONG,
            confidence=0.7,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=[ReasonCode.BULLISH_TREND, ReasonCode.MOMENTUM_CONFIRMS],
            insufficient_data=False,
            rationale="Deterministic test double: always proposes a fixed-offset LONG entry.",
            stop_loss=evidence.last_price - self._stop_offset,
            take_profit=evidence.last_price + self._take_offset,
            model="test-fixed-buy-provider",
            prompt_version="test-1",
            evidence_fingerprint=evidence_fingerprint(evidence),
        )


class RaisingReasoningProvider(ReasoningProvider):
    """Always raises `ReasoningProviderError` -- proves `run_backtest`
    propagates it uncaught rather than skipping the cycle or fabricating a
    WAIT."""

    def reason(self, evidence: Evidence) -> ReasoningOutput:
        raise ReasoningProviderError("test double: this provider always fails")


TIMEFRAME = Timeframe.H1
