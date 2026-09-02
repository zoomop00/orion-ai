"""The deterministic price-action primitives, on bars built to order.

EVERY BAR HERE IS HAND-SPECIFIED, AND THAT IS DELIBERATE. The rest of the suite
reuses a shared rising series because what it is testing does not depend on the
exact shape. These detectors do: a fair value gap is three bars in a specific
relationship, a change of character needs a break in one direction followed by
one in the other. So the bars are written out, and each test says in words what
shape it built and why that shape must produce the result asserted.

THE LOOK-AHEAD TESTS ARE THE ONES THAT MATTER MOST. A fractal swing is not
knowable until `lookback` bars after it forms, so a detector that compares a bar
against a swing confirmed later has used the future to decide the past. Mission
6 lists look-ahead checks as a required validation; these are that check for
this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.analysis.price_action import (
    FIBONACCI_RATIOS,
    PriceDirection,
    StructureEventKind,
    SwingKind,
    compute_fibonacci_retracement,
    find_fair_value_gaps,
    find_liquidity_sweeps,
    find_structure_events,
    find_swings,
)
from orion.analysis.structure import find_swing_points
from orion.data.models import OHLCVBar

BASE_TIME = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)


def bar(index: int, *, high: float, low: float, close: float | None = None) -> OHLCVBar:
    """One bar at `index` hours past BASE_TIME. `open` and `close` default to
    the midpoint so a test only states the values it actually cares about."""
    mid = (high + low) / 2
    return OHLCVBar(
        timestamp=BASE_TIME + timedelta(hours=index),
        open=mid,
        high=high,
        low=low,
        close=mid if close is None else close,
    )


def flat(index: int, price: float) -> OHLCVBar:
    """A bar with no range at all, for padding a series to a usable length."""
    return bar(index, high=price, low=price, close=price)


# --------------------------------------------------------------- swings


class TestSwings:
    def test_a_peak_surrounded_by_lower_bars_is_a_swing_high(self) -> None:
        bars = [
            bar(0, high=10, low=8),
            bar(1, high=11, low=9),
            bar(2, high=20, low=10),  # the peak
            bar(3, high=12, low=9),
            bar(4, high=11, low=8),
        ]

        swings = find_swings(bars, lookback=2)

        highs = [swing for swing in swings if swing.kind is SwingKind.HIGH]
        assert [swing.index for swing in highs] == [2]
        assert highs[0].price == 20
        assert highs[0].timestamp == bars[2].timestamp

    def test_a_trough_surrounded_by_higher_bars_is_a_swing_low(self) -> None:
        bars = [
            bar(0, high=12, low=10),
            bar(1, high=11, low=9),
            bar(2, high=10, low=2),  # the trough
            bar(3, high=11, low=9),
            bar(4, high=12, low=10),
        ]

        lows = [swing for swing in find_swings(bars, lookback=2) if swing.kind is SwingKind.LOW]

        assert [swing.index for swing in lows] == [2]
        assert lows[0].price == 2

    def test_a_swing_is_confirmed_lookback_bars_after_it_forms(self) -> None:
        """The whole basis of the look-ahead guard, asserted on the type."""
        bars = [bar(i, high=10 + i, low=5) for i in range(7)]
        bars[3] = bar(3, high=99, low=5)

        swings = find_swings(bars, lookback=2)
        peak = next(swing for swing in swings if swing.price == 99)

        assert peak.index == 3
        assert peak.confirmed_at_index == 5

    def test_a_series_shorter_than_the_window_has_no_swings(self) -> None:
        assert find_swings([bar(i, high=10, low=5) for i in range(4)], lookback=2) == ()

    def test_an_empty_series_has_no_swings(self) -> None:
        assert find_swings([], lookback=2) == ()

    @pytest.mark.parametrize("lookback", [0, -1])
    def test_a_non_positive_lookback_is_refused(self, lookback: int) -> None:
        """Rather than silently treating every bar as a swing, which a window
        of size one would do."""
        with pytest.raises(ValueError, match="at least 1"):
            find_swings([bar(i, high=10, low=5) for i in range(9)], lookback=lookback)

    def test_the_existing_price_only_view_still_agrees_with_it(self) -> None:
        """`find_swing_points` now delegates here. Its contract — the same
        prices, in the same order, as two lists — must not have moved, because
        `Evidence` and every support/resistance test depend on it."""
        bars = [
            bar(0, high=10, low=8),
            bar(1, high=11, low=9),
            bar(2, high=20, low=3),
            bar(3, high=12, low=9),
            bar(4, high=11, low=8),
            bar(5, high=13, low=2),
            bar(6, high=12, low=7),
            bar(7, high=11, low=6),
        ]

        highs, lows = find_swing_points(bars, lookback=2)
        swings = find_swings(bars, lookback=2)

        assert highs == [swing.price for swing in swings if swing.kind is SwingKind.HIGH]
        assert lows == [swing.price for swing in swings if swing.kind is SwingKind.LOW]


# ----------------------------------------------------- fair value gaps


class TestFairValueGaps:
    def test_a_three_bar_upward_imbalance_is_a_bullish_gap(self) -> None:
        """Bar 2's low (30) is strictly above bar 0's high (20), so nothing
        traded between 20 and 30 across those three bars."""
        bars = [bar(0, high=20, low=10), bar(1, high=35, low=15), bar(2, high=40, low=30)]

        gaps = find_fair_value_gaps(bars)

        assert len(gaps) == 1
        assert gaps[0].direction is PriceDirection.BULLISH
        assert (gaps[0].lower, gaps[0].upper) == (20, 30)
        assert gaps[0].size == 10
        assert gaps[0].index == 1
        assert gaps[0].timestamp == bars[1].timestamp

    def test_a_three_bar_downward_imbalance_is_a_bearish_gap(self) -> None:
        bars = [bar(0, high=40, low=30), bar(1, high=35, low=15), bar(2, high=20, low=10)]

        gaps = find_fair_value_gaps(bars)

        assert len(gaps) == 1
        assert gaps[0].direction is PriceDirection.BEARISH
        assert (gaps[0].lower, gaps[0].upper) == (20, 30)

    def test_overlapping_bars_leave_no_gap(self) -> None:
        """THE NEGATIVE CASE THAT MATTERS. Ordinary overlapping bars are the
        normal state of a market; a detector that reported a gap here would
        report one almost everywhere."""
        bars = [bar(i, high=20 + i, low=10 + i) for i in range(6)]

        assert find_fair_value_gaps(bars) == ()

    def test_a_gap_later_traded_back_into_is_marked_filled(self) -> None:
        bars = [
            bar(0, high=20, low=10),
            bar(1, high=35, low=15),
            bar(2, high=40, low=30),
            bar(3, high=32, low=25),  # trades back inside 20..30
        ]

        gaps = find_fair_value_gaps(bars)

        assert len(gaps) == 1
        assert gaps[0].filled is True

    def test_a_gap_never_revisited_stays_unfilled(self) -> None:
        bars = [
            bar(0, high=20, low=10),
            bar(1, high=35, low=15),
            bar(2, high=40, low=30),
            bar(3, high=45, low=38),
            bar(4, high=50, low=42),
        ]

        gaps = find_fair_value_gaps(bars)

        # A strongly trending series leaves several gaps; the one under test is
        # the 20..30 range, and it is asserted by its bounds rather than by
        # position so the test says what it means.
        original = next(gap for gap in gaps if (gap.lower, gap.upper) == (20, 30))
        assert original.filled is False

    @pytest.mark.parametrize("count", [0, 1, 2])
    def test_fewer_than_three_bars_cannot_form_one(self, count: int) -> None:
        """FVG DETECTION ONLY WHEN THE EVIDENCE SUPPORTS IT. Three bars are the
        definition; two are not a gap that happens to be unmeasurable."""
        bars = [bar(i, high=20 + i * 20, low=10 + i * 20) for i in range(count)]

        assert find_fair_value_gaps(bars) == ()

    def test_a_touching_boundary_is_not_a_gap(self) -> None:
        """Strictly above, not at or above: bars that touch have traded at that
        price, so there is no untraded range between them."""
        bars = [bar(0, high=20, low=10), bar(1, high=35, low=15), bar(2, high=40, low=20)]

        assert find_fair_value_gaps(bars) == ()


# ------------------------------------------------------ structure events


def _break_series() -> list[OHLCVBar]:
    """Bars that form a swing high, then close above it.

    Indices 0-4 put a peak of 30 at index 2, confirmed at index 4. Index 5
    closes at 35, above that peak. Nothing before index 5 closes above 30, so
    there is exactly one bullish break and it is the first in the window.
    """
    return [
        bar(0, high=20, low=10, close=15),
        bar(1, high=22, low=12, close=17),
        bar(2, high=30, low=14, close=20),
        bar(3, high=21, low=11, close=16),
        bar(4, high=20, low=10, close=15),
        bar(5, high=36, low=15, close=35),
    ]


class TestStructureEvents:
    def test_a_close_above_a_confirmed_swing_high_is_a_bullish_break(self) -> None:
        events = find_structure_events(_break_series(), lookback=2)

        assert len(events) == 1
        assert events[0].kind is StructureEventKind.BREAK_OF_STRUCTURE
        assert events[0].direction is PriceDirection.BULLISH
        assert events[0].broken_level == 30
        assert events[0].close == 35
        assert events[0].index == 5

    def test_the_first_break_in_a_window_is_never_a_change_of_character(self) -> None:
        """With no earlier break there is no prevailing direction to change,
        and labelling one would assert something about bars outside the
        window."""
        events = find_structure_events(_break_series(), lookback=2)

        assert all(event.kind is StructureEventKind.BREAK_OF_STRUCTURE for event in events)

    def test_a_break_the_other_way_after_one_is_a_change_of_character(self) -> None:
        """Bullish break at index 5, then a swing low forms and price closes
        below it — the opposite direction, so the second event changes
        character rather than continuing."""
        bars = _break_series() + [
            bar(6, high=40, low=34, close=38),
            bar(7, high=39, low=33, close=36),
            bar(8, high=38, low=28, close=32),  # swing low of 28 at index 8
            bar(9, high=37, low=31, close=34),
            bar(10, high=36, low=32, close=35),
            bar(11, high=35, low=20, close=22),  # closes below 28
        ]

        events = find_structure_events(bars, lookback=2)

        assert [event.kind for event in events] == [
            StructureEventKind.BREAK_OF_STRUCTURE,
            StructureEventKind.CHANGE_OF_CHARACTER,
        ]
        assert events[1].direction is PriceDirection.BEARISH
        assert events[1].broken_level == 28

    def test_a_wick_beyond_a_level_that_closes_back_inside_is_not_a_break(self) -> None:
        """THE DISTINCTION THIS MODULE EXISTS TO KEEP. A break is measured on
        the close; a wick that closes back inside is a sweep, and reporting it
        as a break would present a rejection as a continuation."""
        bars = _break_series()
        bars[5] = bar(5, high=36, low=15, close=25)  # traded to 36, closed at 25

        assert find_structure_events(bars, lookback=2) == ()

    def test_a_level_is_broken_once_not_once_per_bar_that_stays_beyond_it(self) -> None:
        bars = _break_series() + [
            bar(6, high=38, low=33, close=36),
            bar(7, high=39, low=34, close=37),
        ]

        events = find_structure_events(bars, lookback=2)

        assert len([event for event in events if event.broken_level == 30]) == 1

    def test_a_break_is_never_measured_against_a_swing_confirmed_later(self) -> None:
        """THE LOOK-AHEAD GUARD, ASSERTED DIRECTLY.

        Index 4 closes at 35 — above the peak of 30 that forms at index 2. But
        that peak is not confirmed until index 4 itself, and the guard requires
        confirmation strictly before the bar being tested, so no break may be
        reported at index 4. Reporting one would mean the detector used bar 4
        to confirm the swing that bar 4 then broke.
        """
        bars = [
            bar(0, high=20, low=10, close=15),
            bar(1, high=22, low=12, close=17),
            bar(2, high=30, low=14, close=20),
            bar(3, high=21, low=11, close=16),
            bar(4, high=36, low=15, close=35),
        ]

        events = find_structure_events(bars, lookback=2)

        assert events == ()

    def test_a_series_with_no_swings_produces_no_events(self) -> None:
        assert find_structure_events([flat(i, 100.0) for i in range(3)], lookback=2) == ()


# ------------------------------------------------------- liquidity sweeps


class TestLiquiditySweeps:
    def test_a_wick_above_a_swing_high_closing_back_below_is_a_bearish_sweep(self) -> None:
        bars = _break_series()
        bars[5] = bar(5, high=36, low=15, close=25)

        sweeps = find_liquidity_sweeps(bars, lookback=2)

        assert len(sweeps) == 1
        assert sweeps[0].direction is PriceDirection.BEARISH
        assert sweeps[0].swept_level == 30
        assert sweeps[0].extreme == 36
        assert sweeps[0].close == 25

    def test_a_wick_below_a_swing_low_closing_back_above_is_a_bullish_sweep(self) -> None:
        bars = [
            bar(0, high=30, low=20, close=25),
            bar(1, high=28, low=18, close=23),
            bar(2, high=26, low=10, close=20),  # swing low of 10 at index 2
            bar(3, high=27, low=19, close=24),
            bar(4, high=29, low=21, close=26),
            bar(5, high=28, low=5, close=22),  # traded to 5, closed back at 22
        ]

        sweeps = find_liquidity_sweeps(bars, lookback=2)

        assert len(sweeps) == 1
        assert sweeps[0].direction is PriceDirection.BULLISH
        assert sweeps[0].swept_level == 10
        assert sweeps[0].extreme == 5

    def test_a_close_beyond_the_level_is_a_break_not_a_sweep(self) -> None:
        """The mirror of the structure test above: the same bar cannot be both,
        because one requires the close beyond and the other requires it back
        inside."""
        assert find_liquidity_sweeps(_break_series(), lookback=2) == ()

    def test_a_series_with_no_swings_produces_no_sweeps(self) -> None:
        assert find_liquidity_sweeps([flat(i, 100.0) for i in range(3)], lookback=2) == ()


# ---------------------------------------------------------- fibonacci


def _impulse_series() -> list[OHLCVBar]:
    """A confirmed low of 100 at index 2, then a confirmed high of 200 at
    index 7, then bars that retrace toward the middle."""
    return [
        bar(0, high=140, low=130, close=135),
        bar(1, high=135, low=120, close=125),
        bar(2, high=125, low=100, close=110),  # swing low
        bar(3, high=140, low=115, close=135),
        bar(4, high=160, low=130, close=155),
        bar(5, high=175, low=150, close=170),
        bar(6, high=185, low=165, close=180),
        bar(7, high=200, low=175, close=195),  # swing high
        bar(8, high=190, low=170, close=175),
        bar(9, high=180, low=160, close=165),
        bar(10, high=170, low=145, close=150),
    ]


class TestFibonacci:
    def test_levels_are_drawn_between_the_most_recent_opposing_swings(self) -> None:
        retracement = compute_fibonacci_retracement(_impulse_series(), lookback=2)

        assert retracement is not None
        assert retracement.anchor_low == 100
        assert retracement.anchor_high == 200
        assert retracement.direction is PriceDirection.BULLISH
        assert retracement.range_size == 100

    def test_every_conventional_ratio_is_reported_and_sits_inside_the_range(self) -> None:
        retracement = compute_fibonacci_retracement(_impulse_series(), lookback=2)

        assert retracement is not None
        assert tuple(level.ratio for level in retracement.levels) == FIBONACCI_RATIOS
        for level in retracement.levels:
            assert retracement.anchor_low <= level.price <= retracement.anchor_high
        # Measured back from the top of a bullish impulse: 0.382 of a 100-point
        # range below 200 is 161.8.
        by_ratio = {level.ratio: level.price for level in retracement.levels}
        assert by_ratio[0.382] == pytest.approx(161.8)
        assert by_ratio[0.5] == pytest.approx(150.0)

    def test_the_current_depth_is_measured_from_the_end_of_the_impulse(self) -> None:
        bars = _impulse_series()
        retracement = compute_fibonacci_retracement(bars, lookback=2)

        assert retracement is not None
        # Last close is 150, half way back down a 100-point impulse from 200.
        assert retracement.current_ratio == pytest.approx(0.5)

    def test_price_outside_the_anchor_range_reports_no_depth(self) -> None:
        """DO NOT PRETEND. A move price has already exceeded is not being
        retraced, and a ratio outside [0, 1] presented as a retracement depth
        would be a claim the geometry does not support."""
        bars = _impulse_series()
        bars[-1] = bar(10, high=260, low=250, close=255)

        retracement = compute_fibonacci_retracement(bars, lookback=2)

        assert retracement is not None
        assert retracement.current_ratio is None

    def test_no_swing_low_means_no_retracement(self) -> None:
        """FIBONACCI ONLY WHEN VALID ANCHORS EXIST. A strictly rising series has
        no confirmed swing of either kind, so there is nothing to anchor to and
        ORION reports nothing rather than using the window's extremes."""
        bars = [bar(i, high=102 + i * 2, low=100 + i * 2, close=101 + i * 2) for i in range(20)]

        assert compute_fibonacci_retracement(bars, lookback=2) is None
        assert find_swings(bars, lookback=2) == ()

    def test_a_series_too_short_to_hold_a_swing_has_no_retracement(self) -> None:
        assert compute_fibonacci_retracement([bar(i, high=10, low=5) for i in range(4)]) is None

    def test_a_degenerate_pair_is_refused(self) -> None:
        """A swing high at or below the swing low is not an impulse, and
        dividing by its range would be dividing by zero or worse."""
        bars = [flat(i, 100.0) for i in range(9)]

        assert compute_fibonacci_retracement(bars, lookback=2) is None


# ------------------------------------------------------------ determinism


def test_every_detector_is_a_pure_function_of_its_bars() -> None:
    """Same bars in, byte-identical results out, every time. Nothing here reads
    a clock, a random source or any state outside its argument."""
    bars = _impulse_series()

    assert find_swings(bars) == find_swings(bars)
    assert find_fair_value_gaps(bars) == find_fair_value_gaps(bars)
    assert find_structure_events(bars) == find_structure_events(bars)
    assert find_liquidity_sweeps(bars) == find_liquidity_sweeps(bars)
    assert compute_fibonacci_retracement(bars) == compute_fibonacci_retracement(bars)


def test_no_detector_reads_volume() -> None:
    """VOLUME IS UNTOUCHED, PROVEN BY BEHAVIOUR RATHER THAN BY READING THE CODE.

    `OHLCVBar.volume` is optional and routinely absent for spot metals, and
    `bars_to_dataframe` substitutes 0.0 when it is. Every result here is
    identical whether volume is absent, zero or a large number, so nothing in
    this module can be silently depending on that substitution.
    """
    without = _impulse_series()
    with_volume = [item.model_copy(update={"volume": 1234.5}) for item in without]
    with_zero = [item.model_copy(update={"volume": 0.0}) for item in without]

    for other in (with_volume, with_zero):
        assert find_swings(other) == find_swings(without)
        assert find_fair_value_gaps(other) == find_fair_value_gaps(without)
        assert find_structure_events(other) == find_structure_events(without)
        assert find_liquidity_sweeps(other) == find_liquidity_sweeps(without)
        assert compute_fibonacci_retracement(other) == compute_fibonacci_retracement(without)
