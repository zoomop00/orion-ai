"""A real `MarketThesis`, built by hand, for the layers that store and read one.

Deliberately a plain function rather than a fixture: `tests/memory`,
`tests/paper` and `tests/reflection` all need one, and pytest fixtures are not
inherited across sibling test packages. Deliberately built here rather than
through `orion.analysts.synthesis`, so the storage and reflection suites can
exercise a thesis without importing an analyst — the same separation the
production packages are held to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orion.analysis.structure import VolatilityLevel
from orion.data.models import Timeframe
from orion.thesis.schemas import (
    ALIGNMENT_SUMMARY,
    POINT_DETAIL,
    THESIS_VERSION,
    ConditionLevel,
    MacroPolicyPressure,
    MacroProvenance,
    MarketRelevance,
    MarketThesis,
    StructureBias,
    TechnicalAvailability,
    ThesisAlignment,
    ThesisPoint,
    ThesisTimeframe,
    ThesisTopic,
)

THESIS_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
#: Shaped exactly like `orion.orchestrator.analysis.build_analysis_id`'s output
#: since Mission 8 v2.2: the bar the analysis was about *and* the instant it was
#: made, so a test's fixture is the shape production actually mints rather than
#: a shorter string that would never occur.
ANALYSIS_ID = "analysis-XAUUSD-1h-2026-08-31T11:00:00+00:00-2026-08-31T12:00:00+00:00"
EVIDENCE_FINGERPRINT = "a" * 64
MACRO_FINGERPRINT = "b" * 64


def build_thesis(
    *,
    analysis_id: str = ANALYSIS_ID,
    symbol: str = "XAUUSD",
    formed_at: datetime = THESIS_TIME,
    evidence_fingerprint: str = EVIDENCE_FINGERPRINT,
    bias: StructureBias = StructureBias.BULLISH,
    with_macro: bool = False,
) -> MarketThesis:
    """One complete thesis. `with_macro=True` adds the all-or-nothing macro half."""
    alignment = ThesisAlignment.CORROBORATED_ACTIVE if with_macro else ThesisAlignment.MACRO_ABSENT
    macro_fields: dict[str, object] = {}
    points: tuple[ThesisPoint, ...] = ()
    if with_macro:
        macro_fields = {
            "macro_policy_pressure": MacroPolicyPressure.TIGHTENING_PRESSURE,
            "macro_relevance": MarketRelevance.HIGH,
            "macro_confidence": 0.72,
            "macro_condition": ConditionLevel.ACTIVE,
            "macro_provenance": MacroProvenance(
                model="claude-opus-5",
                prompt_version="macro-v1",
                input_fingerprint=MACRO_FINGERPRINT,
                source_provider="finnhub",
                query_start=formed_at - timedelta(days=2),
                query_end=formed_at + timedelta(days=5),
                retrieved_at=formed_at,
                analyzed_at=formed_at,
                event_count=1,
                rationale="Inflation printed above estimate across the batch.",
            ),
        }
        points = (
            ThesisPoint(
                topic=ThesisTopic.MARKET_CONDITIONS,
                agreed=True,
                technical_reading=ConditionLevel.ACTIVE.value,
                macro_reading=ConditionLevel.ACTIVE.value,
                detail=POINT_DETAIL[(ThesisTopic.MARKET_CONDITIONS, True)],
            ),
        )

    return MarketThesis(
        analysis_id=analysis_id,
        symbol=symbol,
        formed_at=formed_at,
        thesis_version=THESIS_VERSION,
        alignment=alignment,
        summary=ALIGNMENT_SUMMARY[alignment],
        technical_availability=TechnicalAvailability.AVAILABLE,
        technical_bias=bias,
        technical_condition=ConditionLevel.ACTIVE if with_macro else ConditionLevel.QUIET,
        technical_timeframe_agreement=True,
        technical_analyst_version="test-analyst",
        technical_knowledge_version="test-knowledge",
        timeframes=(
            ThesisTimeframe(
                timeframe=Timeframe.H1,
                bias=bias,
                volatility=VolatilityLevel.LOW,
                finding_count=5,
                unevaluated_count=6,
                evidence_fingerprint=evidence_fingerprint,
                source_provider="twelvedata",
                retrieved_at=formed_at,
                as_of=formed_at - timedelta(hours=1),
                bar_count=120,
            ),
        ),
        agreements=points,
        conflicts=(),
        **macro_fields,  # type: ignore[arg-type]
    )
