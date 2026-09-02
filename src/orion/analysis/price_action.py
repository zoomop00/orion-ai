"""Deterministic price-action primitives: swings, structure breaks, sweeps,
fair value gaps and Fibonacci anchors.

WHY THIS IS A SEPARATE MODULE AND NOT PART OF `Evidence`. `indicators.py` and
`structure.py` feed `orion.analysis.evidence.Evidence`, which is
content-fingerprinted (`evidence_fingerprint`) and journaled, and every
`ProposedDecision`/`RiskVerdict` in the decision memory references that
fingerprint. Adding a field to `Evidence` would change the hash of every
future record for reasons unrelated to any of them. Nothing here is on
`Evidence`; these are computed on demand by whoever needs them, so no
journaled fingerprint moves.

EVERY FUNCTION IS PURE AND LOOK-AHEAD FREE. A fractal swing at bar `i` is not
*confirmed* until bar `i + lookback` exists, so `find_structure_events` and
`find_liquidity_sweeps` compare bar `j` only against swings already confirmed
strictly before `j`. Detecting a break against a swing whose confirmation
depended on bars after the break would be look-ahead bias — the exact class of
error Mission 6 lists as a required validation check — and it is avoided here
by construction rather than by convention.

NOTHING HERE IS INVENTED. Every level, gap and event is read off real OHLC
values from bars a provider returned. A concept that the supplied bars cannot
support produces an empty result or `None`, never a placeholder: no gap is
reported without the three bars that form it, and no Fibonacci retracement is
reported without two genuine, opposing, non-degenerate swing anchors.

VOLUME IS DELIBERATELY UNTOUCHED. `OHLCVBar.volume` is `float | None` and is
routinely absent for spot FX/metals from the configured provider, and
`orion.analysis.indicators.bars_to_dataframe` substitutes `0.0` for a missing
one. Any volume-weighted construction (VWAP, OBV, volume profile) computed
over that substitution would be arithmetic on a placeholder presented as a
measurement, so no function here reads `volume` at all. See
`orion.analysts.knowledge.UNSUPPORTED_CONCEPTS`, which says so out loud.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.data.models import OHLCVBar

#: The retracement ratios ORION reports. The conventional public set
#: (Fibonacci-derived, in universal use across charting software); no
#: proprietary or book-specific level is included, and nothing here is
#: extrapolated beyond the anchor range.
FIBONACCI_RATIOS: tuple[float, ...] = (0.236, 0.382, 0.5, 0.618, 0.786)


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class PriceDirection(StrEnum):
    """Which side of the book a price-action event sits on.

    A description of geometry — which way the market moved — never an
    instruction. Nothing in this module or its consumers turns one of these
    into an order; see `orion.analysts.technical_schemas` for the field-name
    guarantee that keeps it that way.
    """

    BULLISH = "bullish"
    BEARISH = "bearish"


class SwingPoint(BaseModel):
    """One confirmed fractal swing, with the position it was found at.

    `index` and `timestamp` are what `orion.analysis.structure.
    find_swing_points` throws away — it returns bare prices, which is all the
    trend/support/resistance code needed. Structure breaks, sweeps and
    Fibonacci anchors all need to know *when* a swing formed, which is why
    this richer type exists and why that function now delegates here rather
    than a second fractal implementation being written beside it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SwingKind
    index: int = Field(ge=0)
    timestamp: datetime
    price: float = Field(gt=0)
    confirmed_at_index: int = Field(
        ge=0,
        description="The earliest bar index from which this swing is knowable. A fractal "
        "needs `lookback` bars on both sides, so a swing at `index` is not confirmed until "
        "`index + lookback`. Consumers compare a bar only against swings confirmed strictly "
        "before it, which is what keeps structure detection free of look-ahead bias.",
    )

    @model_validator(mode="after")
    def _check_confirmation_is_not_before_formation(self) -> SwingPoint:
        if self.confirmed_at_index < self.index:
            raise ValueError(
                f"confirmed_at_index ({self.confirmed_at_index}) is before the swing itself "
                f"({self.index}); a fractal cannot be known before it has formed"
            )
        return self


class FairValueGap(BaseModel):
    """A three-bar imbalance: a range no trade occurred in as price moved through.

    Bullish when the third bar's low is strictly above the first bar's high;
    bearish when the third bar's high is strictly below the first bar's low.
    That is the whole definition — a geometric fact about three consecutive
    bars, with no threshold to tune and nothing inferred.

    `filled` records whether any *later* bar traded back into the gap. It is a
    fact about the bars supplied, not a prediction, and a gap outside the
    supplied window cannot be known to be filled.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: PriceDirection
    index: int = Field(ge=0, description="index of the middle bar of the three that formed it.")
    timestamp: datetime
    lower: float = Field(gt=0)
    upper: float = Field(gt=0)
    filled: bool

    @model_validator(mode="after")
    def _check_gap_is_a_real_range(self) -> FairValueGap:
        if self.upper <= self.lower:
            raise ValueError(
                f"a fair value gap must span a real range; got lower={self.lower} "
                f"upper={self.upper}"
            )
        return self

    @property
    def size(self) -> float:
        return self.upper - self.lower


class StructureEventKind(StrEnum):
    """Whether a break continued the prevailing structure or reversed it."""

    #: Price closed beyond a confirmed swing in the direction it was already
    #: trending, or set the first direction this window can establish.
    BREAK_OF_STRUCTURE = "break_of_structure"
    #: Price closed beyond a confirmed swing against the prevailing direction.
    CHANGE_OF_CHARACTER = "change_of_character"


class StructureEvent(BaseModel):
    """A close beyond a previously-confirmed swing.

    Deliberately measured on the *close*, not the wick: a wick beyond a level
    that closes back inside is a sweep (see `LiquiditySweep`), and conflating
    the two would report a rejection as a break.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: StructureEventKind
    direction: PriceDirection
    index: int = Field(ge=0)
    timestamp: datetime
    broken_level: float = Field(gt=0)
    broken_swing_timestamp: datetime
    close: float = Field(gt=0)


class LiquiditySweep(BaseModel):
    """A wick beyond a confirmed swing that closed back inside it.

    The deterministic half of what traders call a liquidity sweep or stop run:
    price traded through a level where resting orders would sit and did not
    hold there. ORION reports the geometry and nothing about intent — it
    cannot see order flow, and does not claim to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: PriceDirection
    index: int = Field(ge=0)
    timestamp: datetime
    swept_level: float = Field(gt=0)
    swept_swing_timestamp: datetime
    extreme: float = Field(gt=0, description="the high (or low) that traded beyond the level.")
    close: float = Field(gt=0)


class FibonacciLevel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ratio: float = Field(gt=0, lt=1)
    price: float = Field(gt=0)


class FibonacciRetracement(BaseModel):
    """Retracement levels between one real, opposing pair of confirmed swings.

    `direction` is the direction of the *impulse* the levels retrace: BULLISH
    when the low formed before the high. `current_ratio` is where the last
    close sits inside that range, and is `None` when price has left the range
    entirely — a retracement of an impulse price has already exceeded is not a
    retracement, and reporting a number outside [0, 1] as one would be a claim
    the geometry does not support.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: PriceDirection
    anchor_high: float = Field(gt=0)
    anchor_low: float = Field(gt=0)
    anchor_high_timestamp: datetime
    anchor_low_timestamp: datetime
    levels: tuple[FibonacciLevel, ...] = Field(min_length=1)
    current_ratio: float | None = None

    @model_validator(mode="after")
    def _check_anchors_are_ordered_and_distinct(self) -> FibonacciRetracement:
        if self.anchor_high <= self.anchor_low:
            raise ValueError(
                f"anchor_high ({self.anchor_high}) must be above anchor_low ({self.anchor_low})"
            )
        return self

    @property
    def range_size(self) -> float:
        return self.anchor_high - self.anchor_low


def find_swings(bars: list[OHLCVBar], *, lookback: int = 2) -> tuple[SwingPoint, ...]:
    """Every fractal swing in `bars`, in index order, high and low interleaved.

    A bar is a swing high (low) when its high (low) is the maximum (minimum)
    of the `2 * lookback + 1` bars centred on it — the exact rule
    `orion.analysis.structure.find_swing_points` has always used, which now
    calls this function rather than repeating it. A bar can be both, which is
    correct for an inside-range bar and is what the original produced too.

    Returns an empty tuple when `bars` is too short to centre a window, and
    raises `ValueError` for a non-positive `lookback` rather than silently
    treating every bar as a swing.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be at least 1, got {lookback}")

    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    swings: list[SwingPoint] = []

    for index in range(lookback, len(bars) - lookback):
        window = slice(index - lookback, index + lookback + 1)
        if highs[index] == max(highs[window]):
            swings.append(
                SwingPoint(
                    kind=SwingKind.HIGH,
                    index=index,
                    timestamp=bars[index].timestamp,
                    price=highs[index],
                    confirmed_at_index=index + lookback,
                )
            )
        if lows[index] == min(lows[window]):
            swings.append(
                SwingPoint(
                    kind=SwingKind.LOW,
                    index=index,
                    timestamp=bars[index].timestamp,
                    price=lows[index],
                    confirmed_at_index=index + lookback,
                )
            )

    return tuple(swings)


def find_fair_value_gaps(bars: list[OHLCVBar]) -> tuple[FairValueGap, ...]:
    """Every three-bar imbalance in `bars`, oldest first.

    Fewer than three bars cannot form one, so a short series returns an empty
    tuple rather than a guess. There is no minimum-size threshold: a gap is a
    gap, and whether one is large enough to matter is the reader's judgment,
    not a number quietly chosen here.
    """
    if len(bars) < 3:
        return ()

    gaps: list[FairValueGap] = []
    for index in range(1, len(bars) - 1):
        before, middle, after = bars[index - 1], bars[index], bars[index + 1]

        if after.low > before.high:
            lower, upper, direction = before.high, after.low, PriceDirection.BULLISH
        elif after.high < before.low:
            lower, upper, direction = after.high, before.low, PriceDirection.BEARISH
        else:
            continue

        # Filled by any later bar trading back inside the range. Checked over
        # the bars actually supplied and nothing else: a gap this window shows
        # as open may well have been filled by a bar the window does not hold,
        # which is a limit of the data, not a claim about the future.
        filled = any(bar.low < upper and bar.high > lower for bar in bars[index + 2 :])

        gaps.append(
            FairValueGap(
                direction=direction,
                index=index,
                timestamp=middle.timestamp,
                lower=lower,
                upper=upper,
                filled=filled,
            )
        )

    return tuple(gaps)


def _confirmed_before(
    swings: tuple[SwingPoint, ...], bar_index: int, kind: SwingKind
) -> SwingPoint | None:
    """The latest swing of `kind` knowable strictly before `bar_index`.

    THE LOOK-AHEAD GUARD. A fractal at index `i` needs `lookback` bars after it,
    so it is not knowable until `i + lookback`. Comparing bar `j` against a
    swing confirmed at or after `j` would let information from the future decide
    that the past broke — the leakage Mission 6 requires a check for.
    """
    candidates = [
        swing
        for swing in swings
        if swing.kind is kind and swing.confirmed_at_index < bar_index and swing.index < bar_index
    ]
    return candidates[-1] if candidates else None


def find_structure_events(bars: list[OHLCVBar], *, lookback: int = 2) -> tuple[StructureEvent, ...]:
    """Every break of structure and change of character in `bars`, oldest first.

    A bullish break is a close above the most recent confirmed swing high; a
    bearish break is a close below the most recent confirmed swing low. Whether
    a break is a BOS or a CHOCH depends only on the direction of the break
    before it: same direction continues the structure, opposite direction
    changes it.

    THE FIRST BREAK IN A WINDOW IS A BOS, DELIBERATELY. With no earlier break
    there is no prevailing direction to change, and labelling it a change of
    character would assert something about bars this window does not contain.
    """
    swings = find_swings(bars, lookback=lookback)
    if not swings:
        return ()

    events: list[StructureEvent] = []
    prevailing: PriceDirection | None = None
    # Each level may be broken once: without this, every subsequent bar that
    # stayed beyond a swing would be reported as another break of it.
    broken_high: float | None = None
    broken_low: float | None = None

    for index, bar in enumerate(bars):
        swing_high = _confirmed_before(swings, index, SwingKind.HIGH)
        swing_low = _confirmed_before(swings, index, SwingKind.LOW)

        direction: PriceDirection | None = None
        swing: SwingPoint | None = None
        if (
            swing_high is not None
            and bar.close > swing_high.price
            and broken_high != swing_high.price
        ):
            direction, swing = PriceDirection.BULLISH, swing_high
            broken_high = swing_high.price
        elif (
            swing_low is not None and bar.close < swing_low.price and broken_low != swing_low.price
        ):
            direction, swing = PriceDirection.BEARISH, swing_low
            broken_low = swing_low.price

        if direction is None or swing is None:
            continue

        kind = (
            StructureEventKind.CHANGE_OF_CHARACTER
            if prevailing is not None and prevailing is not direction
            else StructureEventKind.BREAK_OF_STRUCTURE
        )
        events.append(
            StructureEvent(
                kind=kind,
                direction=direction,
                index=index,
                timestamp=bar.timestamp,
                broken_level=swing.price,
                broken_swing_timestamp=swing.timestamp,
                close=bar.close,
            )
        )
        prevailing = direction

    return tuple(events)


def find_liquidity_sweeps(bars: list[OHLCVBar], *, lookback: int = 2) -> tuple[LiquiditySweep, ...]:
    """Every wick beyond a confirmed swing that closed back inside, oldest first.

    Bearish (a sweep of the liquidity above a swing high): the bar's high
    exceeds the level and its close is back below it. Bullish is the mirror.
    The close condition is what separates this from `find_structure_events` —
    the same bar cannot be both, because one requires the close beyond the
    level and the other requires it back inside.
    """
    swings = find_swings(bars, lookback=lookback)
    if not swings:
        return ()

    sweeps: list[LiquiditySweep] = []
    for index, bar in enumerate(bars):
        swing_high = _confirmed_before(swings, index, SwingKind.HIGH)
        if swing_high is not None and bar.high > swing_high.price and bar.close <= swing_high.price:
            sweeps.append(
                LiquiditySweep(
                    direction=PriceDirection.BEARISH,
                    index=index,
                    timestamp=bar.timestamp,
                    swept_level=swing_high.price,
                    swept_swing_timestamp=swing_high.timestamp,
                    extreme=bar.high,
                    close=bar.close,
                )
            )
            continue

        swing_low = _confirmed_before(swings, index, SwingKind.LOW)
        if swing_low is not None and bar.low < swing_low.price and bar.close >= swing_low.price:
            sweeps.append(
                LiquiditySweep(
                    direction=PriceDirection.BULLISH,
                    index=index,
                    timestamp=bar.timestamp,
                    swept_level=swing_low.price,
                    swept_swing_timestamp=swing_low.timestamp,
                    extreme=bar.low,
                    close=bar.close,
                )
            )

    return tuple(sweeps)


def compute_fibonacci_retracement(
    bars: list[OHLCVBar], *, lookback: int = 2
) -> FibonacciRetracement | None:
    """Retracement levels for the most recent opposing swing pair, or `None`.

    RETURNS `None` RATHER THAN INVENTING AN ANCHOR. There must be both a
    confirmed swing high and a confirmed swing low, they must be distinct bars,
    and the high must be strictly above the low. A window with only highs, only
    lows, or a degenerate pair has no retracement to report, and drawing one
    from the window's extremes instead would be a level ORION made up.

    The impulse direction is read from which anchor formed first: low then high
    is a bullish impulse, retraced downward from the high.
    """
    swings = find_swings(bars, lookback=lookback)
    highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]
    lows = [swing for swing in swings if swing.kind is SwingKind.LOW]
    if not highs or not lows:
        return None

    high, low = highs[-1], lows[-1]
    if high.index == low.index or high.price <= low.price:
        return None

    span = high.price - low.price
    direction = PriceDirection.BULLISH if low.index < high.index else PriceDirection.BEARISH
    levels = tuple(
        FibonacciLevel(
            ratio=ratio,
            # Measured back from the end of the impulse, so a level is always
            # inside the anchor range for either direction.
            price=(high.price - span * ratio)
            if direction is PriceDirection.BULLISH
            else (low.price + span * ratio),
        )
        for ratio in FIBONACCI_RATIOS
    )

    last_close = bars[-1].close
    if direction is PriceDirection.BULLISH:
        ratio = (high.price - last_close) / span
    else:
        ratio = (last_close - low.price) / span
    current_ratio = ratio if 0.0 <= ratio <= 1.0 else None

    return FibonacciRetracement(
        direction=direction,
        anchor_high=high.price,
        anchor_low=low.price,
        anchor_high_timestamp=high.timestamp,
        anchor_low_timestamp=low.timestamp,
        levels=levels,
        current_ratio=current_ratio,
    )
