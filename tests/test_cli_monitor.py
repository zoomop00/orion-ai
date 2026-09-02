"""`orion monitor`: the one verb that keeps running.

WHAT IT IS. `orion resolve` on a repeating interval and nothing more — the same
pass, the same refusals, the same inability to open a position or call a model.
The loop lives in `orion.monitor.supervisor`; this file tests the wiring, the
preflight and the interval validation around it.

NOTHING HERE SLEEPS. The loop is driven through `_run_monitor`'s test seams, or
bounded by `main`'s `monitor_max_iterations`, so a suite that exercises a
recurring runtime still runs in milliseconds.

NOTHING HERE HAS AN ANTHROPIC CREDENTIAL EITHER, and `anthropic.Anthropic` is
replaced by something that raises — a monitor that could build a client is a
monitor that could call one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orion.cli import (
    EXIT_CYCLE_FAILED,
    EXIT_NO_ACCOUNT,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
    EXIT_USAGE,
    INVALID_INTERVAL_MESSAGE,
    MIN_MONITOR_INTERVAL_SECONDS,
    MONITOR_CEILING_MESSAGE,
    MONITOR_STOPPED_MESSAGE,
    NO_ACCOUNT_MESSAGE,
    NO_OPEN_POSITION_MESSAGE,
    NOT_CONFIGURED_FOR_PRICES_MESSAGE,
    _run_monitor,
    build_parser,
    main,
)
from orion.data.provider_base import MarketDataProviderError
from orion.paper.session import CycleOutcome

from .test_cli_resolve import (
    FakePriceProvider,
    neutral_for,
    open_position_in,
    seed_flat_account,
    seed_open_position,
    stop_touch_bar,
)

INTERVAL = 60.0


class Sleeps:
    """A fake wait that records and never waits."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> bool:
        self.seconds.append(seconds)
        return False


# ------------------------------------------------------------- the parser


class TestTheVerbAndItsInterval:
    def test_monitor_requires_a_symbol_and_an_interval(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(["monitor", "XAUUSD", "--interval", "90"])
        assert parsed.command == "monitor"
        assert parsed.symbol == "XAUUSD"
        assert parsed.interval == 90.0

        with pytest.raises(SystemExit):
            parser.parse_args(["monitor", "XAUUSD"])  # no interval
        with pytest.raises(SystemExit):
            parser.parse_args(["monitor", "--interval", "90"])  # no symbol

    def test_there_is_no_default_interval(self) -> None:
        """A cadence is a deployment decision. Shipping a default would make it
        the number everybody runs without having chosen it."""
        parser = build_parser()
        interval = next(
            action
            for action in parser._subparsers._group_actions[0].choices["monitor"]._actions  # type: ignore[union-attr]
            if action.dest == "interval"
        )
        assert interval.required is True
        assert interval.default is None

    @pytest.mark.parametrize(
        "value",
        ["59", "59.999", "0", "-1", "-60", "nan", "inf", "-inf", "abc", ""],
    )
    def test_an_unusable_interval_is_refused(self, value: str) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["monitor", "XAUUSD", "--interval", value])

    def test_the_minimum_is_accepted_exactly(self) -> None:
        parsed = build_parser().parse_args(
            ["monitor", "XAUUSD", "--interval", str(MIN_MONITOR_INTERVAL_SECONDS)]
        )
        assert parsed.interval == MIN_MONITOR_INTERVAL_SECONDS

    def test_the_refusal_names_the_minimum(self) -> None:
        assert str(int(MIN_MONITOR_INTERVAL_SECONDS)) in INVALID_INTERVAL_MESSAGE

    def test_monitor_offers_no_venue_flag(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["monitor", "XAUUSD", "--interval", "60", "--live"])


# ---------------------------------------------------------------- preflight


class TestPreflightHappensBeforeAnyLoopVendorCallOrSleep:
    def test_an_unknown_symbol_refuses_before_anything(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        provider = FakePriceProvider(neutral=2500.0)
        sleeps = Sleeps()

        code = _run_monitor(
            "TSLA",
            INTERVAL,
            market_data_factory=lambda: provider,
            paper_resources=None,  # type: ignore[arg-type]  -- never reached
            sleeper=sleeps,
        )

        assert code == EXIT_USAGE
        assert provider.calls == []
        assert sleeps.seconds == []

    def test_no_account_refuses_before_anything(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        provider = FakePriceProvider(neutral=2500.0)
        sleeps = Sleeps()

        code = main(["monitor", "XAUUSD", "--interval", "60"], market_data_factory=lambda: provider)

        assert code == EXIT_NO_ACCOUNT
        assert NO_ACCOUNT_MESSAGE in capsys.readouterr().err
        assert provider.calls == []
        assert sleeps.seconds == []
        assert not list(account_home.rglob("*.db"))

    def test_a_missing_market_data_key_refuses_before_the_loop(
        self,
        account_home: Path,
        no_model: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A credential that is missing now will be missing in an hour. It must
        not be rediscovered once per interval forever."""
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        seed_open_position(account_home / "orion.db")

        code = main(["monitor", "XAUUSD", "--interval", "60"])

        assert code == EXIT_NOT_CONFIGURED
        err = capsys.readouterr().err
        assert NOT_CONFIGURED_FOR_PRICES_MESSAGE in err
        assert "ANTHROPIC_API_KEY" not in err


# ------------------------------------------------------------ the loop


class TestTheLoopRunsTheSamePassResolveDoes:
    def test_a_bounded_run_resolves_and_exits_cleanly(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held), final=stop_touch_bar(held))

        code = main(
            ["monitor", "XAUUSD", "--interval", "60"],
            market_data_factory=lambda: provider,
            monitor_max_iterations=1,
        )

        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert CycleOutcome.CLOSED.value in out
        assert MONITOR_STOPPED_MESSAGE in out
        assert open_position_in(db_path) is None

    def test_a_flat_account_keeps_the_monitor_alive_and_spends_nothing(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing open is not a failure: the monitor keeps going, and each
        pass costs zero vendor requests."""
        db_path = account_home / "orion.db"
        seed_flat_account(db_path)
        provider = FakePriceProvider(neutral=2500.0)
        sleeps = Sleeps()

        code = _run_monitor(
            "XAUUSD",
            INTERVAL,
            market_data_factory=lambda: provider,
            paper_resources=_paper_resources(),
            max_iterations=3,
            sleeper=sleeps,
        )

        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert out.count(NO_OPEN_POSITION_MESSAGE) == 3
        # The preflight built one provider to check the credential; the three
        # passes over a flat account asked it for nothing.
        assert provider.calls == []
        assert sleeps.seconds == [INTERVAL, INTERVAL]

    def test_three_consecutive_failures_stop_with_the_cycle_failed_code(
        self, account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(
            neutral=neutral_for(held),
            error=MarketDataProviderError("twelvedata is down"),
        )
        sleeps = Sleeps()

        code = _run_monitor(
            "XAUUSD",
            INTERVAL,
            market_data_factory=lambda: provider,
            paper_resources=_paper_resources(),
            sleeper=sleeps,
        )

        assert code == EXIT_CYCLE_FAILED
        assert MONITOR_CEILING_MESSAGE in capsys.readouterr().err
        assert len(provider.calls) == 3
        assert len(sleeps.seconds) == 2
        # The position is untouched: failing to fetch closes nothing.
        assert open_position_in(db_path) == held

    def test_each_pass_reads_the_account_fresh(self, account_home: Path, no_model: None) -> None:
        """Stateless between iterations: a position closed by the first pass is
        simply gone for the second, which reports nothing to resolve rather
        than acting on a remembered snapshot."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held), final=stop_touch_bar(held))

        code = _run_monitor(
            "XAUUSD",
            INTERVAL,
            market_data_factory=lambda: provider,
            paper_resources=_paper_resources(),
            max_iterations=2,
            sleeper=Sleeps(),
        )

        assert code == EXIT_OK
        assert open_position_in(db_path) is None
        # One fetch for the pass that closed it; the second pass found a flat
        # account and never asked.
        assert len(provider.calls) == 1


def _paper_resources():  # type: ignore[no-untyped-def]
    from orion.cli import _default_paper_resources

    return _default_paper_resources


class TestTheMonitorCallsNoModel:
    def test_no_anthropic_client_is_ever_constructed(
        self, account_home: Path, no_model: None
    ) -> None:
        """`no_model` replaces `anthropic.Anthropic` with something that raises,
        so this passing means the loop cannot have built one."""
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)
        provider = FakePriceProvider(neutral=neutral_for(held))

        code = _run_monitor(
            "XAUUSD",
            INTERVAL,
            market_data_factory=lambda: provider,
            paper_resources=_paper_resources(),
            max_iterations=2,
            sleeper=Sleeps(),
        )

        assert code == EXIT_OK

    def test_it_runs_with_no_anthropic_key_in_the_environment(
        self, account_home: Path, no_model: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        db_path = account_home / "orion.db"
        held = seed_open_position(db_path)

        code = _run_monitor(
            "XAUUSD",
            INTERVAL,
            market_data_factory=lambda: FakePriceProvider(neutral=neutral_for(held)),
            paper_resources=_paper_resources(),
            max_iterations=1,
            sleeper=Sleeps(),
        )

        assert code == EXIT_OK


class TestTheMonitorNeverOpensAPosition:
    def test_a_flat_account_stays_flat_across_many_passes(
        self, account_home: Path, no_model: None
    ) -> None:
        db_path = account_home / "orion.db"
        seed_flat_account(db_path)

        _run_monitor(
            "XAUUSD",
            INTERVAL,
            market_data_factory=lambda: FakePriceProvider(neutral=2500.0),
            paper_resources=_paper_resources(),
            max_iterations=5,
            sleeper=Sleeps(),
        )

        assert open_position_in(db_path) is None


def test_an_unexpected_exception_terminates_rather_than_looping_forever(
    account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operational failure is a counted refusal; a defect is not. Swallowing
    one would turn a bug into an infinite loop that looks healthy."""
    seed_open_position(account_home / "orion.db")

    def exploding_resources(db_path: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("a defect, not an outcome")

    code = _run_monitor(
        "XAUUSD",
        INTERVAL,
        market_data_factory=lambda: FakePriceProvider(neutral=2500.0),
        paper_resources=exploding_resources,
        sleeper=Sleeps(),
    )

    assert code == EXIT_CYCLE_FAILED


def test_resolve_still_runs_one_pass_and_exits(
    account_home: Path, no_model: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Phase 3 rewired `resolve` onto the shared iteration. It must still do
    exactly one thing once, with the output and exit code it had before."""
    db_path = account_home / "orion.db"
    held = seed_open_position(db_path)
    provider = FakePriceProvider(neutral=neutral_for(held), final=stop_touch_bar(held))

    code = main(["resolve", "XAUUSD"], market_data_factory=lambda: provider)

    assert code == EXIT_OK
    assert len(provider.calls) == 1
    assert MONITOR_STOPPED_MESSAGE not in capsys.readouterr().out
    assert open_position_in(db_path) is None


def test_the_monitor_help_says_what_it_will_not_do() -> None:
    """A long-running verb is where a user most needs the limits spelled out,
    so the description states them rather than leaving them to be inferred."""
    monitor = build_parser()._subparsers._group_actions[0].choices["monitor"]  # type: ignore[union-attr]
    description = (monitor.description or "").lower()
    assert "never opens a position" in description
    assert "no ai model" in description
    assert "anthropic_api_key" in description
    assert "no real order" in description
