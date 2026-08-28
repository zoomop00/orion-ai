import type { OrionAnalysis } from "@/lib/orion-analysis";

/**
 * A *validated* analysis, in the shape `parseAnalysis` produces.
 *
 * Deliberately not a wire payload. `tests/orion-analysis.test.ts` owns the
 * question "what does the parser accept?" and answers it against raw JSON;
 * everything downstream of the parser is entitled to assume that question is
 * already settled, so these tests start from the type rather than re-proving
 * validation at every layer.
 *
 * The default is the quiet case on purpose -- WAIT, no bias, no portfolio, no
 * news, most indicators present. That is what an honest local build actually
 * returns, and a fixture that defaulted to an approved BUY with every field
 * populated would let the interesting branches go untested by accident.
 */
export function analysisFixture(overrides: Partial<OrionAnalysis> = {}): OrionAnalysis {
  return {
    analysisId: "analysis-XAUUSD-1h-2026-08-28T05:00:00+00:00",
    symbol: "XAUUSD",
    timeframe: "1h",
    asOf: "2026-08-28T05:00:00Z",
    analyzedAt: "2026-08-28T05:37:42.115557Z",
    lastPrice: 2520.6,
    barCount: 120,
    structure: {
      trend: "bullish",
      volatility: "low",
      nearestSupport: 2498.25,
      nearestResistance: 2544.8,
      swingHighCount: 3,
      swingLowCount: 2,
    },
    momentum: {
      rsi: 62.48,
      emaFast: 2510.4,
      emaSlow: 2495.3,
      smaSlow: 2495.3,
      atr: 2.5,
      rsiLength: 14,
      emaFastLength: 20,
      emaSlowLength: 50,
    },
    news: {
      status: "not_connected",
      detail: "No news source is connected in this build.",
    },
    macro: {
      status: "not_connected",
      detail: "No macro source is connected in this build.",
    },
    risk: {
      availability: "no_portfolio_store",
      outcome: null,
      reasonCodes: [],
      failedControls: [],
      portfolioAsOf: null,
    },
    confidence: 0.55,
    riskLevel: "medium",
    conclusion: {
      action: "wait",
      direction: "none",
      rationale: "Resistance is close and the risk/reward is insufficient here.",
      reasonCodes: ["all_rules_passed"],
    },
    ...overrides,
  };
}

/**
 * The same analysis in the shape the *backend* sends, for tests that drive a
 * whole turn through `fetch` rather than starting from the parsed type.
 *
 * snake_case and nested exactly as `orion.interface.schemas.AnalysisSummary`
 * serialises it, so a turn built from this exercises `parseAnalysis` for real
 * on the way in instead of smuggling a pre-validated object past it.
 */
export function analysisPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    analysis_id: "analysis-XAUUSD-1h-2026-08-28T05:00:00+00:00",
    symbol: "XAUUSD",
    timeframe: "1h",
    as_of: "2026-08-28T05:00:00Z",
    analyzed_at: "2026-08-28T05:37:42.115557Z",
    last_price: 2520.6,
    bar_count: 120,
    structure: {
      trend: "bullish",
      volatility: "low",
      nearest_support: 2498.25,
      nearest_resistance: 2544.8,
      swing_high_count: 3,
      swing_low_count: 2,
    },
    momentum: {
      rsi: 62.48,
      ema_fast: 2510.4,
      ema_slow: 2495.3,
      sma_slow: 2495.3,
      atr: 2.5,
      rsi_length: 14,
      ema_fast_length: 20,
      ema_slow_length: 50,
    },
    news: { status: "not_connected", detail: "No news source is connected in this build." },
    macro: { status: "not_connected", detail: "No macro source is connected in this build." },
    risk: {
      availability: "no_portfolio_store",
      outcome: null,
      reason_codes: [],
      failed_controls: [],
      portfolio_as_of: null,
    },
    confidence: 0.55,
    risk_level: "medium",
    conclusion: {
      action: "wait",
      direction: "none",
      rationale: "Resistance is close and the risk/reward is insufficient here.",
      reason_codes: ["all_rules_passed"],
    },
    ...overrides,
  };
}

/** The reviewed case: a real verdict from the deterministic guardian. */
export function reviewedRiskFixture(): OrionAnalysis {
  return analysisFixture({
    risk: {
      availability: "available",
      outcome: "vetoed",
      reasonCodes: ["daily_loss_limit_reached"],
      failedControls: ["daily_loss_limit"],
      portfolioAsOf: "2026-08-28T04:55:00Z",
    },
  });
}
