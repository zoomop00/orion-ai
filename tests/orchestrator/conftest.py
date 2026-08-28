"""Deterministic doubles for the orchestrator's four injected dependencies.

Every fake here implements the real ABC (`MarketDataProvider`,
`ReasoningProvider`, `PortfolioStore`) rather than a `unittest.mock`
stand-in, following the precedent `InMemoryAudioInput`, `InMemoryPortfolioStore`
and `InMemoryHistoricalBarSource` already set: a double that satisfies the
contract catches a signature change, and a `Mock` does not.

The bars are real `OHLCVBar`s on a real hourly grid, so `validate_snapshot`
runs its actual GAP/DUPLICATE/NON_MONOTONIC/STALE checks against them and
`compute_indicators` computes actual EMA/RSI/ATR values. Nothing in the
orchestrator's own path is stubbed — only the network boundaries are.

`FakePortfolioStore.save`/`initialize` raise `AssertionError` on purpose:
Phase 5.1 is read-only, and the cheapest possible proof of that is a store
that fails loudly the moment anything tries to write to it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.data.provider_base import MarketDataProvider
from orion.paper.store import PortfolioRecord, PortfolioStore, PortfolioStoreError
from orion.reasoning.provider_base import ReasoningProvider
from orion.reasoning.schemas import Action, Direction, ReasonCode, ReasoningOutput, RiskLevel
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import PortfolioState, initial_portfolio_state
from orion.voice.schemas import Transcript

# The timestamp of the most recent bar in every fixture snapshot.
LAST_BAR_TIME = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
# "Now" is a few minutes after the last bar closed: fresh by every threshold.
NOW = LAST_BAR_TIME + timedelta(minutes=5)

BAR_COUNT = 120
SYMBOL = "XAUUSD"
STARTING_BALANCE = 10_000.0


def make_bars(
    count: int = BAR_COUNT,
    *,
    end: datetime = LAST_BAR_TIME,
    interval: timedelta = timedelta(hours=1),
    start_price: float = 2400.0,
    step: float = 1.0,
) -> list[OHLCVBar]:
    """A contiguous, steadily rising hourly series.

    Rising on purpose: it produces a genuine BULLISH `TrendDirection` from
    the real `compute_structure`, so the decision path under test is the one
    with a trade proposal in it rather than the trivial "trend unknown"
    downgrade. The small deterministic zigzag on top of the trend keeps RSI
    off its 100.0 ceiling, so the momentum figure the spoken summary reads
    is an ordinary one rather than a degenerate edge case.
    """
    bars: list[OHLCVBar] = []
    for index in range(count):
        timestamp = end - interval * (count - 1 - index)
        close = start_price + index * step + (index % 5) * 0.4
        open_ = close - step / 2
        bars.append(
            OHLCVBar(
                timestamp=timestamp,
                open=open_,
                high=close + 1.0,
                low=open_ - 1.0,
                close=close,
                volume=1_000.0,
            )
        )
    return bars


def make_snapshot(
    *,
    symbol: str = SYMBOL,
    timeframe: Timeframe = Timeframe.H1,
    bars: list[OHLCVBar] | None = None,
    retrieved_at: datetime = NOW,
) -> MarketSnapshot:
    return MarketSnapshot(
        source=DataSourceMeta(
            provider="fake",
            symbol=symbol,
            timeframe=timeframe,
            retrieved_at=retrieved_at,
        ),
        bars=make_bars() if bars is None else bars,
    )


class FakeMarketDataProvider(MarketDataProvider):
    """Returns a fixed snapshot, or raises a caller-chosen error."""

    def __init__(
        self,
        snapshot: MarketSnapshot | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._error = error
        self.calls: list[tuple[str, Timeframe, int]] = []

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        self.calls.append((symbol, timeframe, output_size))
        if self._error is not None:
            raise self._error
        if self._snapshot is not None:
            return self._snapshot
        return make_snapshot(symbol=symbol, timeframe=timeframe)


class FakeReasoningProvider(ReasoningProvider):
    """Returns a fixed, schema-valid proposal stamped with the real
    fingerprint of whatever Evidence it was handed.

    Stamping the fingerprint here rather than hardcoding one is what makes
    `decide()`'s `EvidenceMismatchError` check a live part of these tests
    instead of something bypassed by the double.
    """

    def __init__(
        self,
        *,
        action: Action = Action.BUY,
        direction: Direction = Direction.LONG,
        confidence: float = 0.55,
        rationale: str = "Higher highs with momentum still short of overbought.",
        stop_loss: float | None = 2489.0,
        take_profit: float | None = 2600.0,
        insufficient_data: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._action = action
        self._direction = direction
        self._confidence = confidence
        self._rationale = rationale
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        self._insufficient_data = insufficient_data
        self._error = error
        self.calls: list[Evidence] = []

    def reason(self, evidence: Evidence) -> ReasoningOutput:
        self.calls.append(evidence)
        if self._error is not None:
            raise self._error
        return ReasoningOutput(
            action=self._action,
            direction=self._direction,
            confidence=self._confidence,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=[ReasonCode.BULLISH_TREND, ReasonCode.MOMENTUM_CONFIRMS],
            insufficient_data=self._insufficient_data,
            rationale=self._rationale,
            stop_loss=self._stop_loss,
            take_profit=self._take_profit,
            model="fake-reasoning-model",
            prompt_version="test.1",
            evidence_fingerprint=evidence_fingerprint(evidence),
        )


class FakePortfolioStore(PortfolioStore):
    """A readable paper account, an unreadable one, or one that is missing.

    Writing to it is a test failure by construction — Phase 5.1 never
    persists anything.
    """

    def __init__(
        self,
        state: PortfolioState | None = None,
        *,
        error: PortfolioStoreError | None = None,
    ) -> None:
        self._state = state
        self._error = error
        self.load_calls = 0

    def load(self) -> PortfolioRecord:
        self.load_calls += 1
        if self._error is not None:
            raise self._error
        if self._state is None:
            raise PortfolioStoreError("no PortfolioState has been initialized")
        return PortfolioRecord(state=self._state, revision=1)

    def save(self, state: PortfolioState, *, expected_revision: int) -> PortfolioRecord:
        raise AssertionError("the orchestrator must never write to the portfolio store")

    def initialize(self, state: PortfolioState) -> PortfolioRecord:
        raise AssertionError("the orchestrator must never initialize the portfolio store")


def make_portfolio(
    *,
    symbol: str = SYMBOL,
    as_of: datetime = LAST_BAR_TIME - timedelta(hours=1),
    starting_balance: float = STARTING_BALANCE,
) -> PortfolioState:
    return initial_portfolio_state(symbol=symbol, starting_balance=starting_balance, now=as_of)


def make_transcript(text: str, *, transcribed_at: datetime = NOW) -> Transcript:
    return Transcript(text=text, provider="test", transcribed_at=transcribed_at)


@pytest.fixture
def permissive_policy() -> RiskPolicy:
    """Kill switch disengaged, everything else at its real default — the
    only configuration under which an APPROVED verdict is reachable."""
    return RiskPolicy(kill_switch=KillSwitchState.DISENGAGED)


@pytest.fixture
def blocking_policy() -> RiskPolicy:
    """The fail-safe default: `RiskPolicy()` blocks every trade proposal."""
    return RiskPolicy()


@pytest.fixture
def market_data() -> FakeMarketDataProvider:
    return FakeMarketDataProvider()


@pytest.fixture
def reasoning_provider() -> FakeReasoningProvider:
    return FakeReasoningProvider()


@pytest.fixture
def portfolio_store() -> FakePortfolioStore:
    return FakePortfolioStore(make_portfolio())
