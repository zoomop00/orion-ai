"""The closed technical-knowledge registry: what each concept means, the exact
rule ORION applies, and what ORION explicitly does not claim about it.

WHY A REGISTRY AND NOT A PROMPT. The obvious way to give a model trading
knowledge is to put it in a system prompt and hope. That fails three tests this
codebase already applies elsewhere: it is unauditable (nobody can point at the
sentence that produced a reading), it is unbounded (prose invites the model to
extrapolate past what the data supports), and it is untestable. So knowledge
lives here as data — a frozen mapping from a closed `TechnicalConcept` enum to
a `ConceptDefinition` — and `orion.analysts.technical` *looks up* the
interpretation it attaches to a finding rather than composing one.

THE SPLIT THIS MODULE EXISTS TO ENFORCE. Deterministic calculation supplies
every number; knowledge supplies every sentence. A `TechnicalFinding`'s
`observation` is formatted from real values by a fixed template, and its
`interpretation` is copied verbatim out of the definition below. Neither half
can drift into the other: there is no code path that computes a level from a
sentence, and no code path that writes a sentence at runtime.

EVERY WORD HERE IS ORIGINAL AND DESCRIBES ONLY WHAT ORION ACTUALLY COMPUTES.
These are ORION's own definitions of concepts that are public, decades old and
in universal use across charting software — swings, breaks of structure,
imbalance, retracement ratios. No book, course, newsletter or proprietary
methodology was copied, paraphrased or ingested, and no definition describes a
technique this build cannot execute. `UNSUPPORTED_CONCEPTS` is the other half
of that honesty: the concepts ORION is regularly asked about and deliberately
does *not* implement, each with the concrete reason it cannot.

NOTHING HERE IS AN INSTRUCTION. No definition, interpretation or caveat tells
anyone to buy, sell, size, enter or exit. `not_claimed` on every definition is
the field that says so per concept, in the concept's own terms, so a reader
sees the limits beside the meaning rather than in a footnote.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

# Re-exported, not redefined. `StructureBias` is a field of `MarketThesis`, so
# it has to be nameable by the packages that store and read one -- none of
# which may import `orion.analysts`. Its definition therefore lives in
# `orion.thesis.schemas`; this import keeps every existing `from
# orion.analysts.knowledge import StructureBias` working unchanged.
from orion.thesis.schemas import StructureBias as StructureBias

#: Bumped whenever a definition, rule or interpretation below changes, so a
#: stored finding can be traced to the exact wording that produced it. The same
#: role `ReasoningOutput.prompt_version` plays for the model-authored layer.
KNOWLEDGE_VERSION = "2026-08-30.1"


class TechnicalConcept(StrEnum):
    """Every technical concept ORION can actually evaluate.

    A closed vocabulary, and deliberately a short one: a member exists here
    only when `orion.analysis` can compute it from real OHLC bars. Adding a
    member without a deterministic detector behind it would let ORION name a
    concept it cannot measure, which is the failure mode this whole module is
    built to prevent.
    """

    TREND_REGIME = "trend_regime"
    MOVING_AVERAGE_ALIGNMENT = "moving_average_alignment"
    MOMENTUM_RSI = "momentum_rsi"
    VOLATILITY_ATR = "volatility_atr"
    SWING_STRUCTURE = "swing_structure"
    SUPPORT_RESISTANCE = "support_resistance"
    BREAK_OF_STRUCTURE = "break_of_structure"
    CHANGE_OF_CHARACTER = "change_of_character"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    FAIR_VALUE_GAP = "fair_value_gap"
    FIBONACCI_RETRACEMENT = "fibonacci_retracement"


class ConceptDefinition(BaseModel):
    """One concept: what it is, how ORION measures it, and its limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: TechnicalConcept
    title: str = Field(min_length=1, max_length=80)
    definition: str = Field(
        min_length=1,
        max_length=400,
        description="What the concept means, in ORION's own words.",
    )
    rule: str = Field(
        min_length=1,
        max_length=400,
        description="The exact deterministic rule this build applies, stated so a reader can "
        "check the code against it. Never a heuristic ORION does not actually run.",
    )
    requires: tuple[str, ...] = Field(
        min_length=1,
        description="The inputs the rule needs. A finding is not produced when one is absent; "
        "the concept is reported as unevaluated instead.",
    )
    not_claimed: str = Field(
        min_length=1,
        max_length=400,
        description="What this concept does NOT assert. Present on every definition so the "
        "limits travel with the meaning.",
    )
    interpretations: Mapping[StructureBias, str] = Field(
        description="The sentence attached to a finding for each bias. Looked up verbatim, "
        "never composed at runtime, and never containing a number — numbers belong to the "
        "finding's observation, which is computed."
    )

    def interpretation_for(self, bias: StructureBias) -> str:
        """The canonical sentence for `bias`, or a `KeyError` if there is none.

        Deliberately not a `.get(..., default)`: a missing interpretation is a
        gap in this registry, and answering it with a generic sentence would
        hide that a concept can reach a state nobody wrote words for.
        """
        return self.interpretations[bias]


def _definition(
    concept: TechnicalConcept,
    *,
    title: str,
    definition: str,
    rule: str,
    requires: tuple[str, ...],
    not_claimed: str,
    interpretations: dict[StructureBias, str],
) -> ConceptDefinition:
    return ConceptDefinition(
        concept=concept,
        title=title,
        definition=definition,
        rule=rule,
        requires=requires,
        not_claimed=not_claimed,
        interpretations=MappingProxyType(dict(interpretations)),
    )


_DEFINITIONS: tuple[ConceptDefinition, ...] = (
    _definition(
        TechnicalConcept.TREND_REGIME,
        title="Trend regime",
        definition=(
            "Whether price is generally advancing, declining or going nowhere over the "
            "window examined, judged by where price sits relative to its own moving "
            "averages rather than by eye."
        ),
        rule=(
            "orion.analysis.structure.determine_trend: bullish when the fast EMA is above "
            "the slow EMA and the last close is above the slow EMA; bearish when both "
            "comparisons invert; sideways otherwise; unknown when either EMA is unavailable."
        ),
        requires=("fast EMA", "slow EMA", "last close"),
        not_claimed=(
            "A trend regime says nothing about how long the condition will last, how far "
            "price will travel, or whether entering is sensible. It is a description of "
            "bars that have already closed."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "Price is holding above both moving averages, which is the configuration "
                "associated with an advancing market."
            ),
            StructureBias.BEARISH: (
                "Price is holding below both moving averages, which is the configuration "
                "associated with a declining market."
            ),
            StructureBias.NEUTRAL: (
                "Price and its moving averages disagree, which is the configuration "
                "associated with a market without a settled direction."
            ),
            StructureBias.UNCLEAR: (
                "The moving averages needed to classify a regime are not available for this "
                "window, so ORION is not characterising one."
            ),
        },
    ),
    _definition(
        TechnicalConcept.MOVING_AVERAGE_ALIGNMENT,
        title="Moving average alignment",
        definition=(
            "The relationship between a shorter and a longer average of closing prices. "
            "The shorter one reacts sooner, so the order of the two summarises whether "
            "recent prices sit above or below the longer-run average."
        ),
        rule=(
            "Compares the fast EMA against the slow EMA from "
            "orion.analysis.indicators.compute_indicators, and reports the gap between "
            "them as a fraction of the slow EMA."
        ),
        requires=("fast EMA", "slow EMA"),
        not_claimed=(
            "A crossing is not a signal here. ORION reports which average is on top and by "
            "how much, and never that a cross has just occurred or is about to."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "The faster average is above the slower one, so recent closes are averaging "
                "higher than the longer-run mean."
            ),
            StructureBias.BEARISH: (
                "The faster average is below the slower one, so recent closes are averaging "
                "lower than the longer-run mean."
            ),
            StructureBias.NEUTRAL: (
                "The two averages are effectively level, so recent closes are not averaging "
                "away from the longer-run mean in either direction."
            ),
            StructureBias.UNCLEAR: (
                "One or both averages need more history than this window holds, so there is "
                "no alignment to report."
            ),
        },
    ),
    _definition(
        TechnicalConcept.MOMENTUM_RSI,
        title="Momentum (RSI)",
        definition=(
            "A bounded 0-100 oscillator comparing the size of recent gains with the size of "
            "recent losses. High readings mean gains have dominated the lookback; low "
            "readings mean losses have."
        ),
        rule=(
            "Reads the RSI value computed by orion.analysis.indicators.compute_indicators "
            "and classifies it: at or above 70 stretched upward, at or below 30 stretched "
            "downward, otherwise mid-range."
        ),
        requires=("RSI",),
        not_claimed=(
            "A stretched reading is not a reversal call. RSI can stay stretched for the "
            "length of a strong move, and ORION never treats a threshold crossing as an "
            "instruction."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "Recent gains have dominated recent losses by enough to push the oscillator "
                "into its upper band."
            ),
            StructureBias.BEARISH: (
                "Recent losses have dominated recent gains by enough to push the oscillator "
                "into its lower band."
            ),
            StructureBias.NEUTRAL: (
                "Gains and losses over the lookback are reasonably balanced, leaving the "
                "oscillator in its middle range."
            ),
            StructureBias.UNCLEAR: (
                "There is not enough history in this window to compute the oscillator."
            ),
        },
    ),
    _definition(
        TechnicalConcept.VOLATILITY_ATR,
        title="Volatility (ATR)",
        definition=(
            "How much ground price covers in an average bar, including gaps between bars, "
            "expressed as a percentage of price so it can be compared across levels."
        ),
        rule=(
            "Reads the ATR from orion.analysis.indicators.compute_indicators and the regime "
            "orion.analysis.structure.classify_volatility assigns it. Those thresholds are "
            "documented in that function as uncalibrated placeholders, and this concept "
            "inherits that caveat rather than hiding it."
        ),
        requires=("ATR", "last close"),
        not_claimed=(
            "Volatility has no direction: a high reading says price is moving, never which "
            "way. The LOW/MEDIUM/HIGH cut points are not derived from a historical "
            "distribution and should be read as rough bands."
        ),
        interpretations={
            StructureBias.NEUTRAL: (
                "Average bar range is being reported as a share of price. This measures how "
                "much price moves, not which way."
            ),
            StructureBias.UNCLEAR: (
                "There is not enough history in this window to compute an average range."
            ),
        },
    ),
    _definition(
        TechnicalConcept.SWING_STRUCTURE,
        title="Swing structure",
        definition=(
            "The turning points a market leaves behind: bars whose high is the highest, or "
            "whose low is the lowest, of the bars immediately around them. They are the "
            "anchors every other structural concept here is measured against."
        ),
        rule=(
            "orion.analysis.price_action.find_swings: a bar is a swing when its high (low) "
            "is the maximum (minimum) of the 2*lookback+1 bars centred on it. A swing is "
            "treated as knowable only from lookback bars after it forms."
        ),
        requires=("at least 2*lookback+1 bars",),
        not_claimed=(
            "A swing is a completed fact about bars that have closed. ORION does not label "
            "the most recent bars as a swing before the bars that would confirm one exist."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "The most recent turning points step upward, which is the shape of a market "
                "making progress higher."
            ),
            StructureBias.BEARISH: (
                "The most recent turning points step downward, which is the shape of a "
                "market making progress lower."
            ),
            StructureBias.NEUTRAL: (
                "The most recent turning points do not step consistently in either direction."
            ),
            StructureBias.UNCLEAR: (
                "This window does not contain enough confirmed turning points to describe a "
                "structure."
            ),
        },
    ),
    _definition(
        TechnicalConcept.SUPPORT_RESISTANCE,
        title="Support and resistance",
        definition=(
            "The nearest completed turning point below price, and the nearest one above it. "
            "They are levels the market has already reacted at, not levels ORION expects it "
            "to react at again."
        ),
        rule=(
            "orion.analysis.structure.nearest_levels: the highest swing low beneath the last "
            "close, and the lowest swing high above it."
        ),
        requires=("swing points", "last close"),
        not_claimed=(
            "Neither level is a forecast, a target or a place to act. Either can be absent, "
            "which means this window holds no completed turning point on that side and not "
            "that price is unbounded there."
        ),
        interpretations={
            StructureBias.NEUTRAL: (
                "The nearest completed turning points on either side of price are reported "
                "as levels the market has previously reacted at."
            ),
            StructureBias.UNCLEAR: (
                "This window holds no completed turning point on one or both sides of price."
            ),
        },
    ),
    _definition(
        TechnicalConcept.BREAK_OF_STRUCTURE,
        title="Break of structure",
        definition=(
            "A close beyond the most recent confirmed turning point, in the same direction "
            "as the previous break. It marks the market continuing the structure it was "
            "already building."
        ),
        rule=(
            "orion.analysis.price_action.find_structure_events: a close above the latest "
            "confirmed swing high, or below the latest confirmed swing low, where the "
            "previous break ran the same way or there was no previous break in this window."
        ),
        requires=("confirmed swing points", "bar closes"),
        not_claimed=(
            "A break describes a bar that has closed. It is not a prediction of "
            "continuation, and ORION does not measure how far a break might extend."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "Price closed above the most recent confirmed high, continuing the "
                "structure that was already building upward."
            ),
            StructureBias.BEARISH: (
                "Price closed below the most recent confirmed low, continuing the structure "
                "that was already building downward."
            ),
            StructureBias.NEUTRAL: (
                "No close beyond a confirmed turning point occurred in this window."
            ),
            StructureBias.UNCLEAR: (
                "This window holds no confirmed turning points to measure a break against."
            ),
        },
    ),
    _definition(
        TechnicalConcept.CHANGE_OF_CHARACTER,
        title="Change of character",
        definition=(
            "A close beyond a confirmed turning point in the opposite direction to the "
            "previous break. It is the first structural evidence that the direction the "
            "market had been building in has stopped."
        ),
        rule=(
            "orion.analysis.price_action.find_structure_events, where the break runs "
            "counter to the direction of the break before it. The first break in a window "
            "is never labelled a change of character, because there is no earlier direction "
            "for it to have changed."
        ),
        requires=("confirmed swing points", "a previous break in the same window"),
        not_claimed=(
            "A change of character is not a reversal call and not an entry. It says the "
            "prior structural direction was broken, nothing about what replaces it."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "Price closed above a confirmed high after previously breaking downward, "
                "which ends the downward structure this window had been building."
            ),
            StructureBias.BEARISH: (
                "Price closed below a confirmed low after previously breaking upward, which "
                "ends the upward structure this window had been building."
            ),
            StructureBias.NEUTRAL: (
                "No break against the prevailing structural direction occurred in this window."
            ),
            StructureBias.UNCLEAR: (
                "This window holds no confirmed turning points to measure a break against."
            ),
        },
    ),
    _definition(
        TechnicalConcept.LIQUIDITY_SWEEP,
        title="Liquidity sweep",
        definition=(
            "A bar that traded beyond a completed turning point and then closed back inside "
            "it. Price reached through a level where resting orders would sit and did not "
            "hold there."
        ),
        rule=(
            "orion.analysis.price_action.find_liquidity_sweeps: the bar's high exceeds a "
            "confirmed swing high while its close is back at or below it, or the mirror "
            "below a confirmed swing low. The close condition is what separates a sweep "
            "from a break."
        ),
        requires=("confirmed swing points", "bar highs, lows and closes"),
        not_claimed=(
            "ORION cannot see orders, stops or who was filled. This is the price geometry "
            "only, and it asserts nothing about intent, participants or what happens next."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "Price traded below a completed low and closed back above it, so the move "
                "through that level did not hold."
            ),
            StructureBias.BEARISH: (
                "Price traded above a completed high and closed back below it, so the move "
                "through that level did not hold."
            ),
            StructureBias.NEUTRAL: (
                "No bar in this window traded beyond a completed turning point and closed "
                "back inside it."
            ),
            StructureBias.UNCLEAR: (
                "This window holds no confirmed turning points for a sweep to be measured against."
            ),
        },
    ),
    _definition(
        TechnicalConcept.FAIR_VALUE_GAP,
        title="Fair value gap",
        definition=(
            "A range that price moved through so quickly that the bars either side of the "
            "move do not overlap it. It marks a span where trading was one-sided rather "
            "than two-way."
        ),
        rule=(
            "orion.analysis.price_action.find_fair_value_gaps: across three consecutive "
            "bars, the third bar's low strictly above the first bar's high (bullish), or "
            "the third bar's high strictly below the first bar's low (bearish). A gap is "
            "marked filled when a later bar in the same window traded back into it."
        ),
        requires=("at least 3 consecutive bars",),
        not_claimed=(
            "ORION does not claim a gap will be revisited, nor that an unfilled one is a "
            "target. A gap reported as unfilled may have been filled by a bar outside the "
            "window supplied, which is a limit of the data rather than a statement about "
            "the market."
        ),
        interpretations={
            StructureBias.BULLISH: (
                "Unfilled ranges left behind by upward moves outnumber those left by "
                "downward moves in this window."
            ),
            StructureBias.BEARISH: (
                "Unfilled ranges left behind by downward moves outnumber those left by "
                "upward moves in this window."
            ),
            StructureBias.NEUTRAL: (
                "Either no unfilled ranges remain in this window, or neither direction "
                "outnumbers the other."
            ),
            StructureBias.UNCLEAR: (
                "Three consecutive bars are needed to form one of these ranges and this "
                "window does not hold them."
            ),
        },
    ),
    _definition(
        TechnicalConcept.FIBONACCI_RETRACEMENT,
        title="Fibonacci retracement",
        definition=(
            "Fixed proportional levels drawn between the two ends of a completed move, used "
            "to describe how much of that move price has given back. The ratios are "
            "conventional and fixed; only the anchors change."
        ),
        rule=(
            "orion.analysis.price_action.compute_fibonacci_retracement: anchors are the most "
            "recent confirmed swing high and swing low, which must be different bars with "
            "the high strictly above the low. Levels are 0.236, 0.382, 0.5, 0.618 and 0.786 "
            "of that range. Nothing is reported when no such pair exists."
        ),
        requires=("a confirmed swing high", "a confirmed swing low", "distinct, ordered anchors"),
        not_claimed=(
            "These levels are geometry, not forecasts, and ORION does not claim price "
            "reacts at them. When the last close sits outside the anchor range no "
            "retracement depth is reported, because a move price has already exceeded is "
            "not being retraced."
        ),
        interpretations={
            StructureBias.NEUTRAL: (
                "Proportional levels are reported across the most recent completed move, "
                "along with how much of that move price has given back."
            ),
            StructureBias.UNCLEAR: (
                "This window holds no valid pair of opposing turning points to draw a "
                "retracement between."
            ),
        },
    ),
)

#: The registry. A read-only mapping rather than a plain dict, so a consumer
#: cannot mutate ORION's definitions at runtime — the interpretation attached to
#: a finding must be the committed one, not one an earlier caller edited.
TECHNICAL_KNOWLEDGE: Mapping[TechnicalConcept, ConceptDefinition] = MappingProxyType(
    {definition.concept: definition for definition in _DEFINITIONS}
)


class UnsupportedConcept(BaseModel):
    """A concept ORION is commonly asked for and deliberately does not provide."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=400)


#: THE HONEST HALF OF THE REGISTRY. Naming what ORION cannot do is what stops
#: the list above from reading as a claim of completeness. Each entry states a
#: concrete reason rooted in this build, not a vague "not implemented yet".
UNSUPPORTED_CONCEPTS: tuple[UnsupportedConcept, ...] = (
    UnsupportedConcept(
        name="VWAP",
        reason=(
            "Volume-weighted average price needs real volume. OHLCVBar.volume is optional "
            "and is routinely absent for spot metals from the configured provider, and "
            "orion.analysis.indicators.bars_to_dataframe substitutes 0.0 when it is. A VWAP "
            "computed over that substitution would be arithmetic on a placeholder presented "
            "as a measurement."
        ),
    ),
    UnsupportedConcept(
        name="Volume profile / OBV / volume-confirmed breakouts",
        reason=(
            "Same missing input as VWAP: `OHLCVBar.volume` is optional and the data this "
            "build receives for spot metals does not carry it. Every volume-derived "
            "construction is excluded for that reason rather than approximated from tick "
            "counts or bar ranges."
        ),
    ),
    UnsupportedConcept(
        name="MACD",
        reason=(
            "orion.analysis.indicators computes EMA, SMA, RSI and ATR and does not compute "
            "MACD. Adding it would change IndicatorSnapshot, which is inside the "
            "content-fingerprinted Evidence every journaled decision and risk verdict "
            "references, so it belongs to a deliberate change of that record rather than to "
            "this layer."
        ),
    ),
    UnsupportedConcept(
        name="Order blocks",
        reason=(
            "Every usable definition depends on identifying institutional participation, and "
            "the OHLCVBar data this build receives cannot distinguish such a bar from any "
            "other. ORION reports the imbalance it can measure (fair value gaps, in "
            "orion.analysis.price_action) rather than labelling a bar with an actor it "
            "cannot observe."
        ),
    ),
    UnsupportedConcept(
        name="Chart patterns (head and shoulders, wedges, flags, triangles)",
        reason=(
            "These require a fitting procedure with tolerances that decide the answer, and "
            "no such procedure exists in orion.analysis. Reporting one would be presenting a "
            "tuned judgement as a measurement."
        ),
    ),
    UnsupportedConcept(
        name="Elliott wave counts",
        reason=(
            "Wave labelling is not deterministic from price alone; competing valid counts "
            "exist for the same bars. There is no rule ORION could state and check."
        ),
    ),
    UnsupportedConcept(
        name="Multi-symbol correlation and intermarket analysis",
        reason=(
            "This build analyses one symbol per request and holds no second series to "
            "correlate against."
        ),
    ),
    UnsupportedConcept(
        name="Order flow, depth of book, footprint",
        reason=(
            "No such data source is connected. The configured provider returns OHLCV bars "
            "and nothing about resting or executed orders."
        ),
    ),
)
