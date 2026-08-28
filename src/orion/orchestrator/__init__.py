"""ORION's orchestrator — the composition root that turns "analyze gold"
into a real analysis.

Mission 10 Phase 5.1. This package composes existing engines and computes
nothing of its own:

    Intent  ->  MarketDataProvider.fetch_ohlcv
            ->  validate_snapshot
            ->  build_evidence
            ->  ReasoningProvider.reason
            ->  decide
            ->  review            (only with a real, readable paper account)
            ->  AnalysisReport

Everything is injected — providers, risk policy, portfolio store, and the
clock. This package imports neither `orion.config` nor any vendor SDK, so it
is constructible and fully testable with no environment, no credential and
no network. It is also, by the same token, not wired to anything yet:
`orion.interface` still runs the Mission 9 `StaticResponseProvider`, and
connecting `AnalysisResponseProvider` to the HTTP surface is Phase 5.2.

Nothing is persisted. The portfolio store is only ever read, never written,
and no decision journal is touched — matching Mission 7's and Mission 8's
identical "don't build storage ahead of a proven consumer" precedent.
"""

from orion.orchestrator.analysis import (
    DEFAULT_OUTPUT_SIZE,
    MACRO_NOT_CONNECTED,
    NEWS_NOT_CONNECTED,
    ORCHESTRATOR_VERSION,
    AnalysisDataQualityError,
    AnalysisError,
    AnalysisService,
    build_analysis_id,
    run_analysis,
)
from orion.orchestrator.intent import DEFAULT_TIMEFRAME, known_symbols, parse_intent
from orion.orchestrator.response import (
    MAX_SPOKEN_CHARS,
    AnalysisResponseProvider,
    summarize,
)
from orion.orchestrator.schemas import (
    AnalysisReport,
    CapabilityStatus,
    Intent,
    IntentKind,
    NotConnectedPanel,
    RiskAvailability,
    RiskPanel,
)

__all__ = [
    "DEFAULT_OUTPUT_SIZE",
    "DEFAULT_TIMEFRAME",
    "MACRO_NOT_CONNECTED",
    "MAX_SPOKEN_CHARS",
    "NEWS_NOT_CONNECTED",
    "ORCHESTRATOR_VERSION",
    "AnalysisDataQualityError",
    "AnalysisError",
    "AnalysisReport",
    "AnalysisResponseProvider",
    "AnalysisService",
    "CapabilityStatus",
    "Intent",
    "IntentKind",
    "NotConnectedPanel",
    "RiskAvailability",
    "RiskPanel",
    "build_analysis_id",
    "known_symbols",
    "parse_intent",
    "run_analysis",
    "summarize",
]
