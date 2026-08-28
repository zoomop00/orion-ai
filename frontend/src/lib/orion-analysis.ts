/**
 * The analysis boundary: an untrusted JSON object becomes an `OrionAnalysis`,
 * or it becomes a refusal.
 *
 * TREATED AS UNTRUSTED, FOR THE SAME REASON `orion-audio.ts` IS. It came
 * from our own backend, which is precisely the assumption that turns a
 * compromised, proxied, cached or simply buggy response into arbitrary data
 * in the React tree. Every field is checked here: type, length, numeric
 * range, closed vocabulary, and the cross-field invariants the server's own
 * schema enforces. Nothing is coerced, clamped, defaulted or repaired — a
 * value that could not have come from an engine is a refusal, not a value to
 * fix up.
 *
 * FAIL-CLOSED, WHOLE. If any part of an `analysis` object is unusable the
 * whole turn is refused, rather than a partial analysis with some panels
 * quietly missing. A half-populated report is worse than none: a user cannot
 * tell "ORION found nothing here" from "the client dropped a field it did
 * not understand", and only one of those is true.
 *
 * WHY IT LIVES BESIDE `orion-api.ts` RATHER THAN INSIDE IT. That module's
 * own docstring explains that its validator is "small enough to read in one
 * sitting — which matters more here than elsewhere, because it is the only
 * thing standing between a malformed response and the React tree." Trebling
 * its length would defeat the property it names. This file has no network
 * access, no imports from `orion-api.ts`, and no side effects of any kind: it
 * is a pure function from `unknown` to a validated value, which is exactly
 * what makes it testable to the depth this needs.
 *
 * CLOSED VOCABULARIES WHERE THE SERVER HAS ENUMS, BOUNDED PATTERNS WHERE IT
 * HAS OPEN CODE LISTS. `trend`, `volatility`, `action`, `direction`,
 * `risk_level`, `availability`, `outcome` and both capability statuses are
 * small, stable enums, so they are checked against explicit allowlists —
 * which is what will let Phase 5.4 render them with a total switch rather
 * than a default branch nobody can reason about. `reason_codes` and
 * `failed_controls` are drawn from large enums that legitimately grow
 * (`DecisionReasonCode`, `RiskReasonCode`, `RiskControlName`), so they are
 * bounded, count-limited, snake_case-checked strings instead: strict enough
 * that nothing renderable-as-markup gets through, loose enough that adding a
 * code to the backend does not break every client.
 *
 * NOT_CONNECTED IS THE ONLY STATUS THERE IS. `news` and `macro` have no data
 * source in this build. `parseCapability` accepts exactly one status string
 * and rejects every other, so a backend that started claiming otherwise would
 * fail here rather than put an unexplained panel on screen.
 *
 * NOTHING IS RENDERED YET. Phase 5.3 validates and carries; Phase 5.4 draws.
 * Every string that reaches this type is data destined to be JSX text — it is
 * never markup, never a URL, never a style, and this module constructs no DOM
 * and no HTML.
 */

/** Bounds, mirroring `orion.interface.schemas`'s own. Never looser. */
const MAX_ID_CHARS = 128;
const MAX_SYMBOL_CHARS = 16;
const MAX_CODE_CHARS = 64;
const MAX_DETAIL_CHARS = 200;
const MAX_RATIONALE_CHARS = 400;
const MAX_TIMESTAMP_CHARS = 64;
const MAX_CODE_LIST = 32;

/** `analysis-XAUUSD-1h-2026-08-28T05:00:00+00:00` and nothing more exotic. */
const ID_PATTERN = /^[A-Za-z0-9:+._-]+$/;
const SYMBOL_PATTERN = /^[A-Z0-9]+$/;
/** Every closed-vocabulary code the backend emits is snake_case ASCII. */
const CODE_PATTERN = /^[a-z0-9_]+$/;

export const ORION_TRENDS = ["bullish", "bearish", "sideways", "unknown"] as const;
export const ORION_VOLATILITIES = ["low", "medium", "high", "unknown"] as const;
export const ORION_TIMEFRAMES = ["15min", "1h", "4h", "1day"] as const;
export const ORION_ACTIONS = ["buy", "sell", "wait"] as const;
export const ORION_DIRECTIONS = ["long", "short", "none"] as const;
export const ORION_RISK_LEVELS = ["low", "medium", "high"] as const;
export const ORION_RISK_AVAILABILITIES = [
  "available",
  "no_portfolio_store",
  "portfolio_unavailable",
  "portfolio_symbol_mismatch",
] as const;
export const ORION_RISK_OUTCOMES = ["approved", "vetoed", "no_trade_proposed"] as const;

/** The one capability status this build can represent. See the docstring. */
export const NOT_CONNECTED = "not_connected";

export type OrionTrend = (typeof ORION_TRENDS)[number];
export type OrionVolatility = (typeof ORION_VOLATILITIES)[number];
export type OrionTimeframe = (typeof ORION_TIMEFRAMES)[number];
export type OrionAction = (typeof ORION_ACTIONS)[number];
export type OrionDirection = (typeof ORION_DIRECTIONS)[number];
export type OrionRiskLevel = (typeof ORION_RISK_LEVELS)[number];
export type OrionRiskAvailability = (typeof ORION_RISK_AVAILABILITIES)[number];
export type OrionRiskOutcome = (typeof ORION_RISK_OUTCOMES)[number];

export interface OrionStructure {
  readonly trend: OrionTrend;
  readonly volatility: OrionVolatility;
  /** `null` means no level was found — never zero, never a guess. */
  readonly nearestSupport: number | null;
  readonly nearestResistance: number | null;
  readonly swingHighCount: number;
  readonly swingLowCount: number;
}

export interface OrionMomentum {
  /** `null` throughout means "not enough history", forwarded, never filled in. */
  readonly rsi: number | null;
  readonly emaFast: number | null;
  readonly emaSlow: number | null;
  readonly smaSlow: number | null;
  readonly atr: number | null;
  readonly rsiLength: number;
  readonly emaFastLength: number;
  readonly emaSlowLength: number;
}

export interface OrionCapability {
  readonly status: typeof NOT_CONNECTED;
  /** A calm sentence written by the backend at development time. */
  readonly detail: string;
}

export const ORION_MACRO_STATUSES = [
  "available",
  "not_connected",
  "macro_unavailable",
  "no_events",
] as const;
export type OrionMacroStatus = (typeof ORION_MACRO_STATUSES)[number];

export const ORION_POLICY_PRESSURES = [
  "tightening_pressure",
  "easing_pressure",
  "mixed",
  "in_line",
  "insufficient_data",
] as const;
export type OrionPolicyPressure = (typeof ORION_POLICY_PRESSURES)[number];

export const ORION_MARKET_RELEVANCES = ["high", "medium", "low", "none"] as const;
export type OrionMarketRelevance = (typeof ORION_MARKET_RELEVANCES)[number];

/**
 * Macro context, or an explicit reason there is none.
 *
 * Unlike `OrionCapability` this one *can* carry a reading, so the five
 * payload fields are present together when `status` is `"available"` and
 * `null` together otherwise -- the backend's `MacroSummary` enforces exactly
 * that pairing, and this parser re-checks it rather than trusting it. A
 * partially-populated macro read is a rejected payload, not a half-rendered
 * panel.
 *
 * `policyPressure` describes the DATA, never a trade direction: the backend's
 * `MacroPolicyPressure` docstring is emphatic that "tightening pressure"
 * means the prints read hotter than expected, not "buy" or "sell". Nothing
 * downstream of this type may translate it into one.
 */
export interface OrionMacro {
  readonly status: OrionMacroStatus;
  readonly detail: string;
  readonly policyPressure: OrionPolicyPressure | null;
  readonly marketRelevance: OrionMarketRelevance | null;
  readonly confidence: number | null;
  readonly rationale: string | null;
  readonly eventCount: number | null;
}

export interface OrionRisk {
  readonly availability: OrionRiskAvailability;
  /** Present if and only if `availability` is `"available"`. */
  readonly outcome: OrionRiskOutcome | null;
  readonly reasonCodes: readonly string[];
  readonly failedControls: readonly string[];
  /** The stored paper account's own timestamp, ISO-8601, or `null`. */
  readonly portfolioAsOf: string | null;
}

export interface OrionConclusion {
  readonly action: OrionAction;
  readonly direction: OrionDirection;
  /** Model-authored natural language. Data for JSX text, never markup. */
  readonly rationale: string;
  readonly reasonCodes: readonly string[];
}

/**
 * One completed analysis, in the interface's own vocabulary.
 *
 * Renamed from the server's snake_case for the same reason `OrionTurn` is: a
 * value of this type cannot exist unless it went through `parseAnalysis`, so
 * "has this been validated?" is answered by the type rather than by
 * remembering.
 */
export interface OrionAnalysis {
  readonly analysisId: string;
  readonly symbol: string;
  readonly timeframe: OrionTimeframe;
  /** ISO-8601, proven parseable. Timestamp of the most recent bar analysed. */
  readonly asOf: string;
  readonly analyzedAt: string;
  readonly lastPrice: number;
  readonly barCount: number;
  readonly structure: OrionStructure;
  readonly momentum: OrionMomentum;
  readonly news: OrionCapability;
  readonly macro: OrionMacro;
  readonly risk: OrionRisk;
  readonly confidence: number;
  readonly riskLevel: OrionRiskLevel;
  readonly conclusion: OrionConclusion;
}

/**
 * Thrown for every rejection, carrying no detail.
 *
 * No message parameter, for the same reason `OrionApiError` has none: there
 * must be no way to attach a server value — a field name, an offending
 * string — to an error the interface could end up rendering.
 */
export class OrionAnalysisError extends Error {
  constructor() {
    super("malformed");
    this.name = "OrionAnalysisError";
  }
}

const reject = (): never => {
  throw new OrionAnalysisError();
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readRecord = (source: Record<string, unknown>, key: string): Record<string, unknown> => {
  const value = source[key];
  return isRecord(value) ? value : reject();
};

const readString = (
  source: Record<string, unknown>,
  key: string,
  max: number,
  pattern?: RegExp,
): string => {
  const value = source[key];
  if (typeof value !== "string") return reject();
  if (value.length === 0 || value.length > max) return reject();
  if (pattern !== undefined && !pattern.test(value)) return reject();
  return value;
};

const readOneOf = <T extends string>(
  source: Record<string, unknown>,
  key: string,
  allowed: readonly T[],
): T => {
  const value = source[key];
  if (typeof value !== "string") return reject();
  return (allowed as readonly string[]).includes(value) ? (value as T) : reject();
};

interface NumberBounds {
  readonly min?: number;
  readonly max?: number;
  /** True when `min` is a strict lower bound (`> min` rather than `>= min`). */
  readonly exclusiveMin?: boolean;
  readonly integer?: boolean;
}

const checkNumber = (value: unknown, bounds: NumberBounds): number => {
  // `Number.isFinite` rejects NaN and both infinities in one check; a JSON
  // number can be neither, but a hostile or hand-built payload can.
  if (typeof value !== "number" || !Number.isFinite(value)) return reject();
  if (bounds.integer === true && !Number.isInteger(value)) return reject();
  if (bounds.min !== undefined) {
    if (bounds.exclusiveMin === true ? value <= bounds.min : value < bounds.min) return reject();
  }
  if (bounds.max !== undefined && value > bounds.max) return reject();
  return value;
};

const readNumber = (
  source: Record<string, unknown>,
  key: string,
  bounds: NumberBounds,
): number => checkNumber(source[key], bounds);

const readNullableNumber = (
  source: Record<string, unknown>,
  key: string,
  bounds: NumberBounds,
): number | null => {
  const value = source[key];
  // `null` is a real answer here — "not enough data", "no level found" — and
  // is the one thing that is never turned into a number.
  if (value === null || value === undefined) return null;
  return checkNumber(value, bounds);
};

const readTimestamp = (source: Record<string, unknown>, key: string): string => {
  const value = readString(source, key, MAX_TIMESTAMP_CHARS);
  // Kept as the server's own ISO string rather than converted to a `Date`:
  // the client has no arithmetic to do on it yet, and parsing only to
  // re-serialise would be a chance to change it. Parseability is still
  // proven, so a caller that formats it later cannot be handed nonsense.
  if (!Number.isFinite(Date.parse(value))) return reject();
  return value;
};

const readNullableTimestamp = (
  source: Record<string, unknown>,
  key: string,
): string | null => {
  const value = source[key];
  if (value === null || value === undefined) return null;
  return readTimestamp(source, key);
};

const readCodeList = (source: Record<string, unknown>, key: string): readonly string[] => {
  const value = source[key];
  if (!Array.isArray(value)) return reject();
  if (value.length > MAX_CODE_LIST) return reject();
  return value.map((entry) => {
    if (typeof entry !== "string") return reject();
    if (entry.length === 0 || entry.length > MAX_CODE_CHARS) return reject();
    if (!CODE_PATTERN.test(entry)) return reject();
    return entry;
  });
};

function parseStructure(value: Record<string, unknown>): OrionStructure {
  return {
    trend: readOneOf(value, "trend", ORION_TRENDS),
    volatility: readOneOf(value, "volatility", ORION_VOLATILITIES),
    nearestSupport: readNullableNumber(value, "nearest_support", { min: 0, exclusiveMin: true }),
    nearestResistance: readNullableNumber(value, "nearest_resistance", {
      min: 0,
      exclusiveMin: true,
    }),
    swingHighCount: readNumber(value, "swing_high_count", { min: 0, integer: true }),
    swingLowCount: readNumber(value, "swing_low_count", { min: 0, integer: true }),
  };
}

function parseMomentum(value: Record<string, unknown>): OrionMomentum {
  return {
    rsi: readNullableNumber(value, "rsi", { min: 0, max: 100 }),
    emaFast: readNullableNumber(value, "ema_fast", { min: 0, exclusiveMin: true }),
    emaSlow: readNullableNumber(value, "ema_slow", { min: 0, exclusiveMin: true }),
    smaSlow: readNullableNumber(value, "sma_slow", { min: 0, exclusiveMin: true }),
    atr: readNullableNumber(value, "atr", { min: 0 }),
    rsiLength: readNumber(value, "rsi_length", { min: 0, exclusiveMin: true, integer: true }),
    emaFastLength: readNumber(value, "ema_fast_length", {
      min: 0,
      exclusiveMin: true,
      integer: true,
    }),
    emaSlowLength: readNumber(value, "ema_slow_length", {
      min: 0,
      exclusiveMin: true,
      integer: true,
    }),
  };
}

function parseCapability(value: Record<string, unknown>): OrionCapability {
  // Exactly one acceptable status. See the module docstring.
  readOneOf(value, "status", [NOT_CONNECTED] as const);
  return {
    status: NOT_CONNECTED,
    detail: readString(value, "detail", MAX_DETAIL_CHARS),
  };
}

function parseMacro(value: Record<string, unknown>): OrionMacro {
  const status = readOneOf(value, "status", ORION_MACRO_STATUSES);
  const detail = readString(value, "detail", MAX_DETAIL_CHARS);

  if (status !== "available") {
    // Every payload field must be absent. A backend that sent a reading
    // alongside a non-available status is not something to render partially.
    for (const key of [
      "policy_pressure",
      "market_relevance",
      "confidence",
      "rationale",
      "event_count",
    ]) {
      const present = value[key];
      if (present !== null && present !== undefined) return reject();
    }
    return {
      status,
      detail,
      policyPressure: null,
      marketRelevance: null,
      confidence: null,
      rationale: null,
      eventCount: null,
    };
  }

  return {
    status,
    detail,
    policyPressure: readOneOf(value, "policy_pressure", ORION_POLICY_PRESSURES),
    marketRelevance: readOneOf(value, "market_relevance", ORION_MARKET_RELEVANCES),
    confidence: readNumber(value, "confidence", { min: 0, max: 1 }),
    rationale: readString(value, "rationale", MAX_RATIONALE_CHARS),
    eventCount: readNumber(value, "event_count", { min: 1, integer: true }),
  };
}

function parseRisk(value: Record<string, unknown>): OrionRisk {
  const availability = readOneOf(value, "availability", ORION_RISK_AVAILABILITIES);
  const rawOutcome = value["outcome"];
  const outcome =
    rawOutcome === null || rawOutcome === undefined
      ? null
      : readOneOf(value, "outcome", ORION_RISK_OUTCOMES);
  const reasonCodes = readCodeList(value, "reason_codes");
  const failedControls = readCodeList(value, "failed_controls");

  // The server's own `RiskSummary` validator enforces both directions of
  // this. Re-checking is not distrust of that validator specifically — it is
  // the same defence-in-depth `orion-audio.ts` applies by re-verifying a
  // decoded length the server also stated.
  const available = availability === "available";
  if (available !== (outcome !== null)) reject();
  if (!available && (reasonCodes.length > 0 || failedControls.length > 0)) reject();

  return {
    availability,
    outcome,
    reasonCodes,
    failedControls,
    portfolioAsOf: readNullableTimestamp(value, "portfolio_as_of"),
  };
}

function parseConclusion(value: Record<string, unknown>): OrionConclusion {
  const action = readOneOf(value, "action", ORION_ACTIONS);
  const direction = readOneOf(value, "direction", ORION_DIRECTIONS);

  // Mirrors `ReasoningVerdict`'s own invariant: BUY requires LONG, SELL
  // requires SHORT. WAIT is unconstrained — "wait, with a long bias" is a
  // legitimate and expected answer.
  if (action === "buy" && direction !== "long") reject();
  if (action === "sell" && direction !== "short") reject();

  return {
    action,
    direction,
    rationale: readString(value, "rationale", MAX_RATIONALE_CHARS),
    reasonCodes: readCodeList(value, "reason_codes"),
  };
}

/**
 * Turn an unknown JSON value into an `OrionAnalysis`, or refuse.
 *
 * `null` and `undefined` are accepted and mean "this turn produced no
 * analysis" — the overwhelmingly common case, and the one every ordinary
 * conversational turn takes. Anything else must be a complete, valid
 * analysis; there is no partial success.
 *
 * Unknown *keys* are ignored rather than rejected, matching `parseTurn`'s
 * existing behaviour and for the same reason: this function builds its own
 * object out of the fields it recognises, so a field it has never heard of
 * cannot reach the React tree whether it is refused or dropped — and
 * dropping it means a purely additive backend change does not break every
 * client at once. Unknown *values*, by contrast, are always rejected.
 */
export function parseAnalysis(value: unknown): OrionAnalysis | null {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return reject();

  return {
    analysisId: readString(value, "analysis_id", MAX_ID_CHARS, ID_PATTERN),
    symbol: readString(value, "symbol", MAX_SYMBOL_CHARS, SYMBOL_PATTERN),
    timeframe: readOneOf(value, "timeframe", ORION_TIMEFRAMES),
    asOf: readTimestamp(value, "as_of"),
    analyzedAt: readTimestamp(value, "analyzed_at"),
    lastPrice: readNumber(value, "last_price", { min: 0, exclusiveMin: true }),
    barCount: readNumber(value, "bar_count", { min: 1, integer: true }),
    structure: parseStructure(readRecord(value, "structure")),
    momentum: parseMomentum(readRecord(value, "momentum")),
    news: parseCapability(readRecord(value, "news")),
    macro: parseMacro(readRecord(value, "macro")),
    risk: parseRisk(readRecord(value, "risk")),
    confidence: readNumber(value, "confidence", { min: 0, max: 1 }),
    riskLevel: readOneOf(value, "risk_level", ORION_RISK_LEVELS),
    conclusion: parseConclusion(readRecord(value, "conclusion")),
  };
}
