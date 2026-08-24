"""The Mission 6 backtest engine: one deterministic loop over historical
bars, wiring together existing pure functions — it reimplements none of
them.

`run_backtest` is the single reusable primitive (M6-Q5, approved): out-of-
sample runs, walk-forward analysis, regime comparisons, sensitivity
analysis, and stress tests are all just this same function called
repeatedly with different `start`/`end`/policy arguments — nothing here
builds a separate wrapper for any of them, and none is added in this
mission.

Determinism: every `now` passed to `build_evidence`/`decide`/`review`/
`execute`/`resolve_stop_take_profit_ohlc` is derived directly from the bar
being processed — never `datetime.now()`. `analysis_id` is derived
deterministically from `symbol` and the bar's own timestamp — never
`uuid4()`. Identical `(data_source, symbol, timeframe, start, end,
reasoning_provider, risk_policy, paper_policy, ...)` arguments always
produce a byte-identical `BacktestResult`, given a deterministic
`reasoning_provider` (the engine cannot make a caller-supplied
`ReasoningProvider` deterministic if it isn't one — that responsibility
belongs to whoever implements it, exactly as it already does for every
other consumer of `orion.reasoning.provider_base.ReasoningProvider`).

No look-ahead: at bar index `i`, the Evidence built for that cycle is a
window into `bars[: i + 1]` only — `bars[i + 1 :]` is never read, imported,
or referenced anywhere in this module while processing bar `i`. This is a
structural property of `_window_for` below, not a runtime check — proven
by a dedicated adversarial test that mutates future bars after a decision
and confirms zero effect.

Fail-closed, not fail-soft: `ReasoningProviderError`/`DecisionEngineError`/
`RiskGuardianError`/`PaperExecutionError` raised by any reused pure function
are never caught here — they propagate and abort the run. A backtest that
silently skipped a cycle on an unexpected error would produce a result that
*looks* complete but silently missed data, exactly the kind of
misleading-by-omission failure this codebase never allows anywhere else.

No Mission 2 persistence, no Mission 5 changes: this module never imports
`orion.memory` or `orion.reflection`. `BacktestResult` is returned in
memory (M6-Q4); a caller who wants either integration builds their own
disposable `DecisionJournal` from the result's contents afterward.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from orion.analysis.evidence import build_evidence
from orion.backtest.data import HistoricalBarSource
from orion.backtest.execution import execute_delayed, resolve_stop_take_profit_ohlc
from orion.backtest.schemas import BacktestCycle, BacktestResult, EquityPoint
from orion.data.models import MarketSnapshot, OHLCVBar, Timeframe
from orion.data.validation import validate_snapshot
from orion.decision.engine import decide
from orion.decision.schemas import ProposedDecision
from orion.paper.execution import execute
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, Fill
from orion.reasoning.provider_base import ReasoningProvider
from orion.risk.guardian import review
from orion.risk.policy import RiskPolicy
from orion.risk.portfolio import PortfolioState, compute_position_size, initial_portfolio_state
from orion.risk.schemas import RiskOutcome, RiskVerdict

ENGINE_VERSION = "2026-08-24.1"


class BacktestError(RuntimeError):
    """Raised when a backtest cannot run at all — invalid arguments, or a
    historical dataset that fails `orion.data.validation.validate_snapshot`.
    Own top-level hierarchy, matching this codebase's "every layer owns its
    own RuntimeError" rule. Never caught internally and turned into a
    partial result — an invalid backtest must fail closed, not produce a
    result that looks complete.
    """


@dataclass
class _PendingFill:
    """A verdict APPROVED at `approved_at_index`, awaiting its delayed
    fill at `fill_at_index` (`fill_delay_bars` bars later — see M6-Q3).
    Plain mutable container, not a Pydantic model: purely internal loop
    state, never returned or journaled."""

    proposal: ProposedDecision
    verdict: RiskVerdict
    quantity: float
    fill_at_index: int


def _window_for(bars: list[OHLCVBar], i: int, window_bars: int) -> list[OHLCVBar]:
    """The bars visible to a decision cycle evaluating index `i`: the most
    recent `window_bars` bars ending at and including `i`. Never includes
    `bars[j]` for `j > i` — the structural no-look-ahead guarantee every
    other guarantee in this module depends on."""
    start_index = max(0, i - window_bars + 1)
    return bars[start_index : i + 1]


def run_backtest(
    *,
    data_source: HistoricalBarSource,
    symbol: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    reasoning_provider: ReasoningProvider,
    risk_policy: RiskPolicy,
    paper_policy: PaperExecutionPolicy,
    starting_balance: float,
    window_bars: int,
    fill_delay_bars: int = 0,
) -> BacktestResult:
    """Run one deterministic backtest of the full Evidence -> Reasoning ->
    Decision -> Risk Guardian -> Paper Execution chain over historical
    bars in `[start, end]`, single-symbol, single-position (matching the
    live system's own B2 constraint exactly — see `docs/DECISIONS.md`).

    `window_bars` is the fixed-size rolling lookback each cycle's Evidence
    is built from — matching how `orion.data.provider_base.
    MarketDataProvider.fetch_ohlcv`'s `output_size` already works for live
    fetching (a fixed recent-N-bars window, not "everything since
    inception"). Required, no default — this codebase never guesses a
    threshold.

    `fill_delay_bars` (default `0`, reproducing Mission 4's instant-fill
    behavior exactly): when a verdict is APPROVED at bar `i`, the actual
    fill happens `fill_delay_bars` bars later, using *that* later bar's own
    Evidence as the fill reference price — modeling order latency (M6-Q3).
    While a fill is pending, the engine does not evaluate new decision
    cycles; it only marks the account to market, exactly as if an order
    were genuinely in flight and unactionable.

    Raises `BacktestError` if `start`/`end` are naive, if `end <= start`,
    or if the fetched historical range fails `orion.data.validation.
    validate_snapshot` (empty, gapped, non-monotonic, or duplicate-
    timestamped) — checked once, up front, against the *entire* fetched
    range: any well-formed contiguous sub-window of an internally
    consistent sequence is itself internally consistent, so this one
    check covers every per-cycle window without repeating it per bar.
    Never fabricates, interpolates, or silently repairs bad data.

    Propagates (does not catch) `ReasoningProviderError`/
    `DecisionEngineError`/`RiskGuardianError`/`PaperExecutionError` from any
    reused pure function — see the module docstring's fail-closed note.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise BacktestError("start/end must be timezone-aware datetimes")
    if end <= start:
        raise BacktestError(f"end ({end.isoformat()}) must be after start ({start.isoformat()})")
    if window_bars < 1:
        raise BacktestError(f"window_bars must be at least 1, got {window_bars}")
    if fill_delay_bars < 0:
        raise BacktestError(f"fill_delay_bars must not be negative, got {fill_delay_bars}")

    snapshot = data_source.fetch_range(symbol, timeframe, start=start, end=end)
    # Staleness is meaningless for a bulk historical validate -- `now` is
    # deliberately the dataset's own last bar, so `age` is always exactly
    # zero and the STALE finding can never fire here regardless of the
    # (default) threshold used -- passing an inflated threshold explicitly
    # is unnecessary and actively wrong, since `validate_snapshot` multiplies
    # it into a `timedelta` unconditionally and `timedelta * float("inf")`
    # raises `OverflowError`. Staleness fires per-cycle downstream instead,
    # via orion.risk.controls.evidence_is_fresh, which is trivially satisfied
    # by construction (each cycle's Evidence.as_of *is* that cycle's own
    # `now`). What this call is actually for is EMPTY/GAP/NON_MONOTONIC/
    # DUPLICATE_TIMESTAMP.
    validation_now = snapshot.latest_bar.timestamp if snapshot.latest_bar is not None else end
    validation = validate_snapshot(snapshot, now=validation_now)
    if not validation.is_valid:
        findings = "; ".join(f"{f.issue.value}: {f.detail}" for f in validation.findings)
        raise BacktestError(
            f"historical data for {symbol}/{timeframe.value} is invalid: {findings}"
        )

    bars = snapshot.bars
    portfolio: PortfolioState = initial_portfolio_state(
        symbol=symbol, starting_balance=starting_balance, now=start
    )

    cycles: list[BacktestCycle] = []
    fills: list[Fill] = []
    closed_positions: list[ClosedPosition] = []
    equity_curve: list[EquityPoint] = []
    pending: _PendingFill | None = None

    for i, bar in enumerate(bars):
        now = bar.timestamp

        if pending is not None and i < pending.fill_at_index:
            # Order is in flight -- no new decision this bar. Deliberately
            # do NOT mark_to_market here: `portfolio` is still flat (the
            # order hasn't filled), so its `equity` cannot have moved
            # regardless of price -- and `execute()` requires the exact
            # `PortfolioState` `verdict.portfolio_ref` recorded (see its
            # `portfolio_state_fingerprint` check). Advancing `as_of` via
            # mark_to_market on every waiting bar would silently change that
            # fingerprint and make every delayed fill fail closed with a
            # PaperExecutionError at the fill bar -- so `portfolio` is left
            # byte-identical to the one `review()` actually reviewed, and
            # only the equity curve's own `as_of` advances to this bar.
            equity_curve.append(_equity_point(portfolio, now))
            continue

        if pending is not None:
            # This is the delayed fill's own bar -- build its Evidence
            # exactly as any other cycle would, and fill against it via
            # `execute_delayed` (not `execute` -- see orion.backtest.
            # execution's module docstring for why the two cannot be the
            # same function: `execute()` requires the fill Evidence to be
            # byte-identical to what the proposal was reviewed against,
            # which a genuinely later bar's Evidence never is).
            fill_window = _window_for(bars, i, window_bars)
            fill_snapshot = MarketSnapshot(source=snapshot.source, bars=fill_window)
            fill_evidence = build_evidence(fill_snapshot)
            result = execute_delayed(
                pending.proposal,
                pending.verdict,
                fill_evidence,
                portfolio,
                quantity=pending.quantity,
                policy=paper_policy,
                now=now,
            )
            portfolio = result.portfolio
            fills.append(result.fill)
            pending = None
            equity_curve.append(_equity_point(portfolio, now))
            continue

        if portfolio.open_position is not None:
            portfolio, closed = resolve_stop_take_profit_ohlc(
                portfolio, bar, policy=paper_policy, now=now
            )
            if closed is not None:
                closed_positions.append(closed)
            equity_curve.append(_equity_point(portfolio, now))
            continue

        window = _window_for(bars, i, window_bars)
        cycle_snapshot = MarketSnapshot(source=snapshot.source, bars=window)
        evidence = build_evidence(cycle_snapshot)
        reasoning = reasoning_provider.reason(evidence)
        analysis_id = f"backtest-{symbol}-{now.isoformat()}"
        decision = decide(evidence, reasoning, analysis_id=analysis_id, now=now)
        verdict = review(decision, evidence, policy=risk_policy, portfolio=portfolio, now=now)
        cycles.append(BacktestCycle(evidence=evidence, decision=decision, verdict=verdict))

        if verdict.outcome is RiskOutcome.APPROVED:
            ref = decision.reasoning_ref
            assert ref.stop_loss is not None  # RiskVerdict invariants guarantee this for APPROVED
            quantity = compute_position_size(
                portfolio.equity,
                evidence.last_price,
                ref.stop_loss,
                risk_per_trade_fraction=risk_policy.max_risk_per_trade_fraction,
            )
            if fill_delay_bars == 0:
                result = execute(
                    decision,
                    verdict,
                    evidence,
                    portfolio,
                    quantity=quantity,
                    policy=paper_policy,
                    now=now,
                )
                portfolio = result.portfolio
                fills.append(result.fill)
            else:
                pending = _PendingFill(
                    proposal=decision,
                    verdict=verdict,
                    quantity=quantity,
                    fill_at_index=i + fill_delay_bars,
                )
            equity_curve.append(_equity_point(portfolio, now))
            continue

        # NO_TRADE_PROPOSED or VETOED -- nothing executed, just mark to market.
        portfolio = _mark_flat_or_open(portfolio, bar, paper_policy, now)
        equity_curve.append(_equity_point(portfolio, now))

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        cycles=tuple(cycles),
        fills=tuple(fills),
        closed_positions=tuple(closed_positions),
        final_portfolio=portfolio,
        equity_curve=tuple(equity_curve),
    )


def _mark_flat_or_open(
    portfolio: PortfolioState, bar: OHLCVBar, policy: PaperExecutionPolicy, now: datetime
) -> PortfolioState:
    """Mark `portfolio` to `bar.close` without attempting any stop/take-
    profit resolution -- used for cycles where nothing execution-related
    happened (a VETOED/NO_TRADE_PROPOSED verdict, or a bar spent waiting
    on a pending delayed fill). If a position happens to be open (only
    possible while a fill is pending -- there is none yet), this still
    marks correctly since `resolve_stop_take_profit_ohlc` already handles
    the "no open position" case identically; reusing it here keeps the
    marking formula in exactly one place."""
    marked, _ = resolve_stop_take_profit_ohlc(portfolio, bar, policy=policy, now=now)
    return marked


def _equity_point(portfolio: PortfolioState, now: datetime) -> EquityPoint:
    return EquityPoint(
        as_of=now, equity=portfolio.equity, has_open_position=portfolio.open_position is not None
    )
