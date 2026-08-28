import { describe, expect, it } from "vitest";

import {
  ABSENT,
  NOT_CONNECTED_LABEL,
  RISK_UNAVAILABLE_LABEL,
  formatDecimal,
  formatInstant,
  formatPercent,
  humaniseCode,
  toAnalysisView,
} from "@/lib/analysis-view";
import {
  ORION_ACTIONS,
  ORION_DIRECTIONS,
  ORION_RISK_AVAILABILITIES,
  ORION_RISK_LEVELS,
  ORION_RISK_OUTCOMES,
  ORION_TIMEFRAMES,
  ORION_TRENDS,
  ORION_VOLATILITIES,
} from "@/lib/orion-analysis";

import { analysisFixture, reviewedRiskFixture } from "./support/analysis-fixture";

/**
 * The group separator `analysis-view.ts` uses: U+2009 THIN SPACE. Written as
 * an escape here so the expectation is unambiguous in a diff -- a literal
 * would be indistinguishable from an ordinary space on screen.
 */
const THIN = "\u2009";

/**
 * The whole "what does the screen say?" decision, tested without a screen.
 *
 * Every rounding rule, every label and every absence marker lives in
 * `analysis-view.ts` precisely so it can be asserted here as data. The
 * component test that follows is then free to be about structure and
 * behaviour rather than re-checking that 0.55 renders as "55%".
 */

describe("deterministic number formatting", () => {
  it("formats to a fixed number of places without a locale", () => {
    expect(formatDecimal(2520.6, 2)).toBe(`2${THIN}520.60`);
    expect(formatDecimal(2520.649, 2)).toBe(`2${THIN}520.65`);
    expect(formatDecimal(120, 0)).toBe("120");
    expect(formatDecimal(0, 2)).toBe("0.00");
  });

  it("groups thousands with a thin space, which is neither a comma nor a period", () => {
    // A comma is a decimal separator in half the world. The separator used
    // here must not be confusable with one.
    const grouped = formatDecimal(1234567.5, 2);
    expect(grouped).toBe(`1${THIN}234${THIN}567.50`);
    expect(grouped).not.toContain(",");
    expect(grouped.split(".").length - 1).toBe(1);
  });

  it("keeps a negative sign outside the grouping", () => {
    expect(formatDecimal(-1234.5, 1)).toBe(`-1${THIN}234.5`);
  });

  it("uses no locale-sensitive formatter, so a server and a browser agree", () => {
    // The real guarantee is asserted structurally in source-safety.test.ts
    // (no `toLocaleString`, no `Intl`). This is the behavioural half: the
    // same input must produce one exact string, not "whatever this host
    // happens to think a thousands separator is".
    expect(formatDecimal(9876.5, 2)).toBe(`9${THIN}876.50`);
    expect(formatPercent(0.5)).toBe("50%");
  });

  it("rounds confidence to whole percent, claiming no precision a model lacks", () => {
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(0.554)).toBe("55%");
    expect(formatPercent(0.555)).toBe("56%");
    expect(formatPercent(1)).toBe("100%");
  });
});

describe("timestamps are shown in the offset they arrived in", () => {
  it("reads an instant apart without constructing a Date", () => {
    expect(formatInstant("2026-08-28T05:00:00Z").display).toBe("2026-08-28 · 05:00 UTC");
    expect(formatInstant("2026-08-28T05:00:00+00:00").display).toBe("2026-08-28 · 05:00 UTC");
    expect(formatInstant("2026-08-28T05:37:42.115557Z").display).toBe("2026-08-28 · 05:37 UTC");
  });

  it("never converts an instant into another zone", () => {
    // The report must not restate *when* ORION looked at the market. An
    // offset that is not UTC is shown as itself, not shifted to the viewer.
    const instant = formatInstant("2026-08-28T05:00:00+02:00");
    expect(instant.display).toBe("2026-08-28 · 05:00 UTC+02:00");
    expect(instant.display).toContain("05:00");
    expect(instant.display).not.toContain("03:00");
  });

  it("keeps the exact server string for the machine-readable attribute", () => {
    const raw = "2026-08-28T05:37:42.115557Z";
    expect(formatInstant(raw).machine).toBe(raw);
  });

  it("shows an unrecognised but valid shape verbatim rather than guessing", () => {
    const odd = "2026-08-28T05:00:00.123456789+05:30";
    const instant = formatInstant(odd);
    expect(instant.machine).toBe(odd);
    expect(instant.display).toBeTruthy();
  });
});

describe("reason codes become readable without becoming unsafe", () => {
  it("turns a snake_case code into a sentence", () => {
    expect(humaniseCode("all_rules_passed")).toBe("All rules passed");
    expect(humaniseCode("daily_loss_limit_reached")).toBe("Daily loss limit reached");
    expect(humaniseCode("ok")).toBe("Ok");
  });

  it("cannot produce a character that means anything to a parser", () => {
    // `parseAnalysis` has already proven every code matches /^[a-z0-9_]+$/,
    // so this transformation's output is provably free of markup, quotes and
    // entities for every input that can reach it.
    for (const code of ["all_rules_passed", "risk_veto", "no_trade_proposed", "a1_b2"]) {
      expect(humaniseCode(code)).toMatch(/^[A-Za-z0-9 ]+$/);
    }
  });
});

describe("every closed vocabulary has a label, and no two share one", () => {
  // These loops are the reason the label maps are `Record<Union, string>`
  // rather than switches: a new backend enum member fails the build, and if
  // someone silences that by adding a fallback, this test still fails.
  const labelsFor = <T extends string>(
    values: readonly T[],
    read: (value: T) => string,
  ): string[] => values.map(read);

  it("labels every trend and volatility distinctly", () => {
    const trends = labelsFor(
      ORION_TRENDS,
      (trend) => toAnalysisView(analysisFixture({ structure: { ...analysisFixture().structure, trend } })).structure[0]?.value ?? "",
    );
    expect(new Set(trends).size).toBe(ORION_TRENDS.length);
    expect(trends.every((label) => label.length > 0)).toBe(true);

    const volatilities = labelsFor(
      ORION_VOLATILITIES,
      (volatility) =>
        toAnalysisView(analysisFixture({ structure: { ...analysisFixture().structure, volatility } }))
          .structure[1]?.value ?? "",
    );
    expect(new Set(volatilities).size).toBe(ORION_VOLATILITIES.length);
  });

  it("labels every timeframe distinctly", () => {
    const labels = labelsFor(
      ORION_TIMEFRAMES,
      (timeframe) => toAnalysisView(analysisFixture({ timeframe })).target.timeframe,
    );
    expect(new Set(labels).size).toBe(ORION_TIMEFRAMES.length);
    expect(labels).toEqual(["15M", "1H", "4H", "1D"]);
  });

  it("labels every action and direction distinctly", () => {
    const actions = labelsFor(ORION_ACTIONS, (action) => {
      const direction = action === "buy" ? "long" : action === "sell" ? "short" : "none";
      return toAnalysisView(
        analysisFixture({
          conclusion: { ...analysisFixture().conclusion, action, direction },
        }),
      ).conclusion.action;
    });
    expect(new Set(actions).size).toBe(ORION_ACTIONS.length);

    const directions = labelsFor(
      ORION_DIRECTIONS,
      (direction) =>
        toAnalysisView(
          analysisFixture({
            conclusion: { ...analysisFixture().conclusion, action: "wait", direction },
          }),
        ).conclusion.direction,
    );
    expect(new Set(directions).size).toBe(ORION_DIRECTIONS.length);
    // "No bias" is a claim, not an empty cell: holding no bias differs from
    // holding one and declining to act on it.
    expect(directions).toContain("No bias");
  });

  it("labels every risk level and outcome distinctly", () => {
    const levels = labelsFor(
      ORION_RISK_LEVELS,
      (riskLevel) => toAnalysisView(analysisFixture({ riskLevel })).conclusion.riskLevel,
    );
    expect(new Set(levels).size).toBe(ORION_RISK_LEVELS.length);

    const outcomes = labelsFor(ORION_RISK_OUTCOMES, (outcome) =>
      toAnalysisView(
        analysisFixture({
          risk: {
            availability: "available",
            outcome,
            reasonCodes: [],
            failedControls: [],
            portfolioAsOf: null,
          },
        }),
      ).risk.headline,
    );
    expect(new Set(outcomes).size).toBe(ORION_RISK_OUTCOMES.length);
    expect(outcomes).not.toContain(RISK_UNAVAILABLE_LABEL);
  });

  it("explains every risk availability, and only one of them is a review", () => {
    const explanations = ORION_RISK_AVAILABILITIES.map((availability) =>
      toAnalysisView(
        analysisFixture({
          risk: {
            availability,
            outcome: availability === "available" ? "approved" : null,
            reasonCodes: [],
            failedControls: [],
            portfolioAsOf: null,
          },
        }),
      ).risk,
    );

    expect(new Set(explanations.map((risk) => risk.explanation)).size).toBe(
      ORION_RISK_AVAILABILITIES.length,
    );
    // Exactly one availability means a review happened. The other three must
    // say a review did *not* happen, and must never read as a verdict.
    expect(explanations.filter((risk) => risk.available)).toHaveLength(1);
    for (const risk of explanations.filter((entry) => !entry.available)) {
      expect(risk.headline).toBe(RISK_UNAVAILABLE_LABEL);
      expect(risk.explanation).toMatch(/was not consulted/);
      expect(risk.explanation).not.toMatch(/approved|vetoed|reviewed/i);
    }
  });
});

describe("absence is carried through as absence", () => {
  it("renders a null reading as the absence marker, never as zero", () => {
    const view = toAnalysisView(
      analysisFixture({
        structure: {
          trend: "unknown",
          volatility: "unknown",
          nearestSupport: null,
          nearestResistance: null,
          swingHighCount: 0,
          swingLowCount: 0,
        },
        momentum: {
          rsi: null,
          emaFast: null,
          emaSlow: null,
          smaSlow: null,
          atr: null,
          rsiLength: 14,
          emaFastLength: 20,
          emaSlowLength: 50,
        },
      }),
    );

    const support = view.structure.find((entry) => entry.label === "Support");
    expect(support?.value).toBe(ABSENT);
    expect(support?.available).toBe(false);

    for (const entry of view.momentum) {
      expect(entry.value).toBe(ABSENT);
      expect(entry.available).toBe(false);
      expect(entry.value).not.toBe("0");
      expect(entry.value).not.toBe("0.00");
    }
  });

  it("keeps a genuine zero distinguishable from a missing value", () => {
    // Zero swings is a real count the engines produced. It must not be
    // laundered into the same marker a null uses.
    const view = toAnalysisView(analysisFixture());
    const highs = view.structure.find((entry) => entry.label === "Swing highs");
    expect(highs?.available).toBe(true);

    const zeroed = toAnalysisView(
      analysisFixture({
        structure: { ...analysisFixture().structure, swingHighCount: 0 },
      }),
    );
    const zero = zeroed.structure.find((entry) => entry.label === "Swing highs");
    expect(zero?.value).toBe("0");
    expect(zero?.available).toBe(true);
    expect(zero?.value).not.toBe(ABSENT);
  });
});

describe("news and macro are never anything but not connected", () => {
  it("reports both capabilities as NOT CONNECTED with the backend's own sentence", () => {
    const view = toAnalysisView(analysisFixture());
    expect(view.capabilities.map((capability) => capability.id)).toEqual(["news", "macro"]);

    for (const capability of view.capabilities) {
      expect(capability.status).toBe(NOT_CONNECTED_LABEL);
      expect(capability.status).toBe("NOT CONNECTED");
    }
    expect(view.capabilities[0]?.detail).toBe("No news source is connected in this build.");
  });

  it("invents no headline, count, sentiment or score for either", () => {
    const view = toAnalysisView(analysisFixture());
    const serialised = JSON.stringify(view.capabilities);
    expect(serialised).not.toMatch(/headline|sentiment|score|count|impact/i);
    // The only fields a capability can carry are these four.
    expect(Object.keys(view.capabilities[0] ?? {}).sort()).toEqual([
      "detail",
      "id",
      "label",
      "status",
    ]);
  });
});

describe("the reviewed risk case", () => {
  it("shows the guardian's real verdict, its codes and the account's own timestamp", () => {
    const view = toAnalysisView(reviewedRiskFixture());

    expect(view.risk.available).toBe(true);
    expect(view.risk.headline).toBe("Vetoed");
    expect(view.risk.explanation).toMatch(/reviewed this proposal/);
    expect(view.risk.reasonCodes).toEqual(["Daily loss limit reached"]);
    expect(view.risk.failedControls).toEqual(["Daily loss limit"]);
    expect(view.risk.portfolioAsOf?.machine).toBe("2026-08-28T04:55:00Z");
  });
});

describe("the target line and conclusion carry only fields that exist", () => {
  it("formats the symbol, timeframe, price, bar count and instants", () => {
    const view = toAnalysisView(analysisFixture());

    expect(view.target.symbol).toBe("XAUUSD");
    expect(view.target.timeframe).toBe("1H");
    expect(view.target.lastPrice).toBe(`2${THIN}520.60`);
    expect(view.target.barCount).toBe("120");
    expect(view.target.asOf.display).toBe("2026-08-28 · 05:00 UTC");
  });

  it("passes the rationale through untouched", () => {
    const rationale = "Gold is showing bullish momentum, but resistance remains nearby.";
    const view = toAnalysisView(
      analysisFixture({ conclusion: { ...analysisFixture().conclusion, rationale } }),
    );
    expect(view.conclusion.rationale).toBe(rationale);
  });

  it("carries confidence as both a rounded label and the raw fraction", () => {
    const view = toAnalysisView(analysisFixture({ confidence: 0.55 }));
    expect(view.conclusion.confidence).toBe("55%");
    // The meter is drawn from the fraction, so it must not be pre-rounded.
    expect(view.conclusion.confidenceFraction).toBe(0.55);
  });
});
