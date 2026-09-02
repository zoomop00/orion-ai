"""The Technical Analyst: a deterministic specialist over real OHLC bars.

IT IS NOT A MODEL, AND THAT IS THE POINT. The Macro Analyst calls Claude
because economic releases are prose that has to be read. Technical analysis
over OHLCV is arithmetic: an EMA crossing, a close beyond a swing, a three-bar
imbalance. Asking a language model to *report* those would replace a
reproducible measurement with a plausible one — and put a token cost, a
latency, a failure mode and a prompt-injection surface on the analysis path in
exchange for nothing. So every value here is computed by `orion.analysis`, and
every sentence is looked up in `orion.analysts.knowledge`. The same bars always
produce the same findings, and a test can assert exactly that.

IT COMPUTES NOTHING OF ITS OWN. Trend, volatility, swing counts and the nearest
levels come from `orion.analysis.structure`; the EMAs, RSI and ATR from
`orion.analysis.indicators`; gaps, structure breaks, sweeps and Fibonacci
anchors from `orion.analysis.price_action`. This module classifies what those
return and attaches the registry's words to it. There is no second
implementation of any indicator here, and no threshold that a detector already
owns is re-chosen.

ABSENCE IS REPORTED, NEVER FILLED IN. Every concept in `TechnicalConcept` ends
up in exactly one of two lists per timeframe — a finding, or an
`UnevaluatedConcept` with the concrete reason. "We looked and found no gaps" is
a finding with a NEUTRAL bias; "this window is too short to hold three bars" is
unevaluated. `TechnicalTimeframeReport`'s own validator enforces that every
concept lands in one of them, so a concept cannot quietly vanish.

MULTI-TIMEFRAME IS A LIST, NOT A HIERARCHY. `analyze_technical` takes one
observation per timeframe and reports each separately, then states whether the
directional ones agree. It does *not* weight higher timeframes more heavily:
that is a real and widely-held convention, but this build has run no study that
would justify a specific weighting, and inventing one would be exactly the
fabricated-calibration failure `classify_volatility`'s own docstring already
warns about. Disagreement is reported as disagreement.

ADVISORY ONLY. Nothing this module returns reaches `orion.decision`,
`orion.risk` or `orion.paper` — those packages cannot import `orion.analysts`
at all (`tests/analysts/test_import_hygiene.py`). The Risk Guardian is
unaffected by everything here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from orion.analysis.evidence import Evidence, build_evidence, evidence_fingerprint
from orion.analysis.price_action import (
    PriceDirection,
    StructureEventKind,
    SwingKind,
    compute_fibonacci_retracement,
    find_fair_value_gaps,
    find_liquidity_sweeps,
    find_structure_events,
    find_swings,
)
from orion.analysis.structure import TrendDirection, VolatilityLevel
from orion.analysts.knowledge import (
    KNOWLEDGE_VERSION,
    TECHNICAL_KNOWLEDGE,
    StructureBias,
    TechnicalConcept,
)
from orion.analysts.technical_schemas import (
    TECHNICAL_ANALYST_VERSION,
    TechnicalAnalystOutput,
    TechnicalAvailability,
    TechnicalFinding,
    TechnicalTimeframeReport,
    TechnicalValue,
    UnevaluatedConcept,
    UnevaluatedReason,
)
from orion.data.models import MarketSnapshot

#: Swing lookback for the price-action detectors. The same default
#: `orion.analysis.structure.compute_structure` uses, named once here so the
#: swings this module reasons about are the swings `Evidence` counted.
SWING_LOOKBACK = 2

#: RSI bands. The conventional 70/30, stated as constants rather than buried in
#: a comparison, and described in the knowledge registry as bands rather than
#: signals. No calibration study backs them; none is claimed.
RSI_UPPER_BAND = 70.0
RSI_LOWER_BAND = 30.0

#: How far apart two moving averages must be, as a fraction of the slower one,
#: before ORION calls them separated rather than level. A deliberately small
#: floor for float noise, not a tuned parameter: it exists so two averages that
#: differ in the tenth decimal are not reported as an alignment.
MA_SEPARATION_FLOOR = 0.0005


class TechnicalAnalystError(RuntimeError):
    """Raised when a technical pass cannot be run at all.

    Own top-level hierarchy, never inheriting `MacroAnalystProviderError` or
    any other layer's — this codebase's established "every layer owns its own
    RuntimeError" rule. Raised for a naive `now` or for observations of more
    than one symbol; never for thin data, which is a reportable state rather
    than a failure.
    """


class TimeframeObservation(BaseModel):
    """One timeframe's bars paired with the `Evidence` built from them.

    THE PAIR IS VALIDATED, SO IT CANNOT BE MISMATCHED. A caller that already
    has `Evidence` — `orion.orchestrator.analysis` does, it just built one —
    passes both and nothing is recomputed. A caller that only has a snapshot
    uses `from_snapshot`, which calls the same `build_evidence` the pipeline
    calls. Either way the fingerprint on every finding is the fingerprint of an
    `Evidence` genuinely built from these bars.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: MarketSnapshot
    evidence: Evidence

    @classmethod
    def from_snapshot(cls, snapshot: MarketSnapshot) -> TimeframeObservation:
        """Build the evidence for `snapshot` with the pipeline's own function.

        Callers are responsible for having already run
        `orion.data.validation.validate_snapshot`, exactly as `build_evidence`
        itself requires — this does not re-check data quality.
        """
        return cls(snapshot=snapshot, evidence=build_evidence(snapshot))

    @model_validator(mode="after")
    def _check_the_evidence_is_this_snapshots(self) -> TimeframeObservation:
        source = self.snapshot.source
        if self.evidence.symbol != source.symbol:
            raise ValueError(
                f"evidence is for {self.evidence.symbol!r} but the snapshot is for "
                f"{source.symbol!r}"
            )
        if self.evidence.timeframe is not source.timeframe:
            raise ValueError(
                f"evidence is on {self.evidence.timeframe.value!r} but the snapshot is on "
                f"{source.timeframe.value!r}"
            )
        if self.evidence.bar_count != len(self.snapshot.bars):
            raise ValueError(
                f"evidence counted {self.evidence.bar_count} bars but the snapshot holds "
                f"{len(self.snapshot.bars)}"
            )
        return self


def analyze_technical(
    observations: Sequence[TimeframeObservation],
    *,
    now: datetime,
) -> TechnicalAnalystOutput:
    """Evaluate every supported concept on every supplied timeframe.

    Pure and deterministic: no network, no model, no clock. `now` is required
    and caller-supplied — stamped as `observed_at`/`analyzed_at` — matching
    `decide`/`review`/`execute`/`reflect_on_closed_position`'s identical
    discipline, so two runs over the same bars produce byte-identical output.

    Raises `TechnicalAnalystError` for a naive `now`, for observations of more
    than one symbol (a pass covers one market), or for a repeated timeframe.
    An empty sequence is `NO_INPUT` and a set of windows too thin to evaluate
    anything is `INSUFFICIENT_DATA` — both real answers, neither an error.
    """
    if now.tzinfo is None:
        raise TechnicalAnalystError("now must be a timezone-aware datetime")

    if not observations:
        return TechnicalAnalystOutput(
            availability=TechnicalAvailability.NO_INPUT,
            symbol="unknown",
            analyzed_at=now,
            analyst_version=TECHNICAL_ANALYST_VERSION,
            knowledge_version=KNOWLEDGE_VERSION,
            timeframes=(),
            bias=StructureBias.UNCLEAR,
            timeframe_agreement=False,
        )

    symbols = {observation.evidence.symbol for observation in observations}
    if len(symbols) != 1:
        raise TechnicalAnalystError(f"one technical pass covers one symbol; got {sorted(symbols)}")
    timeframes = [observation.evidence.timeframe for observation in observations]
    if len(set(timeframes)) != len(timeframes):
        raise TechnicalAnalystError(
            f"each timeframe may appear once; got {sorted(item.value for item in timeframes)}"
        )

    reports = tuple(_report_for(observation, now=now) for observation in observations)
    directional = [
        report.bias
        for report in reports
        if report.bias in (StructureBias.BULLISH, StructureBias.BEARISH)
    ]
    availability = (
        TechnicalAvailability.AVAILABLE
        if any(report.findings for report in reports)
        else TechnicalAvailability.INSUFFICIENT_DATA
    )

    return TechnicalAnalystOutput(
        availability=availability,
        symbol=symbols.pop(),
        analyzed_at=now,
        analyst_version=TECHNICAL_ANALYST_VERSION,
        knowledge_version=KNOWLEDGE_VERSION,
        timeframes=reports,
        bias=_aggregate(directional),
        # Vacuously true is wrong here: no directional timeframe means nothing
        # agreed, so agreement is False rather than an unearned True.
        timeframe_agreement=bool(directional) and len(set(directional)) == 1,
    )


def _aggregate(biases: Sequence[StructureBias]) -> StructureBias:
    """One lean from several. Every entry counts once; a tie is NEUTRAL.

    Deliberately a plain majority rather than a weighted one — see this
    module's docstring on why no timeframe weighting is invented here.
    """
    if not biases:
        return StructureBias.UNCLEAR
    bullish = sum(1 for bias in biases if bias is StructureBias.BULLISH)
    bearish = sum(1 for bias in biases if bias is StructureBias.BEARISH)
    if bullish > bearish:
        return StructureBias.BULLISH
    if bearish > bullish:
        return StructureBias.BEARISH
    return StructureBias.NEUTRAL


def _finding(
    concept: TechnicalConcept,
    *,
    bias: StructureBias,
    observation: str,
    values: tuple[TechnicalValue, ...],
    evidence: Evidence,
    fingerprint: str,
    provider: str,
    retrieved_at: datetime,
    now: datetime,
) -> TechnicalFinding:
    """Assemble one finding, taking its interpretation from the registry.

    `interpretation_for` raises rather than defaulting, so a concept/bias pair
    nobody wrote words for fails loudly here instead of reaching a reader as a
    generic sentence.
    """
    return TechnicalFinding(
        concept=concept,
        timeframe=evidence.timeframe,
        bias=bias,
        observation=observation,
        interpretation=TECHNICAL_KNOWLEDGE[concept].interpretation_for(bias),
        values=values,
        deterministic=True,
        evidence_fingerprint=fingerprint,
        source_provider=provider,
        retrieved_at=retrieved_at,
        as_of=evidence.as_of,
        bar_count=evidence.bar_count,
        observed_at=now,
        knowledge_version=KNOWLEDGE_VERSION,
    )


def _report_for(observation: TimeframeObservation, *, now: datetime) -> TechnicalTimeframeReport:
    """Evaluate every concept on one timeframe."""
    evidence = observation.evidence
    bars = observation.snapshot.bars
    source = observation.snapshot.source
    fingerprint = evidence_fingerprint(evidence)

    findings: list[TechnicalFinding] = []
    unevaluated: list[UnevaluatedConcept] = []

    def add(
        concept: TechnicalConcept,
        bias: StructureBias,
        observation_text: str,
        values: tuple[TechnicalValue, ...],
    ) -> None:
        findings.append(
            _finding(
                concept,
                bias=bias,
                observation=observation_text,
                values=values,
                evidence=evidence,
                fingerprint=fingerprint,
                provider=source.provider,
                retrieved_at=source.retrieved_at,
                now=now,
            )
        )

    def skip(concept: TechnicalConcept, reason: UnevaluatedReason) -> None:
        unevaluated.append(UnevaluatedConcept(concept=concept, reason=reason))

    indicators = evidence.indicators
    structure = evidence.structure
    price = evidence.last_price

    # ------------------------------------------------------------- trend
    if structure.trend is TrendDirection.UNKNOWN:
        skip(TechnicalConcept.TREND_REGIME, UnevaluatedReason.INDICATOR_UNAVAILABLE)
    else:
        trend_bias = {
            TrendDirection.BULLISH: StructureBias.BULLISH,
            TrendDirection.BEARISH: StructureBias.BEARISH,
            TrendDirection.SIDEWAYS: StructureBias.NEUTRAL,
        }[structure.trend]
        assert indicators.ema_fast is not None and indicators.ema_slow is not None
        add(
            TechnicalConcept.TREND_REGIME,
            trend_bias,
            f"last close {price:.4f} against EMA{indicators.ema_fast_length} "
            f"{indicators.ema_fast:.4f} and EMA{indicators.ema_slow_length} "
            f"{indicators.ema_slow:.4f}",
            (
                TechnicalValue(name="last_close", value=price, unit="price"),
                TechnicalValue(name="ema_fast", value=indicators.ema_fast, unit="price"),
                TechnicalValue(name="ema_slow", value=indicators.ema_slow, unit="price"),
            ),
        )

    # --------------------------------------------- moving average alignment
    if indicators.ema_fast is None or indicators.ema_slow is None:
        skip(TechnicalConcept.MOVING_AVERAGE_ALIGNMENT, UnevaluatedReason.INDICATOR_UNAVAILABLE)
    else:
        separation = (indicators.ema_fast - indicators.ema_slow) / indicators.ema_slow
        if abs(separation) < MA_SEPARATION_FLOOR:
            ma_bias = StructureBias.NEUTRAL
        elif separation > 0:
            ma_bias = StructureBias.BULLISH
        else:
            ma_bias = StructureBias.BEARISH
        add(
            TechnicalConcept.MOVING_AVERAGE_ALIGNMENT,
            ma_bias,
            f"EMA{indicators.ema_fast_length} sits {separation * 100:+.3f}% against "
            f"EMA{indicators.ema_slow_length}",
            (
                TechnicalValue(name="ema_fast", value=indicators.ema_fast, unit="price"),
                TechnicalValue(name="ema_slow", value=indicators.ema_slow, unit="price"),
                TechnicalValue(name="separation", value=separation * 100, unit="percent"),
            ),
        )

    # ---------------------------------------------------------- momentum
    if indicators.rsi is None:
        skip(TechnicalConcept.MOMENTUM_RSI, UnevaluatedReason.INDICATOR_UNAVAILABLE)
    else:
        if indicators.rsi >= RSI_UPPER_BAND:
            rsi_bias = StructureBias.BULLISH
        elif indicators.rsi <= RSI_LOWER_BAND:
            rsi_bias = StructureBias.BEARISH
        else:
            rsi_bias = StructureBias.NEUTRAL
        add(
            TechnicalConcept.MOMENTUM_RSI,
            rsi_bias,
            f"RSI({indicators.rsi_length}) is {indicators.rsi:.1f}, against bands "
            f"{RSI_LOWER_BAND:.0f} and {RSI_UPPER_BAND:.0f}",
            (
                TechnicalValue(name="rsi", value=indicators.rsi, unit="index"),
                TechnicalValue(name="upper_band", value=RSI_UPPER_BAND, unit="index"),
                TechnicalValue(name="lower_band", value=RSI_LOWER_BAND, unit="index"),
            ),
        )

    # -------------------------------------------------------- volatility
    if indicators.atr is None or structure.volatility is VolatilityLevel.UNKNOWN:
        skip(TechnicalConcept.VOLATILITY_ATR, UnevaluatedReason.INDICATOR_UNAVAILABLE)
    else:
        atr_pct = indicators.atr / price * 100 if price > 0 else 0.0
        add(
            # Volatility has no side, so it is always NEUTRAL: a high reading
            # says price is moving, never which way. Giving it a direction
            # would be the single easiest way to smuggle a bias in here.
            TechnicalConcept.VOLATILITY_ATR,
            StructureBias.NEUTRAL,
            f"ATR({indicators.atr_length}) is {indicators.atr:.4f}, {atr_pct:.3f}% of price; "
            f"regime {structure.volatility.value}",
            (
                TechnicalValue(name="atr", value=indicators.atr, unit="price"),
                TechnicalValue(name="atr_percent_of_price", value=atr_pct, unit="percent"),
            ),
        )

    # ----------------------------------------------------- swing structure
    swings = find_swings(bars, lookback=SWING_LOOKBACK)
    swing_highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]
    swing_lows = [swing for swing in swings if swing.kind is SwingKind.LOW]
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        skip(TechnicalConcept.SWING_STRUCTURE, UnevaluatedReason.NO_CONFIRMED_SWINGS)
    else:
        rising = swing_highs[-1].price > swing_highs[-2].price
        higher_lows = swing_lows[-1].price > swing_lows[-2].price
        falling = swing_highs[-1].price < swing_highs[-2].price
        lower_lows = swing_lows[-1].price < swing_lows[-2].price
        if rising and higher_lows:
            swing_bias = StructureBias.BULLISH
        elif falling and lower_lows:
            swing_bias = StructureBias.BEARISH
        else:
            swing_bias = StructureBias.NEUTRAL
        add(
            TechnicalConcept.SWING_STRUCTURE,
            swing_bias,
            f"last two swing highs {swing_highs[-2].price:.4f} then "
            f"{swing_highs[-1].price:.4f}; last two swing lows "
            f"{swing_lows[-2].price:.4f} then {swing_lows[-1].price:.4f}",
            (
                TechnicalValue(
                    name="previous_swing_high", value=swing_highs[-2].price, unit="price"
                ),
                TechnicalValue(name="latest_swing_high", value=swing_highs[-1].price, unit="price"),
                TechnicalValue(name="previous_swing_low", value=swing_lows[-2].price, unit="price"),
                TechnicalValue(name="latest_swing_low", value=swing_lows[-1].price, unit="price"),
                TechnicalValue(name="swing_count", value=float(len(swings)), unit="count"),
            ),
        )

    # ------------------------------------------------- support/resistance
    support, resistance = structure.nearest_support, structure.nearest_resistance
    if support is None and resistance is None:
        skip(TechnicalConcept.SUPPORT_RESISTANCE, UnevaluatedReason.NO_CONFIRMED_SWINGS)
    else:
        levels: list[TechnicalValue] = []
        parts: list[str] = []
        if support is not None:
            levels.append(TechnicalValue(name="nearest_support", value=support, unit="price"))
            parts.append(f"support {support:.4f}")
        if resistance is not None:
            levels.append(TechnicalValue(name="nearest_resistance", value=resistance, unit="price"))
            parts.append(f"resistance {resistance:.4f}")
        levels.append(TechnicalValue(name="last_close", value=price, unit="price"))
        add(
            TechnicalConcept.SUPPORT_RESISTANCE,
            # A level is a level. Which side price is nearer to is not evidence
            # of direction, and calling it that would be this module's own
            # opinion rather than a measurement.
            StructureBias.NEUTRAL,
            f"last close {price:.4f} with " + " and ".join(parts),
            tuple(levels),
        )

    # ------------------------------------- structure breaks and character
    events = find_structure_events(bars, lookback=SWING_LOOKBACK)
    if not swings:
        skip(TechnicalConcept.BREAK_OF_STRUCTURE, UnevaluatedReason.NO_CONFIRMED_SWINGS)
        skip(TechnicalConcept.CHANGE_OF_CHARACTER, UnevaluatedReason.NO_CONFIRMED_SWINGS)
    else:
        for concept, kind in (
            (TechnicalConcept.BREAK_OF_STRUCTURE, StructureEventKind.BREAK_OF_STRUCTURE),
            (TechnicalConcept.CHANGE_OF_CHARACTER, StructureEventKind.CHANGE_OF_CHARACTER),
        ):
            matching = [event for event in events if event.kind is kind]
            if not matching:
                add(
                    concept,
                    StructureBias.NEUTRAL,
                    f"0 of these events in the last {len(bars)} bars",
                    (TechnicalValue(name="event_count", value=0.0, unit="count"),),
                )
                continue
            latest = matching[-1]
            event_bias = (
                StructureBias.BULLISH
                if latest.direction is PriceDirection.BULLISH
                else StructureBias.BEARISH
            )
            add(
                concept,
                event_bias,
                f"{len(matching)} in the last {len(bars)} bars; latest at "
                f"{latest.timestamp.isoformat()} closing {latest.close:.4f} through "
                f"{latest.broken_level:.4f}",
                (
                    TechnicalValue(name="event_count", value=float(len(matching)), unit="count"),
                    TechnicalValue(
                        name="latest_broken_level", value=latest.broken_level, unit="price"
                    ),
                    TechnicalValue(name="latest_close", value=latest.close, unit="price"),
                ),
            )

    # ----------------------------------------------------- liquidity sweeps
    if not swings:
        skip(TechnicalConcept.LIQUIDITY_SWEEP, UnevaluatedReason.NO_CONFIRMED_SWINGS)
    else:
        sweeps = find_liquidity_sweeps(bars, lookback=SWING_LOOKBACK)
        if not sweeps:
            add(
                TechnicalConcept.LIQUIDITY_SWEEP,
                StructureBias.NEUTRAL,
                f"0 sweeps in the last {len(bars)} bars",
                (TechnicalValue(name="sweep_count", value=0.0, unit="count"),),
            )
        else:
            latest_sweep = sweeps[-1]
            sweep_bias = (
                StructureBias.BULLISH
                if latest_sweep.direction is PriceDirection.BULLISH
                else StructureBias.BEARISH
            )
            add(
                TechnicalConcept.LIQUIDITY_SWEEP,
                sweep_bias,
                f"{len(sweeps)} in the last {len(bars)} bars; latest at "
                f"{latest_sweep.timestamp.isoformat()} reached {latest_sweep.extreme:.4f} "
                f"through {latest_sweep.swept_level:.4f} and closed {latest_sweep.close:.4f}",
                (
                    TechnicalValue(name="sweep_count", value=float(len(sweeps)), unit="count"),
                    TechnicalValue(
                        name="latest_swept_level", value=latest_sweep.swept_level, unit="price"
                    ),
                    TechnicalValue(name="latest_extreme", value=latest_sweep.extreme, unit="price"),
                    TechnicalValue(name="latest_close", value=latest_sweep.close, unit="price"),
                ),
            )

    # ---------------------------------------------------- fair value gaps
    if len(bars) < 3:
        skip(TechnicalConcept.FAIR_VALUE_GAP, UnevaluatedReason.INSUFFICIENT_BARS)
    else:
        gaps = find_fair_value_gaps(bars)
        open_gaps = [gap for gap in gaps if not gap.filled]
        bullish_open = sum(1 for gap in open_gaps if gap.direction is PriceDirection.BULLISH)
        bearish_open = len(open_gaps) - bullish_open
        if bullish_open > bearish_open:
            gap_bias = StructureBias.BULLISH
        elif bearish_open > bullish_open:
            gap_bias = StructureBias.BEARISH
        else:
            gap_bias = StructureBias.NEUTRAL
        add(
            TechnicalConcept.FAIR_VALUE_GAP,
            gap_bias,
            f"{len(gaps)} detected in the last {len(bars)} bars, {len(open_gaps)} still "
            f"unfilled ({bullish_open} bullish, {bearish_open} bearish)",
            (
                TechnicalValue(name="gap_count", value=float(len(gaps)), unit="count"),
                TechnicalValue(name="unfilled_count", value=float(len(open_gaps)), unit="count"),
                TechnicalValue(name="unfilled_bullish", value=float(bullish_open), unit="count"),
                TechnicalValue(name="unfilled_bearish", value=float(bearish_open), unit="count"),
            ),
        )

    # ------------------------------------------------------- fibonacci
    retracement = compute_fibonacci_retracement(bars, lookback=SWING_LOOKBACK)
    if retracement is None:
        skip(TechnicalConcept.FIBONACCI_RETRACEMENT, UnevaluatedReason.NO_VALID_ANCHOR)
    else:
        depth = (
            "outside the anchor range"
            if retracement.current_ratio is None
            else f"{retracement.current_ratio * 100:.1f}% retraced"
        )
        fib_values = [
            TechnicalValue(name="anchor_high", value=retracement.anchor_high, unit="price"),
            TechnicalValue(name="anchor_low", value=retracement.anchor_low, unit="price"),
        ]
        fib_values.extend(
            TechnicalValue(name=f"level_{level.ratio:.3f}", value=level.price, unit="price")
            for level in retracement.levels
        )
        if retracement.current_ratio is not None:
            fib_values.append(
                TechnicalValue(name="current_ratio", value=retracement.current_ratio, unit="ratio")
            )
        add(
            TechnicalConcept.FIBONACCI_RETRACEMENT,
            # Geometry, not a side. Which level price sits near says nothing
            # directional that the retracement itself establishes.
            StructureBias.NEUTRAL,
            f"anchors {retracement.anchor_low:.4f} to {retracement.anchor_high:.4f} "
            f"({retracement.direction.value} impulse), {depth}",
            tuple(fib_values),
        )

    directional_findings = [
        finding.bias
        for finding in findings
        if finding.bias in (StructureBias.BULLISH, StructureBias.BEARISH)
    ]

    return TechnicalTimeframeReport(
        timeframe=evidence.timeframe,
        symbol=evidence.symbol,
        bar_count=evidence.bar_count,
        volatility=structure.volatility,
        evidence_fingerprint=fingerprint,
        source_provider=source.provider,
        retrieved_at=source.retrieved_at,
        as_of=evidence.as_of,
        findings=tuple(findings),
        unevaluated=tuple(unevaluated),
        bias=_aggregate(directional_findings),
    )
