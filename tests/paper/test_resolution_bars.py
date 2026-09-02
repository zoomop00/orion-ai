"""Mission 9 Phase 2: intrabar resolution on the forward path.

WHAT THIS COVERS THAT PHASE 1 COULD NOT. A price that crosses a stop and comes
back before the next observation leaves no trace in any bar's close, so
last-price resolution reports HOLDING for a position that really did stop out.
These tests drive `resolve_stop_take_profit_bars` over bar ranges and assert the
crossing is seen -- and, where a bar gapped through a level, that the fill is
the gapped open rather than the level the market never offered.

THE FILL RULE IS DELIBERATELY NOT THE BACKTEST'S.
`orion.backtest.execution.resolve_stop_take_profit_ohlc` always fills at the
level, which models a resting order and is right for replaying history. Forward,
that would book a better result than the market gave, so a bar that opened
through a level fills at the open instead. Several tests below pin exactly that
difference so it cannot be "simplified" back into agreement.

Every time here is anchored to `BASE_TIME`, the same anchor the shared paper and
risk builders use, so the position's `opened_at` and the bars around it are
consistent by construction rather than by coincidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.paper.execution import (
    eligible_bars,
    resolve_stop_take_profit,
    resolve_stop_take_profit_bars,
)
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason, PaperExecutionError
from orion.paper.session import ResolutionCoverage, window_coverage
from orion.reasoning.schemas import Direction
from orion.risk.portfolio import PortfolioState, Position

from .conftest import BASE_TIME, build_portfolio, build_position

SYMBOL = "XAUUSD"
ZERO_COST_POLICY = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)

#: `build_position`'s own default. Named here because the entry-bar exclusion
#: rule is expressed relative to it and every bar below is placed around it.
OPENED_AT = BASE_TIME
FIRST_BAR = OPENED_AT + timedelta(hours=1)
#: Well after the last bar any test places, so `mark_to_market` only ever moves
#: an account forward.
RESOLVE_NOW = OPENED_AT + timedelta(hours=12)


class LastPrice:
    """The minimal `ObservedPrice`: what the Phase 1 resolver sees of a bar."""

    def __init__(self, last_price: float) -> None:
        self.symbol = SYMBOL
        self.last_price = last_price


def bar(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    at: datetime = FIRST_BAR,
) -> OHLCVBar:
    return OHLCVBar(timestamp=at, open=open_, high=high, low=low, close=close, volume=1_000.0)


def flat(price: float, *, at: datetime) -> OHLCVBar:
    """A bar that went nowhere: it can trigger nothing."""
    return OHLCVBar(timestamp=at, open=price, high=price, low=price, close=price, volume=1_000.0)


def snapshot(*bars: OHLCVBar, symbol: str = SYMBOL) -> MarketSnapshot:
    return MarketSnapshot(
        source=DataSourceMeta(
            provider="fake", symbol=symbol, timeframe=Timeframe.H1, retrieved_at=BASE_TIME
        ),
        bars=list(bars),
    )


def long_position() -> Position:
    return build_position(
        direction=Direction.LONG, entry_price=2400.0, stop_loss=2350.0, take_profit=2500.0
    )


def short_position() -> Position:
    return build_position(
        direction=Direction.SHORT, entry_price=2400.0, stop_loss=2450.0, take_profit=2300.0
    )


def account(position: Position) -> PortfolioState:
    return build_portfolio(open_position=position, cash=90_000.0)


# ------------------------------------------------------------- detection


class TestDetectionUsesTheBarRange:
    def test_a_long_stops_out_on_the_bar_low(self) -> None:
        held = long_position()
        # Close well above the stop: a last price would see nothing.
        touched = bar(open_=2400.0, high=2405.0, low=2345.0, close=2400.0)
        _, closed, trigger = resolve_stop_take_profit_bars(
            account(held), snapshot(touched), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT
        assert trigger is touched

    def test_a_long_takes_profit_on_the_bar_high(self) -> None:
        held = long_position()
        touched = bar(open_=2400.0, high=2505.0, low=2395.0, close=2400.0)
        _, closed, trigger = resolve_stop_take_profit_bars(
            account(held), snapshot(touched), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
        assert trigger is touched

    def test_a_short_stops_out_on_the_bar_high(self) -> None:
        held = short_position()
        touched = bar(open_=2400.0, high=2455.0, low=2395.0, close=2400.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(touched), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT

    def test_a_short_takes_profit_on_the_bar_low(self) -> None:
        held = short_position()
        touched = bar(open_=2400.0, high=2405.0, low=2295.0, close=2400.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(touched), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT

    def test_both_levels_in_one_bar_resolves_stop_first(self) -> None:
        """M6-Q2, restated forward rather than re-decided: OHLC cannot say which
        came first, so the stop is assumed to have already happened."""
        held = long_position()
        both = bar(open_=2400.0, high=2510.0, low=2340.0, close=2450.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(both), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT

    def test_both_levels_in_one_bar_resolves_stop_first_for_a_short_too(self) -> None:
        held = short_position()
        both = bar(open_=2400.0, high=2460.0, low=2290.0, close=2400.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(both), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT

    def test_a_range_that_touches_nothing_holds(self) -> None:
        held = long_position()
        quiet = bar(open_=2400.0, high=2420.0, low=2380.0, close=2410.0)
        marked, closed, trigger = resolve_stop_take_profit_bars(
            account(held), snapshot(quiet), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is None
        assert trigger is None
        assert marked.open_position == held


class TestBarDetectionSeesWhatALastPriceCannot:
    def test_the_same_bar_holds_on_last_price_and_closes_on_its_range(self) -> None:
        """The whole justification for Phase 2, in one comparison. Identical
        account, identical bar: its close says nothing happened, its range says
        the stop was hit."""
        held = long_position()
        wick = bar(open_=2400.0, high=2405.0, low=2345.0, close=2400.0)

        _, by_last_price = resolve_stop_take_profit(
            account(held),
            LastPrice(wick.close),
            policy=ZERO_COST_POLICY,
            now=RESOLVE_NOW,
        )
        _, by_range, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(wick), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )

        assert by_last_price is None
        assert by_range is not None
        assert by_range.close_reason is CloseReason.STOP_LOSS_HIT


# ----------------------------------------------------- the worst-of gap rule


class TestTheWorstOfGapRule:
    def test_a_long_gapping_through_its_stop_fills_at_the_open(self) -> None:
        """The bar opened below the stop, so the stop was never available once
        the bar began. Filling there would book a loss the market did not
        offer."""
        held = long_position()
        gapped = bar(open_=2300.0, high=2320.0, low=2290.0, close=2310.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(gapped), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT
        assert closed.exit_price == pytest.approx(2300.0)
        assert closed.exit_price < held.stop_loss

    def test_a_long_gapping_through_its_target_fills_at_the_open(self) -> None:
        held = long_position()
        gapped = bar(open_=2560.0, high=2580.0, low=2550.0, close=2570.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(gapped), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
        assert closed.exit_price == pytest.approx(2560.0)
        assert closed.exit_price > held.take_profit

    def test_a_short_gapping_through_its_stop_fills_at_the_open(self) -> None:
        held = short_position()
        gapped = bar(open_=2520.0, high=2540.0, low=2510.0, close=2530.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(gapped), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT
        assert closed.exit_price == pytest.approx(2520.0)
        assert closed.exit_price > held.stop_loss

    def test_a_short_gapping_through_its_target_fills_at_the_open(self) -> None:
        held = short_position()
        gapped = bar(open_=2250.0, high=2270.0, low=2240.0, close=2260.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(gapped), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.close_reason is CloseReason.TAKE_PROFIT_HIT
        assert closed.exit_price == pytest.approx(2250.0)
        assert closed.exit_price < held.take_profit

    def test_a_bar_that_opened_inside_the_range_still_fills_at_the_level(self) -> None:
        """No gap, no special case: the ordinary path is unchanged, and the
        level is what a stop order would have got."""
        held = long_position()
        ordinary = bar(open_=2400.0, high=2405.0, low=2345.0, close=2400.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(ordinary), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert closed.exit_price == pytest.approx(held.stop_loss)

    def test_a_gapped_stop_books_a_worse_loss_than_the_level_would(self) -> None:
        """The forward/backtest divergence, pinned. `orion.backtest` fills at
        the level; a paper account doing the same would report a smaller loss
        than the gap actually handed out."""
        held = long_position()
        gapped = bar(open_=2300.0, high=2320.0, low=2290.0, close=2310.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(gapped), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        at_the_level = (held.stop_loss - held.entry_price) * held.quantity
        assert closed.realized_pnl < at_the_level

    def test_spread_and_slippage_still_worsen_the_fill_on_top(self) -> None:
        held = long_position()
        gapped = bar(open_=2300.0, high=2320.0, low=2290.0, close=2310.0)
        policy = PaperExecutionPolicy(spread_bps=2.0, slippage_bps=1.0, fee_bps=1.0)
        _, closed, _ = resolve_stop_take_profit_bars(
            account(held), snapshot(gapped), policy=policy, now=RESOLVE_NOW
        )
        assert closed is not None
        # Closing a LONG sells: a worse fill is a lower price than the open.
        assert closed.exit_price < 2300.0
        assert closed.fee > 0


# --------------------------------------------------- scanning a bar sequence


class TestScanningASequence:
    def test_the_first_triggering_bar_closes_and_later_bars_are_ignored(self) -> None:
        held = long_position()
        first_hit = bar(open_=2400.0, high=2405.0, low=2345.0, close=2400.0, at=FIRST_BAR)
        later_hit = bar(
            open_=2400.0,
            high=2600.0,
            low=2390.0,
            close=2400.0,
            at=FIRST_BAR + timedelta(hours=1),
        )
        _, closed, trigger = resolve_stop_take_profit_bars(
            account(held),
            snapshot(first_hit, later_hit),
            policy=ZERO_COST_POLICY,
            now=RESOLVE_NOW,
        )
        assert closed is not None
        # The stop, from the earlier bar -- not the target from the later one.
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT
        assert trigger is first_hit

    def test_quiet_bars_before_the_hit_do_not_prevent_it(self) -> None:
        held = long_position()
        quiet = flat(2400.0, at=FIRST_BAR)
        hit = bar(
            open_=2400.0,
            high=2405.0,
            low=2345.0,
            close=2400.0,
            at=FIRST_BAR + timedelta(hours=1),
        )
        _, closed, trigger = resolve_stop_take_profit_bars(
            account(held), snapshot(quiet, hit), policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is not None
        assert trigger is hit

    def test_a_bar_at_or_before_the_entry_is_excluded(self) -> None:
        """The bar the position was opened from had already traded its range
        before the entry existed. Honouring it would close a position on prices
        that predate it."""
        held = long_position()
        at_entry = bar(open_=2400.0, high=2405.0, low=2300.0, close=2400.0, at=OPENED_AT)
        earlier = bar(
            open_=2400.0,
            high=2405.0,
            low=2300.0,
            close=2400.0,
            at=OPENED_AT - timedelta(hours=1),
        )
        _, closed, trigger = resolve_stop_take_profit_bars(
            account(held),
            snapshot(earlier, at_entry),
            policy=ZERO_COST_POLICY,
            now=RESOLVE_NOW,
        )
        assert closed is None
        assert trigger is None

    def test_eligible_bars_is_strictly_after_the_entry(self) -> None:
        held = long_position()
        at_entry = flat(2400.0, at=OPENED_AT)
        after = flat(2400.0, at=FIRST_BAR)
        assert eligible_bars(snapshot(at_entry, after), held) == (after,)
        assert eligible_bars(snapshot(at_entry), held) == ()
        assert eligible_bars(snapshot(after), None) == ()

    def test_a_window_entirely_before_the_entry_leaves_the_account_untouched(self) -> None:
        """No eligible bar means no price this function is entitled to value the
        account at -- marking it against a pre-entry bar would be a fabricated
        valuation, so the stored state is returned unchanged."""
        held = long_position()
        stored = account(held)
        marked, closed, trigger = resolve_stop_take_profit_bars(
            stored,
            snapshot(flat(2400.0, at=OPENED_AT)),
            policy=ZERO_COST_POLICY,
            now=RESOLVE_NOW,
        )
        assert closed is None
        assert trigger is None
        assert marked is stored


class TestFailClosed:
    def test_a_symbol_mismatch_raises(self) -> None:
        """The check the backtest's bar resolver structurally cannot make: a
        bare `OHLCVBar` does not know what it is a bar of."""
        held = long_position()
        with pytest.raises(PaperExecutionError, match="does not match snapshot symbol"):
            resolve_stop_take_profit_bars(
                account(held),
                snapshot(flat(2400.0, at=FIRST_BAR), symbol="EURUSD"),
                policy=ZERO_COST_POLICY,
                now=RESOLVE_NOW,
            )

    def test_a_flat_account_closes_nothing(self) -> None:
        flat_account = build_portfolio(open_position=None)
        marked, closed, trigger = resolve_stop_take_profit_bars(
            flat_account,
            snapshot(bar(open_=2400.0, high=2600.0, low=2300.0, close=2400.0)),
            policy=ZERO_COST_POLICY,
            now=RESOLVE_NOW,
        )
        assert closed is None
        assert trigger is None
        assert marked.open_position is None

    def test_no_bar_ever_produces_a_fill(self) -> None:
        """Resolution closes; it cannot open. Swept across the geometry."""
        held = long_position()
        for low, high in ((2300.0, 2310.0), (2380.0, 2420.0), (2590.0, 2600.0)):
            marked, closed, _ = resolve_stop_take_profit_bars(
                account(held),
                snapshot(bar(open_=(low + high) / 2, high=high, low=low, close=(low + high) / 2)),
                policy=ZERO_COST_POLICY,
                now=RESOLVE_NOW,
            )
            # Either it closed, or it held -- never a second open position.
            assert marked.open_position in (None, held)


# ------------------------------------------------------------- coverage


class TestWindowCoverage:
    def test_a_contiguous_window_reaching_the_entry_is_complete(self) -> None:
        """Contiguous means contiguous *across the entry too*: the bar covering
        `opened_at` is present, so the step from it to the first examined bar is
        one interval like every other."""
        held = long_position()
        bars = [flat(2400.0, at=OPENED_AT - timedelta(hours=1)), flat(2400.0, at=OPENED_AT)]
        bars += [flat(2400.0, at=OPENED_AT + timedelta(hours=h)) for h in (1, 2, 3)]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.COMPLETE

    def test_a_window_starting_after_the_entry_is_truncated(self) -> None:
        held = long_position()
        bars = [flat(2400.0, at=OPENED_AT + timedelta(hours=h)) for h in (1, 2)]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.TRUNCATED

    def test_a_window_ending_at_the_entry_is_truncated(self) -> None:
        held = long_position()
        assert (
            window_coverage(snapshot(flat(2400.0, at=OPENED_AT)), held)
            is ResolutionCoverage.TRUNCATED
        )

    def test_an_empty_window_is_truncated(self) -> None:
        assert window_coverage(snapshot(), long_position()) is ResolutionCoverage.TRUNCATED

    def test_a_hole_after_the_entry_is_gapped(self) -> None:
        held = long_position()
        bars = [
            flat(2400.0, at=OPENED_AT),
            flat(2400.0, at=OPENED_AT + timedelta(hours=1)),
            # Four hours later: the bars in between were never returned.
            flat(2400.0, at=OPENED_AT + timedelta(hours=5)),
        ]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.GAPPED

    def test_a_hole_before_the_entry_does_not_make_the_window_gapped(self) -> None:
        """Only the since-entry window has to be complete. What the market did
        before the position existed is not this command's business -- the scan
        starts at the boundary bar, so an older hole is never reached."""
        held = long_position()
        bars = [
            flat(2400.0, at=OPENED_AT - timedelta(hours=9)),
            flat(2400.0, at=OPENED_AT - timedelta(hours=1)),
            flat(2400.0, at=OPENED_AT),
            flat(2400.0, at=OPENED_AT + timedelta(hours=1)),
            flat(2400.0, at=OPENED_AT + timedelta(hours=2)),
        ]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.COMPLETE

    def test_a_flat_account_has_nothing_to_cover(self) -> None:
        assert window_coverage(snapshot(), None) is ResolutionCoverage.COMPLETE


class TestTheGapAcrossTheEntryBoundaryIsCounted:
    """REGRESSION. The gap scan once ran over the eligible bars alone, which
    left the entry boundary itself unchecked: a window holding the last bar
    before the entry and then nothing until hours later is contiguous *within*
    its eligible set, so it claimed COMPLETE while never seeing the hours in
    between. It reached back and it had no internal hole -- the hole was
    exactly at the seam between those two checks.

    This is the ordinary shape of a position opened just after the last bar
    before a market weekend: the boundary bar is the Friday one, the first
    eligible bar is the Monday one, and the whole weekend is unexamined.
    """

    def test_a_hole_spanning_the_entry_is_gapped_not_complete(self) -> None:
        held = long_position()
        bars = [
            # The last bar before the entry -- the window does reach back.
            flat(2400.0, at=OPENED_AT - timedelta(hours=1)),
            # Nothing for five hours. The position existed for all of them.
            flat(2400.0, at=OPENED_AT + timedelta(hours=5)),
            flat(2400.0, at=OPENED_AT + timedelta(hours=6)),
        ]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.GAPPED

    def test_a_weekend_shaped_hole_spanning_the_entry_is_gapped(self) -> None:
        """The realistic case, at realistic scale: a Friday-evening entry, then
        the market's own weekend, then Monday. Every eligible bar is contiguous
        with the next; only the step across the entry is not."""
        held = long_position()
        friday_close = OPENED_AT - timedelta(minutes=30)
        bars = [flat(2400.0, at=friday_close - timedelta(hours=1)), flat(2400.0, at=friday_close)]
        monday_open = OPENED_AT + timedelta(hours=48)
        bars += [flat(2400.0, at=monday_open + timedelta(hours=h)) for h in range(4)]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.GAPPED

    def test_a_contiguous_boundary_is_still_complete(self) -> None:
        """The other half: the boundary bar sitting one interval before the
        first examined bar -- which is what a healthy window looks like when
        the entry landed mid-bar -- must stay COMPLETE."""
        held = long_position()
        bars = [
            flat(2400.0, at=OPENED_AT),
            flat(2400.0, at=OPENED_AT + timedelta(hours=1)),
            flat(2400.0, at=OPENED_AT + timedelta(hours=2)),
        ]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.COMPLETE

    def test_an_entry_landing_between_bars_is_still_complete(self) -> None:
        """Production's actual shape: `opened_at` is wall-clock, minutes after
        the bar it was decided from, so the boundary bar sits slightly before
        the entry and one interval before the first examined bar."""
        held = build_position(
            direction=Direction.LONG,
            entry_price=2400.0,
            stop_loss=2350.0,
            take_profit=2500.0,
            opened_at=OPENED_AT + timedelta(minutes=5),
        )
        bars = [
            flat(2400.0, at=OPENED_AT),
            flat(2400.0, at=OPENED_AT + timedelta(hours=1)),
            flat(2400.0, at=OPENED_AT + timedelta(hours=2)),
        ]
        assert window_coverage(snapshot(*bars), held) is ResolutionCoverage.COMPLETE

    def test_the_boundary_bar_is_still_never_examined_for_a_trigger(self) -> None:
        """The boundary bar earns its place in the *coverage* scan only. The
        resolver must still ignore it: its range predates the entry, and
        counting it would close a position on prices from before it existed."""
        held = long_position()
        boundary_hit = bar(open_=2400.0, high=2405.0, low=2300.0, close=2400.0, at=OPENED_AT)
        after = flat(2400.0, at=OPENED_AT + timedelta(hours=1))
        window = snapshot(boundary_hit, after)

        assert window_coverage(window, held) is ResolutionCoverage.COMPLETE
        _, closed, trigger = resolve_stop_take_profit_bars(
            account(held), window, policy=ZERO_COST_POLICY, now=RESOLVE_NOW
        )
        assert closed is None
        assert trigger is None
