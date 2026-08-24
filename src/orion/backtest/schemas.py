"""In-memory result shapes for one backtest run.

`BacktestResult` is never persisted by `orion.backtest` itself (M6-Q4,
approved) — a backtest returns its full result to the caller in-process.
Every object it carries is exactly the same Pydantic model the live
pipeline already produces (`Evidence`, `ProposedDecision`, `RiskVerdict`,
`Fill`, `ClosedPosition`) — no new domain schema, no new fingerprint
mechanism. A caller who wants Mission 5 Reflection over a backtest, or a
durable record of one, journals these objects into a *fresh, disposable*
`DecisionJournal` themselves (e.g. an in-memory `SqliteDecisionJournal`) —
never the production journal. See `docs/DECISIONS.md`'s Mission 6 entry.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from orion.analysis.evidence import Evidence
from orion.data.models import Timeframe
from orion.decision.schemas import ProposedDecision
from orion.paper.schemas import ClosedPosition, Fill
from orion.risk.portfolio import PortfolioState
from orion.risk.schemas import RiskVerdict


class BacktestCycle(BaseModel):
    """One evaluated decision cycle — Evidence, the resulting
    ProposedDecision, and the RiskVerdict that reviewed it. Present for
    *every* cycle the backtest evaluated, not only the ones that traded:
    a WAIT/VETOED cycle is exactly as traceable as an APPROVED one.
    `verdict.decision_ref.content_hash`/`decision.evidence_ref.content_hash`
    already link this record's three fields together via the existing
    fingerprint functions — no new linkage field is added here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: Evidence
    decision: ProposedDecision
    verdict: RiskVerdict


class EquityPoint(BaseModel):
    """One mark-to-market snapshot of account equity, one per bar
    processed — the raw series `orion.backtest.metrics` computes Sharpe/
    Sortino/exposure from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: datetime
    equity: float
    has_open_position: bool


class BacktestResult(BaseModel):
    """The complete, in-memory output of one `orion.backtest.engine.
    run_backtest` call. Frozen and closed (`extra="forbid"`), matching
    every other schema in this codebase.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    cycles: tuple[BacktestCycle, ...]
    fills: tuple[Fill, ...]
    closed_positions: tuple[ClosedPosition, ...]
    final_portfolio: PortfolioState
    equity_curve: tuple[EquityPoint, ...]
