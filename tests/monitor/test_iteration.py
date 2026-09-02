"""One monitoring pass: the shared implementation `resolve` and `monitor` run.

WHAT IS BEING PROVEN HERE. `resolve_once` is the whole of a monitoring pass,
extracted from Phase 2's `orion.cli._run_resolve` so that the one-shot verb and
the supervised loop cannot drift apart. These tests drive it directly against
real stores, real journals and the real Phase 2 bar-aware resolver, with only
the vendor faked.

NOTHING HERE HAS AN ANTHROPIC CREDENTIAL, AND NOTHING CAN BUILD A CLIENT: the
fixture deletes the key and replaces `anthropic.Anthropic` with something that
raises. "No model was called" is proven by the call being impossible.
"""

from __future__ import annotations

from datetime import timedelta

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.data.provider_base import MarketDataProvider, MarketDataProviderError
from orion.monitor.iteration import IterationStatus, resolve_once
from orion.paper.schemas import CloseReason
from orion.paper.session import CycleOutcome, ResolutionCoverage
from orion.risk.portfolio import Position

from ..orchestrator.conftest import NOW
from ..paper.test_session import (
    POLICY,
    SYMBOL,
    RecordingJournal,
    RecordingStore,
    cycle,
    fresh_account,
)

#: The pass runs two hours after the position opened, so the window it examines
#: contains bars that genuinely postdate the entry.
RESOLVE_AT = NOW + timedelta(hours=2)
MAX_STALENESS_BARS = 3.0


def flat_bar(price: float, *, hours_after_entry: int) -> OHLCVBar:
    at = NOW + timedelta(hours=hours_after_entry)
    return OHLCVBar(timestamp=at, open=price, high=price, low=price, close=price, volume=1_000.0)


def wick_bar(*, low: float, price: float, hours_after_entry: int) -> OHLCVBar:
    """A bar whose LOW reached `low` and whose CLOSE did not move -- the shape a
    last price is blind to and the bar-aware resolver is not."""
    at = NOW + timedelta(hours=hours_after_entry)
    return OHLCVBar(timestamp=at, open=price, high=price, low=low, close=price, volume=1_000.0)


class FakeProvider(MarketDataProvider):
    """A fixed window, and a record of what was asked for."""

    def __init__(
        self,
        *bars: OHLCVBar,
        error: Exception | None = None,
        symbol: str = SYMBOL,
    ) -> None:
        self._bars = list(bars)
        self._error = error
        self._symbol = symbol
        self.calls: list[tuple[str, Timeframe, int]] = []

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        self.calls.append((symbol, timeframe, output_size))
        if self._error is not None:
            raise self._error
        return MarketSnapshot(
            source=DataSourceMeta(
                provider="fake",
                symbol=self._symbol,
                timeframe=timeframe,
                retrieved_at=RESOLVE_AT,
            ),
            bars=self._bars,
        )


def open_one() -> tuple[RecordingStore, Position]:
    """An account holding one real, Guardian-approved open position."""
    store = RecordingStore(fresh_account(), calls=[])
    journal = RecordingJournal(calls=[])
    assert cycle(store=store, journal=journal).outcome is CycleOutcome.EXECUTED
    held = store.load().state.open_position
    assert held is not None
    return store, held


def run(
    store: RecordingStore,
    journal: RecordingJournal,
    provider: MarketDataProvider | None,
    *,
    symbol: str = SYMBOL,
):
    return resolve_once(
        symbol=symbol,
        timeframe=Timeframe.H1,
        store=store,
        journal=journal,
        market_data=lambda: provider,
        policy=POLICY,
        max_staleness_bars=MAX_STALENESS_BARS,
        now=RESOLVE_AT,
    )


def window(held: Position, *bars: OHLCVBar) -> FakeProvider:
    """A provider whose window reaches back to the entry, then holds `bars`.

    The leading bar sits at `opened_at` so coverage can prove the window
    reached back; without it every result would be `TRUNCATED`.
    """
    return FakeProvider(flat_bar(held.entry_price, hours_after_entry=0), *bars)


class TestAPassUsesThePhase2BarAwareResolver:
    def test_a_wick_through_the_stop_closes_the_position(self, no_model: None) -> None:
        """The close never dipped below the stop; only the low did. A pass that
        resolved on a last price would report holding and leave it open."""
        store, held = open_one()
        journal = RecordingJournal(calls=[])
        provider = window(
            held, wick_bar(low=held.stop_loss - 5.0, price=held.entry_price, hours_after_entry=1)
        )

        result = run(store, journal, provider)

        assert result.status is IterationStatus.RESOLVED
        assert result.cycle is not None
        assert result.cycle.outcome is CycleOutcome.CLOSED
        assert result.cycle.closed_position is not None
        assert result.cycle.closed_position.close_reason is CloseReason.STOP_LOSS_HIT
        assert store.load().state.open_position is None

    def test_a_quiet_window_holds_and_writes_nothing(self, no_model: None) -> None:
        store, held = open_one()
        journal = RecordingJournal(calls=[])
        before = store.load().revision

        result = run(store, journal, window(held, flat_bar(held.entry_price, hours_after_entry=1)))

        assert result.status is IterationStatus.RESOLVED
        assert result.cycle is not None
        assert result.cycle.outcome is CycleOutcome.HOLDING
        assert store.load().revision == before
        assert journal.closed == []

    def test_coverage_is_reported_on_the_result(self, no_model: None) -> None:
        store, held = open_one()
        journal = RecordingJournal(calls=[])

        complete = run(
            store, journal, window(held, flat_bar(held.entry_price, hours_after_entry=1))
        )
        assert complete.cycle is not None
        assert complete.cycle.coverage is ResolutionCoverage.COMPLETE

        # No leading bar: the window starts after the entry.
        truncated = run(
            store, journal, FakeProvider(flat_bar(held.entry_price, hours_after_entry=1))
        )
        assert truncated.cycle is not None
        assert truncated.cycle.coverage is ResolutionCoverage.TRUNCATED

    def test_the_window_is_derived_from_how_long_the_position_has_been_open(
        self, no_model: None
    ) -> None:
        store, held = open_one()
        journal = RecordingJournal(calls=[])
        provider = window(held, flat_bar(held.entry_price, hours_after_entry=1))

        run(store, journal, provider)

        assert len(provider.calls) == 1
        symbol, timeframe, output_size = provider.calls[0]
        assert symbol == SYMBOL
        assert timeframe is Timeframe.H1
        # Two hours open, plus the reach-back margin.
        assert output_size == 4


class TestAPassCostsNothingWhenThereIsNothingToDo:
    def test_a_flat_account_never_asks_the_vendor(self, no_model: None) -> None:
        """The property that makes a short interval reasonable: an account with
        nothing open must not spend a request, or even build a client."""
        store = RecordingStore(fresh_account(), calls=[])
        journal = RecordingJournal(calls=[])
        built = False

        def factory() -> MarketDataProvider | None:
            nonlocal built
            built = True
            return FakeProvider()

        result = resolve_once(
            symbol=SYMBOL,
            timeframe=Timeframe.H1,
            store=store,
            journal=journal,
            market_data=factory,
            policy=POLICY,
            max_staleness_bars=MAX_STALENESS_BARS,
            now=RESOLVE_AT,
        )

        assert result.status is IterationStatus.NO_POSITION
        assert result.cycle is None
        assert built is False


class TestRefusals:
    def test_an_unreadable_account_is_reported_not_raised(self, no_model: None) -> None:
        class BrokenStore(RecordingStore):
            def load(self):  # type: ignore[no-untyped-def]
                raise RuntimeError("disk is on fire at ./orion.db")

        store = BrokenStore(fresh_account(), calls=[])
        result = run(store, RecordingJournal(calls=[]), FakeProvider())
        assert result.status is IterationStatus.ACCOUNT_UNREADABLE

    def test_a_missing_credential_is_reported(self, no_model: None) -> None:
        store, _ = open_one()
        result = run(store, RecordingJournal(calls=[]), None)
        assert result.status is IterationStatus.NOT_CONFIGURED

    def test_a_vendor_failure_is_reported_and_closes_nothing(self, no_model: None) -> None:
        store, held = open_one()
        provider = FakeProvider(error=MarketDataProviderError("twelvedata 401 for td-SECRET"))

        result = run(store, RecordingJournal(calls=[]), provider)

        assert result.status is IterationStatus.PRICE_UNAVAILABLE
        assert store.load().state.open_position == held

    def test_stale_data_refuses_rather_than_closing(self, no_model: None) -> None:
        """A month-old window says nothing about where price is now. Unlike a
        gap, staleness is fatal to a pass."""
        store, held = open_one()
        stale = OHLCVBar(
            timestamp=NOW - timedelta(days=30),
            open=held.stop_loss - 50.0,
            high=held.stop_loss - 50.0,
            low=held.stop_loss - 50.0,
            close=held.stop_loss - 50.0,
            volume=1_000.0,
        )

        result = run(store, RecordingJournal(calls=[]), FakeProvider(stale))

        assert result.status is IterationStatus.PRICE_UNAVAILABLE
        assert store.load().state.open_position == held

    def test_a_symbol_mismatch_fails_closed(self, no_model: None) -> None:
        store, held = open_one()
        journal = RecordingJournal(calls=[])
        provider = FakeProvider(
            flat_bar(held.entry_price, hours_after_entry=0),
            flat_bar(held.entry_price, hours_after_entry=1),
            symbol="EURUSD",
        )

        result = run(store, journal, provider)

        assert result.status is IterationStatus.RESOLVED
        assert result.cycle is not None
        assert result.cycle.outcome is CycleOutcome.EXECUTION_REFUSED
        assert store.load().state.open_position == held
        assert journal.closed == []


class TestAPassCanNeverOpenAPosition:
    def test_no_window_over_a_flat_account_produces_a_fill(self, no_model: None) -> None:
        store = RecordingStore(fresh_account(), calls=[])
        journal = RecordingJournal(calls=[])

        for price in (2000.0, 2500.0, 3000.0):
            result = run(
                store,
                journal,
                FakeProvider(
                    flat_bar(price, hours_after_entry=0), flat_bar(price, hours_after_entry=1)
                ),
            )
            assert result.status is IterationStatus.NO_POSITION

        assert journal.fills == []
        assert store.load().state.open_position is None

    def test_a_resolving_pass_never_writes_a_fill(self, no_model: None) -> None:
        store, held = open_one()
        journal = RecordingJournal(calls=[])

        result = run(
            store,
            journal,
            window(
                held,
                wick_bar(low=held.stop_loss - 5.0, price=held.entry_price, hours_after_entry=1),
            ),
        )

        assert result.cycle is not None
        assert result.cycle.fill is None
        assert journal.fills == []


def test_identical_inputs_produce_identical_results(no_model: None) -> None:
    """Two identical accounts, the same window, the same instant: the pass is a
    function of its arguments, which is what makes repeating it safe."""
    first_store, first_held = open_one()
    second_store, second_held = open_one()
    assert first_held == second_held

    bar = wick_bar(
        low=first_held.stop_loss - 5.0, price=first_held.entry_price, hours_after_entry=1
    )
    first = run(first_store, RecordingJournal(calls=[]), window(first_held, bar))
    second = run(second_store, RecordingJournal(calls=[]), window(second_held, bar))

    assert first.status is second.status
    assert first.price == second.price
    assert first.cycle is not None and second.cycle is not None
    assert first.cycle.outcome is second.cycle.outcome
    assert first.cycle.closed_position == second.cycle.closed_position
    assert first.cycle.journal_fingerprint == second.cycle.journal_fingerprint
    assert first_store.load().state == second_store.load().state
