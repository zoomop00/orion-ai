"""Tests for `orion.backtest.engine.run_backtest` -- the full Evidence ->
Reasoning -> Decision -> Risk Guardian -> Paper Execution loop over
historical bars.

Covers the adversarial requirements called out for Mission 6: no-look-ahead
(a structural proof, not just a spot check), determinism of repeated runs,
latency (`fill_delay_bars`) shifting the fill to the correct later bar and
price, position-sizing/affordability enforcement, drawdown/equity-curve
correctness on a losing trade, single-position enforcement, fail-closed
behavior on invalid arguments and bad historical data, uncaught propagation
of a `ReasoningProviderError`, and provenance linkage across every
recorded `BacktestCycle`.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from orion.analysis.evidence import evidence_fingerprint
from orion.backtest.data import InMemoryHistoricalBarSource
from orion.backtest.engine import BacktestError, run_backtest
from orion.backtest.schemas import BacktestResult
from orion.data.models import OHLCVBar, Timeframe
from orion.decision.schemas import decision_fingerprint
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason
from orion.reasoning.provider_base import ReasoningProvider, ReasoningProviderError
from orion.risk.policy import RiskPolicy
from orion.risk.schemas import RiskOutcome
from tests.backtest.conftest import (
    H1,
    SYMBOL,
    AlwaysWaitReasoningProvider,
    FixedBuyReasoningProvider,
    RaisingReasoningProvider,
    build_policy,
    make_uptrend_bars,
)

ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)
REAL_COST_POLICY = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
WINDOW_BARS = 50


def _run(
    bars: list[OHLCVBar],
    *,
    reasoning_provider: ReasoningProvider,
    policy: PaperExecutionPolicy = ZERO_COST_POLICY,
    risk_policy: RiskPolicy | None = None,
    starting_balance: float = 100_000.0,
    window_bars: int = WINDOW_BARS,
    fill_delay_bars: int = 0,
    end: datetime | None = None,
) -> BacktestResult:
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    return run_backtest(
        data_source=source,
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        start=bars[0].timestamp,
        end=end if end is not None else bars[-1].timestamp,
        reasoning_provider=reasoning_provider,
        risk_policy=risk_policy if risk_policy is not None else build_policy(),
        paper_policy=policy,
        starting_balance=starting_balance,
        window_bars=window_bars,
        fill_delay_bars=fill_delay_bars,
    )


# --- golden path: full trade lifecycle ---


def test_golden_path_trade_opens_and_reaches_take_profit() -> None:
    bars = make_uptrend_bars(85)
    result = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )

    assert len(result.fills) >= 1
    assert len(result.closed_positions) >= 1
    first_closed = result.closed_positions[0]
    assert first_closed.close_reason is CloseReason.TAKE_PROFIT_HIT
    assert first_closed.realized_pnl > 0
    assert len(result.equity_curve) == len(bars)


def test_before_minimum_history_every_cycle_is_no_trade_proposed() -> None:
    bars = make_uptrend_bars(60)
    result = _run(bars, reasoning_provider=FixedBuyReasoningProvider())
    early_cycles = [c for c in result.cycles if c.evidence.bar_count < WINDOW_BARS]
    assert early_cycles
    assert all(c.verdict.outcome is RiskOutcome.NO_TRADE_PROPOSED for c in early_cycles)
    assert all(c.decision.action.value == "wait" for c in early_cycles)


def test_never_trading_provider_produces_zero_fills() -> None:
    bars = make_uptrend_bars(70)
    result = _run(bars, reasoning_provider=AlwaysWaitReasoningProvider())
    assert result.fills == ()
    assert result.closed_positions == ()
    assert result.final_portfolio.open_position is None
    assert result.final_portfolio.cash == result.final_portfolio.starting_balance


# --- losing trade / drawdown ---


def test_losing_trade_reduces_equity_below_starting_balance() -> None:
    # A downtrend: EMA still separates cleanly (BEARISH), but the fixed-buy
    # provider proposes LONG anyway -- the AI is not required to be right,
    # only the mechanics of a losing trade are under test here.
    bars = make_uptrend_bars(85, start_price=2600.0, step=-2.0)
    result = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )
    assert len(result.closed_positions) >= 1
    first_closed = result.closed_positions[0]
    assert first_closed.close_reason is CloseReason.STOP_LOSS_HIT
    assert first_closed.realized_pnl < 0
    assert result.final_portfolio.equity < result.final_portfolio.starting_balance


# --- fees / spread / slippage ---


def test_realistic_costs_produce_a_positive_fee_and_a_worse_fill_price() -> None:
    bars = make_uptrend_bars(85)
    result = _run(
        bars,
        reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0),
        policy=REAL_COST_POLICY,
    )
    assert len(result.fills) >= 1
    opening_fill = result.fills[0]
    assert opening_fill.fee > 0
    # Opening a LONG under nonzero spread/slippage fills strictly above the
    # decision-cycle's own last_price -- worse for the buyer, by construction.
    opening_cycle = next(c for c in result.cycles if c.verdict.outcome is RiskOutcome.APPROVED)
    assert opening_fill.fill_price > opening_cycle.evidence.last_price


# --- position sizing / affordability enforcement ---


def test_unaffordable_stop_distance_is_vetoed_and_never_fills() -> None:
    bars = make_uptrend_bars(70)
    # `orion.risk.portfolio.compute_position_size`'s notional/equity ratio is
    # `risk_fraction * entry/stop_distance` -- a *tight* stop against a small
    # account computes a large size (small loss-per-unit means many units
    # fit inside the risk budget), which is exactly what makes it unaffordable.
    result = _run(
        bars,
        reasoning_provider=FixedBuyReasoningProvider(stop_offset=1.0, take_offset=2.0),
        starting_balance=1_000.0,
    )
    assert result.fills == ()
    trade_eligible = [c for c in result.cycles if c.evidence.bar_count >= WINDOW_BARS]
    assert trade_eligible
    assert all(c.verdict.outcome is RiskOutcome.VETOED for c in trade_eligible)


# --- single-position enforcement ---


def test_never_more_than_one_open_position_at_a_time() -> None:
    bars = make_uptrend_bars(85)
    result = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )
    # Every cycle that resulted in an APPROVED verdict must have been
    # evaluated while flat -- the engine only calls the decision cycle when
    # `portfolio.open_position is None`, so no verdict's own `portfolio_ref`
    # can ever describe an account already holding a position.
    for cycle in result.cycles:
        if cycle.verdict.outcome is RiskOutcome.APPROVED:
            assert cycle.verdict.portfolio_ref.has_open_position is False


# --- latency (fill_delay_bars) ---


def test_fill_delay_shifts_the_fill_to_the_later_bar_price_and_timestamp() -> None:
    bars = make_uptrend_bars(85)
    immediate = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )
    delayed = _run(
        bars,
        reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0),
        fill_delay_bars=3,
    )
    assert immediate.fills
    assert delayed.fills
    immediate_fill = immediate.fills[0]
    delayed_fill = delayed.fills[0]
    assert delayed_fill.filled_at == immediate_fill.filled_at + 3 * H1
    # The uptrend means 3 bars later the reference price is strictly higher.
    assert delayed_fill.fill_price > immediate_fill.fill_price


def test_fill_delay_bars_negative_raises() -> None:
    bars = make_uptrend_bars(60)
    with pytest.raises(BacktestError):
        _run(bars, reasoning_provider=AlwaysWaitReasoningProvider(), fill_delay_bars=-1)


# --- determinism ---


def test_identical_runs_produce_byte_identical_results() -> None:
    bars = make_uptrend_bars(85)
    result_a = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )
    result_b = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )
    assert result_a == result_b


# --- no-look-ahead ---


def test_shared_prefix_decisions_are_identical_regardless_of_divergent_future_bars() -> None:
    """Two datasets share the exact same first 60 bars and diverge sharply
    afterward (a crash vs. a rally). If the engine ever let a later bar
    influence an earlier decision, the shared-prefix cycles/equity points
    would differ between the two runs. `AlwaysWaitReasoningProvider` is used
    so a decision cycle runs on *every* bar (never skipped by an open
    position), making this the most direct structural proof available.
    """
    shared_prefix = make_uptrend_bars(60)
    crash_tail = make_uptrend_bars(
        30,
        start=shared_prefix[-1].timestamp + H1,
        start_price=shared_prefix[-1].close - 5.0,
        step=-20.0,
    )
    rally_tail = make_uptrend_bars(
        30,
        start=shared_prefix[-1].timestamp + H1,
        start_price=shared_prefix[-1].close + 5.0,
        step=50.0,
    )
    bars_a = shared_prefix + crash_tail
    bars_b = shared_prefix + rally_tail

    result_a = _run(bars_a, reasoning_provider=AlwaysWaitReasoningProvider())
    result_b = _run(bars_b, reasoning_provider=AlwaysWaitReasoningProvider())

    shared_cycles_a = [
        c for c in result_a.cycles if c.evidence.as_of <= shared_prefix[-1].timestamp
    ]
    shared_cycles_b = [
        c for c in result_b.cycles if c.evidence.as_of <= shared_prefix[-1].timestamp
    ]
    assert shared_cycles_a
    assert shared_cycles_a == shared_cycles_b

    shared_len = len(shared_prefix)
    assert result_a.equity_curve[:shared_len] == result_b.equity_curve[:shared_len]


# --- fail-closed: invalid arguments ---


def test_naive_start_raises() -> None:
    bars = make_uptrend_bars(60)
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    with pytest.raises(BacktestError):
        run_backtest(
            data_source=source,
            symbol=SYMBOL,
            timeframe=Timeframe.H1,
            start=bars[0].timestamp.replace(tzinfo=None),
            end=bars[-1].timestamp,
            reasoning_provider=AlwaysWaitReasoningProvider(),
            risk_policy=build_policy(),
            paper_policy=ZERO_COST_POLICY,
            starting_balance=100_000.0,
            window_bars=WINDOW_BARS,
        )


def test_end_before_start_raises() -> None:
    bars = make_uptrend_bars(60)
    with pytest.raises(BacktestError):
        _run(bars, reasoning_provider=AlwaysWaitReasoningProvider(), end=bars[0].timestamp - H1)


def test_window_bars_less_than_one_raises() -> None:
    bars = make_uptrend_bars(60)
    with pytest.raises(BacktestError):
        _run(bars, reasoning_provider=AlwaysWaitReasoningProvider(), window_bars=0)


# --- fail-closed: bad or missing historical data ---


def test_empty_range_raises_before_any_reasoning_call() -> None:
    bars = make_uptrend_bars(10)
    provider = AlwaysWaitReasoningProvider()
    far_future = bars[-1].timestamp + H1 * 1000
    # `start`/`end` both strictly past the dataset -- a well-formed range
    # that genuinely fetches zero bars.
    source = InMemoryHistoricalBarSource(symbol=SYMBOL, timeframe=Timeframe.H1, bars=bars)
    with pytest.raises(BacktestError):
        run_backtest(
            data_source=source,
            symbol=SYMBOL,
            timeframe=Timeframe.H1,
            start=far_future,
            end=far_future + H1 * 5,
            reasoning_provider=provider,
            risk_policy=build_policy(),
            paper_policy=ZERO_COST_POLICY,
            starting_balance=100_000.0,
            window_bars=WINDOW_BARS,
        )
    assert provider.calls == []


def test_gapped_data_fails_closed_before_any_reasoning_call() -> None:
    prefix = make_uptrend_bars(10)
    gapped_tail = make_uptrend_bars(
        10, start=prefix[-1].timestamp + H1 * 20, start_price=prefix[-1].close + 40.0
    )
    bars = prefix + gapped_tail
    provider = AlwaysWaitReasoningProvider()
    with pytest.raises(BacktestError, match="gap"):
        _run(bars, reasoning_provider=provider, window_bars=5)
    assert provider.calls == []


def test_non_monotonic_data_fails_closed() -> None:
    bars = make_uptrend_bars(10)
    scrambled = bars[:5] + [bars[6]] + [bars[5]] + bars[7:]
    provider = AlwaysWaitReasoningProvider()
    with pytest.raises(BacktestError):
        _run(scrambled, reasoning_provider=provider, window_bars=5)
    assert provider.calls == []


def test_duplicate_timestamps_fail_closed() -> None:
    bars = make_uptrend_bars(10)
    duplicated = bars[:5] + [bars[4]] + bars[5:]
    provider = AlwaysWaitReasoningProvider()
    with pytest.raises(BacktestError):
        _run(duplicated, reasoning_provider=provider, window_bars=5)
    assert provider.calls == []


# --- reasoning provider failure propagates uncaught ---


def test_reasoning_provider_error_propagates_uncaught() -> None:
    bars = make_uptrend_bars(60)
    with pytest.raises(ReasoningProviderError):
        _run(bars, reasoning_provider=RaisingReasoningProvider())


# --- provenance linkage ---


def test_every_cycle_is_internally_linked_by_its_own_fingerprints() -> None:
    bars = make_uptrend_bars(85)
    result = _run(
        bars, reasoning_provider=FixedBuyReasoningProvider(stop_offset=30.0, take_offset=60.0)
    )
    assert result.cycles
    for cycle in result.cycles:
        assert cycle.decision.evidence_ref.content_hash == evidence_fingerprint(cycle.evidence)
        assert cycle.verdict.decision_ref.content_hash == decision_fingerprint(cycle.decision)


def test_no_reasoning_call_ever_sees_a_future_bar_index_via_bar_count() -> None:
    """`evidence.bar_count` for the cycle evaluating bar `i` must never
    exceed `window_bars`, and must never reflect more bars than have been
    processed so far (`i + 1`) -- a direct, cheap corollary of the
    no-look-ahead property that every recorded cycle can be checked
    against without needing two full runs."""
    bars = make_uptrend_bars(70)
    result = _run(bars, reasoning_provider=AlwaysWaitReasoningProvider())
    by_time = {c.evidence.as_of: c for c in result.cycles}
    for i, bar in enumerate(bars):
        cycle = by_time.get(bar.timestamp)
        if cycle is None:
            continue
        assert cycle.evidence.bar_count <= WINDOW_BARS
        assert cycle.evidence.bar_count <= i + 1
