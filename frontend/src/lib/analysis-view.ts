/**
 * A validated `OrionAnalysis` becomes something a component can render.
 *
 * WHY THIS IS A SEPARATE, PURE MODULE. `orion-analysis.ts` decides what is
 * *true*; this decides what is *shown*. Keeping them apart means the parser
 * stays the small, readable boundary its own docstring promises, and it means
 * every labelling and formatting decision below is testable without a DOM.
 * The component that consumes this ends up almost declarative, which is the
 * point: there is nowhere in JSX for a rounding rule or a fallback string to
 * hide.
 *
 * NOTHING HERE INVENTS A VALUE. Every string this module produces is either
 * (a) a field that survived `parseAnalysis`, (b) a label chosen at
 * development time from a closed vocabulary, or (c) the em dash that means
 * "the backend sent null". A missing RSI renders as an explicit absence, not
 * as zero -- and the component pairs that dash with an accessible "not
 * available" so it cannot be misread as a reading of zero. Absence is
 * information, and the one thing it must never look like is data.
 *
 * NO LOCALE, NO CLOCK, NO `Date`. Numbers are formatted with `toFixed` and a
 * hand-written group separator; timestamps are read off the server's own ISO
 * string with a regular expression and never parsed into a `Date`. Both
 * choices are for the same two reasons. First, `toLocaleString` and `Intl`
 * resolve against the host's locale and time zone, so a server render and a
 * browser render can disagree and React would report a hydration mismatch on
 * a page whose entire content is a report. Second, converting an instant into
 * the viewer's zone would silently restate *when* ORION looked at the market
 * -- the one fact a report must not quietly change. The timestamp is
 * displayed in the offset the backend sent it in.
 *
 * TOTAL RECORDS, NOT SWITCHES WITH A DEFAULT. Every enum below is mapped with
 * `Record<TheUnion, string>`, so adding a member to the backend's enum and to
 * `orion-analysis.ts` fails the build here until someone decides what it
 * should be called. A `default:` branch would render the next new state as
 * whatever the fallback happened to be, which is how an interface starts
 * lying without anyone editing it.
 */

import type {
  OrionAction,
  OrionAnalysis,
  OrionDirection,
  OrionRiskAvailability,
  OrionRiskLevel,
  OrionRiskOutcome,
  OrionTimeframe,
  OrionTrend,
  OrionVolatility,
} from "./orion-analysis";

/** What the interface shows where the backend sent `null`. */
export const ABSENT = "—";

/** Said aloud by a screen reader wherever `ABSENT` is shown. */
export const ABSENT_LABEL = "not available";

/**
 * The one capability status this build can represent, in the interface's own
 * voice. Upper case in the data rather than only in CSS: a reader of the
 * accessibility tree, a test, and a person looking at the screen should all
 * get the same unambiguous answer about whether news exists here.
 */
export const NOT_CONNECTED_LABEL = "NOT CONNECTED";

/** Shown instead of a risk verdict when the guardian was never consulted. */
export const RISK_UNAVAILABLE_LABEL = "NOT AVAILABLE";

const TREND_LABELS: Readonly<Record<OrionTrend, string>> = {
  bullish: "Bullish",
  bearish: "Bearish",
  sideways: "Sideways",
  unknown: "Unknown",
};

const VOLATILITY_LABELS: Readonly<Record<OrionVolatility, string>> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  unknown: "Unknown",
};

const TIMEFRAME_LABELS: Readonly<Record<OrionTimeframe, string>> = {
  "15min": "15M",
  "1h": "1H",
  "4h": "4H",
  "1day": "1D",
};

const ACTION_LABELS: Readonly<Record<OrionAction, string>> = {
  buy: "Buy",
  sell: "Sell",
  wait: "Wait",
};

/**
 * `none` is "no bias is held", which is a different statement from "waiting
 * while biased long" -- `orion.reasoning.schemas.Direction` is explicit that
 * the two must stay distinguishable, so the label says so rather than
 * rendering an empty cell.
 */
const DIRECTION_LABELS: Readonly<Record<OrionDirection, string>> = {
  long: "Long bias",
  short: "Short bias",
  none: "No bias",
};

const RISK_LEVEL_LABELS: Readonly<Record<OrionRiskLevel, string>> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

const RISK_OUTCOME_LABELS: Readonly<Record<OrionRiskOutcome, string>> = {
  approved: "Approved",
  vetoed: "Vetoed",
  no_trade_proposed: "No trade proposed",
};

/**
 * Why there is no verdict, in ORION's own voice.
 *
 * Frontend-authored for the same reason `FAILURE_MESSAGES` in `orion-api.ts`
 * is: these sentences are shown to a person, so they must be calm and they
 * must be ours. Not one of them implies that a review happened, because in
 * three of the four cases none did -- that distinction is the whole reason
 * `RiskAvailability` has four members instead of a boolean.
 */
const RISK_AVAILABILITY_EXPLANATIONS: Readonly<Record<OrionRiskAvailability, string>> = {
  available: "The risk guardian reviewed this proposal.",
  no_portfolio_store: "No paper account is connected, so the risk guardian was not consulted.",
  portfolio_unavailable:
    "The paper account could not be read, so the risk guardian was not consulted.",
  portfolio_symbol_mismatch:
    "The stored paper account is for a different symbol, so the risk guardian was not consulted.",
};

/**
 * Group thousands with a thin space rather than a comma or a period.
 *
 * A comma is a decimal separator to most of the world and a group separator
 * to the rest, and this module has deliberately given up the locale machinery
 * that would know which. A thin space is unambiguous under both conventions,
 * which is the honest way to be locale-free rather than quietly picking one
 * region's punctuation and calling it neutral.
 */
const GROUP_SEPARATOR = " ";

export function formatDecimal(value: number, places: number): string {
  const fixed = Math.abs(value).toFixed(places);
  const [whole = "0", fraction] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, GROUP_SEPARATOR);
  const sign = value < 0 ? "-" : "";
  return fraction === undefined ? `${sign}${grouped}` : `${sign}${grouped}.${fraction}`;
}

const formatNullable = (value: number | null, places: number): string =>
  value === null ? ABSENT : formatDecimal(value, places);

/**
 * A confidence of 0.55 becomes "55%".
 *
 * A unit change on a field that exists, not a new number: `confidence` is
 * already a 0..1 fraction the engines produced and `parseAnalysis` bounded.
 * Rounded to whole percent because a report claiming 55.0000001% confidence
 * is claiming a precision no model has.
 */
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/**
 * `all_rules_passed` becomes "All rules passed".
 *
 * Safe to do to these strings specifically, because `parseAnalysis` has
 * already proven every code matches `/^[a-z0-9_]+$/` -- there is no character
 * in the input that could survive as markup, a quote or an entity. The
 * transformation is total and still legible as the code a reader would find
 * in `DecisionReasonCode`.
 */
export function humaniseCode(code: string): string {
  const spaced = code.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export interface AnalysisInstant {
  /** Exactly what the backend sent, for the `datetime` attribute. */
  readonly machine: string;
  /** `2026-08-28 - 05:00 UTC`, in the offset the backend sent. */
  readonly display: string;
}

/**
 * Split an ISO-8601 instant into its parts without constructing a `Date`.
 *
 * `parseAnalysis` has already proven the string is parseable and at most 64
 * characters, so this is a presentation concern only. Anything this pattern
 * does not recognise is shown verbatim rather than guessed at: an unexpected
 * but valid shape should look slightly raw, never subtly wrong.
 */
const ISO_INSTANT =
  /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;

export function formatInstant(iso: string): AnalysisInstant {
  const match = ISO_INSTANT.exec(iso);
  if (match === null) return { machine: iso, display: iso };

  const [, date = "", time = "", offset] = match;
  const zone =
    offset === undefined
      ? ""
      : offset === "Z" || offset === "+00:00" || offset === "+0000"
        ? " UTC"
        : ` UTC${offset}`;
  return { machine: iso, display: `${date} · ${time}${zone}` };
}

/** One label/value pair in a panel. */
export interface AnalysisReading {
  readonly label: string;
  readonly value: string;
  /** False when the backend sent `null` and `value` is therefore `ABSENT`. */
  readonly available: boolean;
  /** Secondary text, e.g. the period an indicator was computed over. */
  readonly hint?: string;
}

export interface AnalysisCapabilityView {
  readonly id: "news" | "macro";
  readonly label: string;
  /** Always `NOT_CONNECTED_LABEL`; the type says so and so does the parser. */
  readonly status: typeof NOT_CONNECTED_LABEL;
  /** The backend's own fixed sentence about why. */
  readonly detail: string;
}

export interface AnalysisRiskView {
  /** True only when the deterministic guardian actually produced a verdict. */
  readonly available: boolean;
  /** The outcome, or `RISK_UNAVAILABLE_LABEL`. Never a stand-in verdict. */
  readonly headline: string;
  readonly explanation: string;
  readonly reasonCodes: readonly string[];
  readonly failedControls: readonly string[];
  readonly portfolioAsOf: AnalysisInstant | null;
}

export interface AnalysisTargetView {
  readonly symbol: string;
  readonly timeframe: string;
  readonly lastPrice: string;
  readonly barCount: string;
  readonly asOf: AnalysisInstant;
  readonly analyzedAt: AnalysisInstant;
}

export interface AnalysisConclusionView {
  readonly action: string;
  readonly direction: string;
  readonly rationale: string;
  readonly reasonCodes: readonly string[];
  readonly confidence: string;
  /** 0..1, for a meter drawn with a transform rather than a width. */
  readonly confidenceFraction: number;
  readonly riskLevel: string;
}

export interface AnalysisView {
  readonly target: AnalysisTargetView;
  readonly conclusion: AnalysisConclusionView;
  readonly structure: readonly AnalysisReading[];
  readonly momentum: readonly AnalysisReading[];
  readonly capabilities: readonly AnalysisCapabilityView[];
  readonly risk: AnalysisRiskView;
}

const reading = (
  label: string,
  value: number | null,
  places: number,
  hint?: string,
): AnalysisReading => ({
  label,
  value: formatNullable(value, places),
  available: value !== null,
  ...(hint === undefined ? {} : { hint }),
});

/**
 * The whole transformation, in one total function.
 *
 * Every field of `OrionAnalysis` is either placed somewhere below or
 * deliberately not shown, and nothing is reached for later out of the raw
 * object. That is what keeps "what does the interface render?" answerable by
 * reading this one function.
 */
export function toAnalysisView(analysis: OrionAnalysis): AnalysisView {
  const { structure, momentum, risk, conclusion } = analysis;

  return {
    target: {
      symbol: analysis.symbol,
      timeframe: TIMEFRAME_LABELS[analysis.timeframe],
      lastPrice: formatDecimal(analysis.lastPrice, 2),
      barCount: formatDecimal(analysis.barCount, 0),
      asOf: formatInstant(analysis.asOf),
      analyzedAt: formatInstant(analysis.analyzedAt),
    },
    conclusion: {
      action: ACTION_LABELS[conclusion.action],
      direction: DIRECTION_LABELS[conclusion.direction],
      rationale: conclusion.rationale,
      reasonCodes: conclusion.reasonCodes.map(humaniseCode),
      confidence: formatPercent(analysis.confidence),
      confidenceFraction: analysis.confidence,
      riskLevel: RISK_LEVEL_LABELS[analysis.riskLevel],
    },
    structure: [
      { label: "Trend", value: TREND_LABELS[structure.trend], available: true },
      { label: "Volatility", value: VOLATILITY_LABELS[structure.volatility], available: true },
      reading("Support", structure.nearestSupport, 2),
      reading("Resistance", structure.nearestResistance, 2),
      reading("Swing highs", structure.swingHighCount, 0),
      reading("Swing lows", structure.swingLowCount, 0),
    ],
    momentum: [
      reading("RSI", momentum.rsi, 1, `period ${momentum.rsiLength}`),
      reading("EMA fast", momentum.emaFast, 2, `period ${momentum.emaFastLength}`),
      reading("EMA slow", momentum.emaSlow, 2, `period ${momentum.emaSlowLength}`),
      reading("SMA slow", momentum.smaSlow, 2, `period ${momentum.emaSlowLength}`),
      reading("ATR", momentum.atr, 2),
    ],
    capabilities: [
      { id: "news", label: "News", status: NOT_CONNECTED_LABEL, detail: analysis.news.detail },
      { id: "macro", label: "Macro", status: NOT_CONNECTED_LABEL, detail: analysis.macro.detail },
    ],
    risk: {
      // The parser has already made "available with no outcome" and
      // "unavailable with an outcome" both unrepresentable, so these two
      // agree by construction rather than by being kept in step here.
      available: risk.availability === "available",
      headline:
        risk.outcome === null ? RISK_UNAVAILABLE_LABEL : RISK_OUTCOME_LABELS[risk.outcome],
      explanation: RISK_AVAILABILITY_EXPLANATIONS[risk.availability],
      reasonCodes: risk.reasonCodes.map(humaniseCode),
      failedControls: risk.failedControls.map(humaniseCode),
      portfolioAsOf: risk.portfolioAsOf === null ? null : formatInstant(risk.portfolioAsOf),
    },
  };
}
