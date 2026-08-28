/**
 * The macro read, at the two places it can go wrong on this side.
 *
 * The backend can now send a real assessment of the economic calendar, so
 * this file covers the parser that decides whether to believe it and the two
 * view helpers that turn it into the one line and one sentence the deck
 * renders. The deck itself gained no branch for this capability.
 */
import { describe, expect, it } from "vitest";

import { macroDetailLine, macroStatusLine } from "@/lib/analysis-view";
import { parseAnalysis, type OrionMacro } from "@/lib/orion-analysis";

import { analysisPayload } from "./support/analysis-fixture";

const AVAILABLE = {
  status: "available",
  detail: "Macro context from the economic calendar, assessed by the Macro Analyst.",
  policy_pressure: "tightening_pressure",
  market_relevance: "high",
  confidence: 0.72,
  rationale: "US prints came in above estimate across the batch.",
  event_count: 3,
} as const;

const withMacro = (macro: unknown): unknown => ({
  ...(analysisPayload() as Record<string, unknown>),
  macro,
});

const parseMacroOf = (macro: unknown): OrionMacro => {
  const analysis = parseAnalysis(withMacro(macro));
  // `parseAnalysis` returns null for a payload it rejects; every caller below
  // either expects a parse or asserts the throw, so a null here is a bug in
  // the fixture rather than a case to handle.
  if (analysis === null) throw new Error("the fixture payload did not parse");
  return analysis.macro;
};

describe("parsing a macro read", () => {
  it("accepts a complete available read", () => {
    const macro = parseMacroOf(AVAILABLE);

    expect(macro.status).toBe("available");
    expect(macro.policyPressure).toBe("tightening_pressure");
    expect(macro.marketRelevance).toBe("high");
    expect(macro.confidence).toBeCloseTo(0.72);
    expect(macro.eventCount).toBe(3);
  });

  it("keeps every payload field null when the calendar is not connected", () => {
    const macro = parseMacroOf({
      status: "not_connected",
      detail: "No macro source is connected in this build.",
      policy_pressure: null,
      market_relevance: null,
      confidence: null,
      rationale: null,
      event_count: null,
    });

    expect(macro.policyPressure).toBeNull();
    expect(macro.rationale).toBeNull();
    expect(macro.eventCount).toBeNull();
  });

  it.each(["policy_pressure", "market_relevance", "confidence", "rationale", "event_count"])(
    "rejects an available read missing %s",
    (field) => {
      const partial: Record<string, unknown> = { ...AVAILABLE };
      partial[field] = null;

      expect(() => parseMacroOf(partial)).toThrow();
    },
  );

  it("rejects a reading that arrives alongside a not-connected status", () => {
    // The one shape that would let a disconnected build render macro context.
    expect(() =>
      parseMacroOf({ ...AVAILABLE, status: "not_connected" }),
    ).toThrow();
  });

  it.each([
    ["an unknown status", { ...AVAILABLE, status: "connected" }],
    ["an unknown policy pressure", { ...AVAILABLE, policy_pressure: "bullish" }],
    ["an unknown relevance", { ...AVAILABLE, market_relevance: "extreme" }],
    ["a confidence above one", { ...AVAILABLE, confidence: 1.4 }],
    ["a zero event count", { ...AVAILABLE, event_count: 0 }],
    ["a fractional event count", { ...AVAILABLE, event_count: 2.5 }],
  ])("rejects %s", (_label, payload) => {
    expect(() => parseMacroOf(payload)).toThrow();
  });
});

describe("rendering a macro read", () => {
  it("states what the data implied, how much it matters, and how sure", () => {
    const macro = parseMacroOf(AVAILABLE);

    expect(macroStatusLine(macro)).toBe("TIGHTENING \u00b7 HIGH RELEVANCE \u00b7 72%");
    expect(macroDetailLine(macro)).toBe(AVAILABLE.rationale);
  });

  it("never translates policy pressure into a trade direction", () => {
    // The backend's own vocabulary is explicit that this describes the data,
    // not what to do about it. The label a user reads must not promote it.
    const line = macroStatusLine(parseMacroOf(AVAILABLE));

    for (const forbidden of ["BUY", "SELL", "LONG", "SHORT", "BULLISH", "BEARISH", "HAWKISH"]) {
      expect(line).not.toContain(forbidden);
    }
  });

  it.each([
    ["not_connected", "NOT CONNECTED"],
    ["macro_unavailable", "UNAVAILABLE"],
    ["no_events", "NO EVENTS"],
  ])("falls back to the backend's own sentence for %s", (status, label) => {
    const macro = parseMacroOf({
      status,
      detail: "A calm backend sentence.",
      policy_pressure: null,
      market_relevance: null,
      confidence: null,
      rationale: null,
      event_count: null,
    });

    expect(macroStatusLine(macro)).toBe(label);
    expect(macroDetailLine(macro)).toBe("A calm backend sentence.");
  });
});
