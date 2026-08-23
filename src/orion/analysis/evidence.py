"""Assembles indicators + structure into the single Evidence object the AI
reasoning layer is given. This is the last step before an opinion enters the
pipeline — Evidence itself contains no action, direction, or recommendation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from orion.analysis.indicators import IndicatorSnapshot, compute_indicators
from orion.analysis.structure import StructureSnapshot, compute_structure
from orion.data.models import MarketSnapshot, Timeframe
from orion.provenance import canonical_hash


class Evidence(BaseModel):
    symbol: str
    timeframe: Timeframe
    as_of: datetime
    last_price: float
    bar_count: int
    indicators: IndicatorSnapshot
    structure: StructureSnapshot


def build_evidence(snapshot: MarketSnapshot) -> Evidence:
    """Build Evidence from an already data-quality-validated MarketSnapshot.

    Callers are responsible for running `orion.data.validation.validate_snapshot`
    first — this function does not re-check data quality.
    """
    latest = snapshot.latest_bar
    if latest is None:
        raise ValueError("cannot build evidence from an empty MarketSnapshot")

    indicators = compute_indicators(snapshot.bars)
    structure = compute_structure(snapshot.bars, indicators)

    return Evidence(
        symbol=snapshot.source.symbol,
        timeframe=snapshot.source.timeframe,
        as_of=latest.timestamp,
        last_price=latest.close,
        bar_count=len(snapshot.bars),
        indicators=indicators,
        structure=structure,
    )


def evidence_fingerprint(evidence: Evidence) -> str:
    """A deterministic fingerprint of `evidence`'s full content.

    Thin, domain-named wrapper around `orion.provenance.canonical_hash` — the
    single source of truth for "which Evidence did this concern" across
    package boundaries. `orion.reasoning.claude_provider` computes it and
    stamps it onto every `ReasoningOutput` at proposal time (never asking the
    model to report it itself); `orion.decision.engine` and `orion.risk.
    guardian` each recompute it from the Evidence they receive to verify it
    still refers to the same snapshot before treating anything downstream of
    it as usable.
    """
    return canonical_hash(evidence)
