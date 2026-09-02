"""`orion resolve`: honouring a stop without asking a model.

WHAT MISSION 9 ACTUALLY CHANGED. Resolving an open position was reachable only
through `orion paper`, which runs a full analysis first — market data,
indicators, structure, a Claude call, a decision and a Risk Guardian verdict —
none of which the resolution branch read. A stop-loss that can only be checked
by calling a model is a stop-loss that stops working when a credential expires
or a rate limit is hit. This verb is that branch, on its own.

THE ASSERTIONS THAT MATTER MOST ARE THE ABSENT ONES. Every test here runs with
`ANTHROPIC_API_KEY` stripped from the environment *and* with
`anthropic.Anthropic` replaced by something that raises if it is ever
constructed — so "no model was called" is proven by the call being impossible,
not by inspecting a transcript afterwards.

NOTHING HERE OPENS A POSITION. `resolve` has no path to `execute()`, and the
tests assert the account's fills are unchanged across every branch.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orion.analysis.evidence import build_evidence
from orion.cli import (
    _COVERAGE_SUMMARY,
    EXIT_NO_ACCOUNT,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
    EXIT_PRICE_UNAVAILABLE,
    EXIT_USAGE,
    NO_ACCOUNT_MESSAGE,
    NO_OPEN_POSITION_MESSAGE,
    NOT_CONFIGURED_FOR_PRICES_MESSAGE,
    PRICE_UNAVAILABLE_MESSAGE,
    build_parser,
    main,
)
from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.data.provider_base import MarketDataProvider, MarketDataProviderError
from orion.decision.engine import decide
from orion.memory.engine import create_memory_engine
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.memory.sqlite_journal import SqliteDecisionJournal

# Mission 9 Phase 3 moved the window derivation into the shared iteration, so
# that both `orion resolve` and `orion monitor` run one implementation. These
# tests follow it to its new home; what they assert is unchanged.
from orion.monitor.iteration import MAX_RESOLUTION_BARS, resolution_window_bars
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import ClosedPosition, CloseReason
from orion.paper.session import CycleOutcome, ResolutionCoverage, run_cycle
from orion.risk.guardian import review
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import Position, initial_portfolio_state, mark_to_market

from .orchestrator.conftest import (
    LAST_BAR_TIME,
    FakeReasoningProvider,
    make_bars,
    make_snapshot,
)

SYMBOL = "XAUUSD"
POLICY = PaperExecutionPolicy()


def neutral_for(held: Position) -> float:
    """A price strictly between the stop and the target: it triggers nothing."""
    return (held.stop_loss + held.take_profit) / 2.0


def target_touch_bar(held: Position) -> OHLCVBar:
    """A bar whose HIGH reached the target and whose CLOSE did not.

    This is the shape last-price resolution is blind to, and the reason Phase 2
    exists: price traded through the level and came back before the bar ended.
    """
    neutral = neutral_for(held)
    return OHLCVBar(
        timestamp=top_of_current_hour(),
        open=neutral,
        high=held.take_profit + 5.0,
        low=neutral,
        close=neutral,
        volume=1_000.0,
    )


def stop_touch_bar(held: Position) -> OHLCVBar:
    """The mirror: a bar whose LOW reached the stop and whose CLOSE did not."""
    neutral = neutral_for(held)
    return OHLCVBar(
        timestamp=top_of_current_hour(),
        open=neutral,
        high=neutral,
        low=held.stop_loss - 5.0,
        close=neutral,
        volume=1_000.0,
    )


def top_of_current_hour() -> datetime:
    """The bar timestamp a live fetch would really return right now.

    `orion resolve` stamps its own `now` from the wall clock and validates the
    snapshot against `RiskPolicy.max_staleness_bars`, so a fixture pinned to a
    literal date would be rejected as stale — correctly, and uselessly here.
    Anchoring to the real clock keeps the staleness check live: it still runs,
    and it still passes for a fresh bar. The same technique
    `tests/interface/test_analysis_turn.py` already uses.
    """
    return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)


def flat_bar(timestamp: datetime, price: float) -> OHLCVBar:
    """A bar that went nowhere. Used to fill a window with prices that cannot
    trigger anything, so a test's *one* interesting bar is the only one the
    resolver can act on and the assertion is about that bar alone."""
    return OHLCVBar(
        timestamp=timestamp, open=price, high=price, low=price, close=price, volume=1_000.0
    )


class FakePriceProvider(MarketDataProvider):
    """A window of `output_size` hourly bars ending at the current hour.

    Every bar sits flat at `neutral` — chosen by each test to be strictly
    between the position's stop and target, so it can trigger nothing — except
    the newest, which the test shapes with `final`. That keeps each assertion
    about the bar it is actually about, rather than about whichever bar of a
    rising fixture series happened to cross a level first.

    Honouring `output_size` is the point: Phase 2 derives it from how long the
    position has been open, and a provider that ignored it would make the
    derived window untestable.
    """

    def __init__(
        self,
        *,
        neutral: float,
        final: OHLCVBar | None = None,
        error: Exception | None = None,
        offsets_hours: Sequence[int] | None = None,
    ) -> None:
        self._neutral = neutral
        self._final = final
        self._error = error
        #: Explicit hours-before-the-current-hour for each bar, newest last.
        #: `None` means "honour `output_size` and return a contiguous window",
        #: which is what a healthy provider does; an explicit list is how a
        #: test builds a window that is deliberately short or deliberately
        #: missing bars in the middle.
        self._offsets_hours = offsets_hours
        self.calls: list[tuple[str, Timeframe, int]] = []

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        self.calls.append((symbol, timeframe, output_size))
        if self._error is not None:
            raise self._error
        end = top_of_current_hour()
        offsets = (
            list(self._offsets_hours)
            if self._offsets_hours is not None
            else list(range(output_size - 1, -1, -1))
        )
        bars = [flat_bar(end - timedelta(hours=offset), self._neutral) for offset in offsets]
        if self._final is not None:
            bars[-1] = self._final.model_copy(update={"timestamp": bars[-1].timestamp})
        return MarketSnapshot(
            source=DataSourceMeta(
                provider="fake",
                symbol=symbol,
                timeframe=timeframe,
                retrieved_at=datetime.now(UTC),
            ),
            bars=bars,
        )


class StalePriceProvider(MarketDataProvider):
    """A window old enough that `validate_snapshot` refuses it outright."""

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        bars = make_bars(count=2, end=top_of_current_hour() - timedelta(days=30))
        return MarketSnapshot(
            source=DataSourceMeta(
                provider="fake",
                symbol=symbol,
                timeframe=timeframe,
                retrieved_at=datetime.now(UTC),
            ),
            bars=bars,
        )


#: How long before the current hour the seeded position was opened. Six hours,
#: not one, so the window `orion resolve` derives holds several bars *after*
#: `opened_at` -- enough room for a test to place a gap between two of them. A
#: position opened from the newest bar has no post-entry bar at all, which is a
#: real and correct state but a useless one to resolve against.
SEED_AGE = timedelta(hours=6)


def seed_bar_end() -> datetime:
    """The last bar the seeded position was opened from."""
    return top_of_current_hour() - SEED_AGE


def seed_now() -> datetime:
    """The instant the seeded position was opened -- just after its last bar,
    exactly as a live cycle would stamp it."""
    return seed_bar_end() + timedelta(minutes=5)


def seed_open_position(db_path: Path) -> Position:
    """Open one real position through the real forward cycle, and leave it open.

    Not a hand-built `Position`: the stop and target this verb later honours are
    the ones `orion.risk.guardian.review` actually approved, which is the whole
    reason resolution needs no authority of its own.
    """
    engine = create_memory_engine(str(db_path))
    try:
        store = SqlitePortfolioStore(engine)
        journal = SqliteDecisionJournal(engine)
        store.initialize(
            initial_portfolio_state(symbol=SYMBOL, starting_balance=100_000.0, now=LAST_BAR_TIME)
        )
        opened_at = seed_now()
        evidence = build_evidence(make_snapshot(bars=make_bars(end=seed_bar_end())))
        reasoning = FakeReasoningProvider().reason(evidence)
        decision = decide(evidence, reasoning, analysis_id="a-0", now=opened_at)
        marked = mark_to_market(store.load().state, now=opened_at, last_price=evidence.last_price)
        verdict = review(
            decision,
            evidence,
            policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
            portfolio=marked,
            now=opened_at,
        )
        opened = run_cycle(
            decision=decision,
            evidence=evidence,
            reasoning=reasoning,
            verdict=verdict,
            store=store,
            journal=journal,
            policy=POLICY,
            risk_per_trade_fraction=0.01,
            now=opened_at,
        )
        assert opened.outcome is CycleOutcome.EXECUTED
        held = store.load().state.open_position
        assert held is not None
        return held
    finally:
        engine.dispose()


def seed_flat_account(db_path: Path) -> None:
    engine = create_memory_engine(str(db_path))
    try:
        SqlitePortfolioStore(engine).initialize(
            initial_portfolio_state(symbol=SYMBOL, starting_balance=100_000.0, now=LAST_BAR_TIME)
        )
    finally:
        engine.dispose()


def open_position_in(db_path: Path) -> Position | None:
    engine = create_memory_engine(str(db_path))
    try:
        return SqlitePortfolioStore(engine).load().state.open_position
    finally:
        engine.dispose()


def revision_of(db_path: Path) -> int:
    engine = create_memory_engine(str(db_path))
    try:
        return SqlitePortfolioStore(engine).load().revision
    finally:
        engine.dispose()


def only_closed_position(db_path: Path) -> ClosedPosition:
    """The single `ClosedPosition` the journal holds, read back from disk.

    Read rather than taken from the return value on purpose: it asserts the
    close was really persisted through the existing account-then-journal path,
    not merely computed.
    """
    engine = create_memory_engine(str(db_path))
    try:
        closed = SqliteDecisionJournal(engine).list_closed_positions_by_symbol(SYMBOL)
        assert len(closed) == 1
        return closed[0]
    finally:
        engine.dispose()


# ------------------------------------------------------------ the parser


class TestTheVerbExists:
    def test_resolve_takes_a_symbol_and_offers_no_venue_flag(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["resolve", "XAUUSD"])
        assert parsed.command == "resolve"
        assert parsed.symbol == "XAUUSD"

        with pytest.raises(SystemExit):
            parser.parse_args(["resolve"])
        with pytest.raises(SystemExit):
            parser.parse_args(["resolve", "XAUUSD", "--live"])

    def test_an_unknown_symbol_is_refused_before_anything_is_opened(
        self, account_home: Path, no_model: None
    ) -> None:
        provider = FakePriceProvider(neutral=2500.0)
        code = main(["resolve", "TSLA"], market_data_factory=lambda: provider)
        assert code == EXIT_USAGE
        assert provider.calls == []


# ------------------------------------------------- the derived fetch window


class TestTheWindowIsDerivedAndBounded:
    def test_the_requested_window_covers_the_time_the_position_has_been_open(
        self, account_home: Path, no_model: None
    ) -> None:
        """One vendor request, on the configured timeframe, for a window sized
        from `opened_at` rather than a fixed 1 — that is what makes a crossing
        between two observations visible at all."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held))

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK

        assert len(provider.calls) == 1
        symbol, timeframe, output_size = provider.calls[0]
        assert symbol == SYMBOL
        assert timeframe is Timeframe.H1
        # The position has been open ~6h, so the window is a handful of bars:
        # more than the one Phase 1 asked for, far fewer than the ceiling.
        assert 2 < output_size <= 12
        assert output_size < MAX_RESOLUTION_BARS

    def test_the_window_is_capped_so_an_old_position_cannot_request_unboundedly(
        self,
    ) -> None:
        """A position left open for years must not turn one command into an
        unbounded vendor request."""
        ancient = datetime.now(UTC) - timedelta(days=3650)
        capped = resolution_window_bars(ancient, datetime.now(UTC), timeframe=Timeframe.H1)
        assert capped == MAX_RESOLUTION_BARS

    def test_a_freshly_opened_position_still_asks_for_enough_to_reach_back(self) -> None:
        now = datetime.now(UTC)
        assert resolution_window_bars(now, now, timeframe=Timeframe.H1) >= 2

    def test_a_backwards_clock_yields_the_minimum_rather_than_a_negative_count(
        self,
    ) -> None:
        now = datetime.now(UTC)
        assert resolution_window_bars(now + timedelta(hours=5), now, timeframe=Timeframe.H1) >= 2


# ------------------------------------------------- no model, ever


class TestResolutionCallsNoModel:
    def test_a_take_profit_closes_with_no_anthropic_key_configured(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The mission, end to end through the CLI: a real position closed at a
        real approved level, with no Anthropic credential and no Anthropic
        client constructible."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held), final=target_touch_bar(held))

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "closed" in out
        assert CycleOutcome.CLOSED.value in out
        assert open_position_in(db_path) is None

    def test_a_stop_loss_closes_the_same_way(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held), final=stop_touch_bar(held))

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_OK
        assert "stop_loss_hit" in capsys.readouterr().out
        assert open_position_in(db_path) is None


# ---------------------------------------- the bar-aware path, from the CLI


class TestTheCliUsesTheBarAwarePath:
    def test_a_wick_through_the_target_closes_where_a_last_price_would_hold(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """THE POINT OF PHASE 2, ASSERTED FROM THE COMMAND LINE. The bar's high
        reached the target and price came back; its close is neutral, so
        last-price resolution would have reported HOLDING and left a position
        open that really did take profit."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        touch = target_touch_bar(held)
        assert touch.close < held.take_profit  # a last price would not trigger
        provider = FakePriceProvider(neutral=neutral_for(held), final=touch)

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "take_profit_hit" in out
        assert open_position_in(db_path) is None

    def test_a_wick_through_the_stop_closes_where_a_last_price_would_hold(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        touch = stop_touch_bar(held)
        assert touch.close > held.stop_loss
        provider = FakePriceProvider(neutral=neutral_for(held), final=touch)

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK
        assert "stop_loss_hit" in capsys.readouterr().out
        assert open_position_in(db_path) is None

    def test_the_triggering_bar_timestamp_is_rendered(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Provenance for the reader, without touching `ClosedPosition`."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held), final=target_touch_bar(held))

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK
        assert "triggering bar" in capsys.readouterr().out

    def test_a_gap_through_the_stop_fills_at_the_bar_open(
        self, account_home: Path, no_model: None
    ) -> None:
        """The worst-of rule, visible in the account: a bar that opened below
        the stop fills there, not at the stop, so the realised loss is the one
        the gap actually handed out."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        gap_open = held.stop_loss - 40.0
        provider = FakePriceProvider(
            neutral=neutral_for(held),
            final=OHLCVBar(
                timestamp=top_of_current_hour(),
                open=gap_open,
                high=gap_open + 5.0,
                low=gap_open - 10.0,
                close=gap_open,
                volume=1_000.0,
            ),
        )

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK

        closed = only_closed_position(db_path)
        assert closed.close_reason is CloseReason.STOP_LOSS_HIT
        # Filled at the gapped open, worse than the stop -- never at the stop.
        assert closed.exit_price < held.stop_loss


# ------------------------------------------------------------- coverage


class TestCoverageIsReportedHonestly:
    def test_a_complete_window_says_so_and_still_names_the_forming_bar_caveat(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held))

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK

        out = capsys.readouterr().out
        assert CycleOutcome.HOLDING.value in out
        assert _COVERAGE_SUMMARY[ResolutionCoverage.COMPLETE] in out
        assert "INCOMPLETE" not in out

    def test_a_window_that_cannot_reach_the_entry_is_reported_incomplete(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A HOLDING whose window starts after the position opened must never
        read as "nothing happened" -- bars before it were never seen."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        # One bar, at the current hour: well after `opened_at`.
        provider = FakePriceProvider(neutral=neutral_for(held), offsets_hours=[0])

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK

        out = capsys.readouterr().out
        assert CycleOutcome.HOLDING.value in out
        assert _COVERAGE_SUMMARY[ResolutionCoverage.TRUNCATED] in out
        assert "INCOMPLETE" in out

    def test_a_window_missing_bars_in_the_middle_is_reported_incomplete(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A gap does not refuse -- a real stop must still be honourable when
        the window spans a market weekend -- but it is never a clean hold."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        # Reaches back past the entry (7h ago), then jumps: every bar between
        # 5h ago and the current one is missing, and the entry was 6h ago -- so
        # the hole sits squarely inside the window that had to be examined.
        provider = FakePriceProvider(neutral=neutral_for(held), offsets_hours=[7, 5, 0])

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK

        out = capsys.readouterr().out
        assert CycleOutcome.HOLDING.value in out
        assert _COVERAGE_SUMMARY[ResolutionCoverage.GAPPED] in out
        assert "INCOMPLETE" in out

    def test_a_hole_spanning_the_entry_is_reported_incomplete(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """REGRESSION, FROM THE COMMAND LINE. The gap scan once ran over the
        eligible bars alone, so a window holding the last bar before the entry
        and then nothing until hours later was contiguous *within* that set and
        claimed COMPLETE -- while never seeing the hours in between.

        The seeded position opened 6h ago. This window holds a bar from 7h ago
        (so it reaches back) and then nothing until 2h ago: four unexamined
        hours during which the position was open, sitting exactly at the seam
        between the reach-back check and the internal-hole check. It is the
        shape of a position opened just before a market weekend.
        """
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held), offsets_hours=[7, 2, 1, 0])

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK

        out = capsys.readouterr().out
        assert CycleOutcome.HOLDING.value in out
        assert _COVERAGE_SUMMARY[ResolutionCoverage.GAPPED] in out
        assert "INCOMPLETE" in out

    def test_a_gapped_window_still_closes_a_position_that_really_hit(
        self, account_home: Path, no_model: None
    ) -> None:
        """Fail-closed on trust, not on action: imperfect data downgrades the
        coverage claim, it never suppresses a real exit."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(
            neutral=neutral_for(held),
            final=target_touch_bar(held),
            offsets_hours=[7, 5, 0],
        )

        assert main(["resolve", "XAUUSD"], market_data_factory=lambda: provider) == EXIT_OK
        assert open_position_in(db_path) is None


# ------------------------------------------------------------- holding


class TestHolding:
    def test_holding_prints_the_outcome_and_writes_no_revision(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        before = revision_of(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held))

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_OK
        assert CycleOutcome.HOLDING.value in capsys.readouterr().out
        assert revision_of(db_path) == before
        assert open_position_in(db_path) == held


# -------------------------------------------------- nothing to resolve


class TestNothingToResolve:
    def test_a_flat_account_says_so_without_asking_the_vendor(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Phase 2 reads the account first, so a command with nothing to do
        now spends no vendor credit finding that out."""
        db_path = account_home / "orion.db"
        seed_flat_account(db_path)
        before = revision_of(db_path)
        provider = FakePriceProvider(neutral=2500.0)

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_OK
        assert NO_OPEN_POSITION_MESSAGE in capsys.readouterr().out
        assert revision_of(db_path) == before
        assert provider.calls == []

    def test_resolution_never_opens_a_position(self, account_home: Path, no_model: None) -> None:
        """A flat account stays flat at every price. `resolve` has no path to
        `execute()` and must never acquire one."""
        db_path = account_home / "orion.db"
        seed_flat_account(db_path)

        for price in (2000.0, 2500.0, 3000.0):
            assert (
                main(
                    ["resolve", "XAUUSD"],
                    market_data_factory=lambda p=price: FakePriceProvider(neutral=p),
                )
                == EXIT_OK
            )
            assert open_position_in(db_path) is None


# ------------------------------------------------------------ refusals


class TestRefusals:
    def test_no_account_refuses_and_creates_no_database(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        provider = FakePriceProvider(neutral=2500.0)
        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_NO_ACCOUNT
        assert NO_ACCOUNT_MESSAGE in capsys.readouterr().err
        assert not list(account_home.rglob("*.db"))
        assert provider.calls == []

    def test_a_missing_market_data_key_names_only_the_key_it_needs(
        self,
        account_home: Path,
        no_model: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """It must not tell the user to set `ANTHROPIC_API_KEY`: this command
        does not use one."""
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        seed_open_position(account_home / "orion.db")

        code = main(["resolve", "XAUUSD"])

        assert code == EXIT_NOT_CONFIGURED
        err = capsys.readouterr().err
        assert NOT_CONFIGURED_FOR_PRICES_MESSAGE in err
        assert "ANTHROPIC_API_KEY" not in err

    def test_a_provider_failure_is_a_clean_refusal_naming_no_vendor(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(
            neutral=neutral_for(held),
            error=MarketDataProviderError(
                "twelvedata 401 for key td-live-SECRET at /v1/time_series"
            ),
        )

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

        assert code == EXIT_PRICE_UNAVAILABLE
        err = capsys.readouterr().err
        assert PRICE_UNAVAILABLE_MESSAGE in err
        assert "SECRET" not in err
        assert "twelvedata" not in err
        assert open_position_in(db_path) == held

    def test_a_stale_window_refuses_rather_than_closing_a_position(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unlike a gap, staleness is fatal: a month-old window says nothing
        about where price is now, so it must never resolve a stop."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)

        code = main(["resolve", "XAUUSD"], market_data_factory=lambda: StalePriceProvider())

        assert code == EXIT_PRICE_UNAVAILABLE
        assert PRICE_UNAVAILABLE_MESSAGE in capsys.readouterr().err
        assert open_position_in(db_path) == held
