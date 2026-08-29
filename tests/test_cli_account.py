"""The local paper account, and the persistence boundary it crosses.

WHAT MISSION 4 PHASE 2a ACTUALLY CHANGED. `orion.paper` has been complete and
tested since Mission 4, and `build_analysis_service` has accepted a
`PortfolioStore` since Mission 10 Phase 5.1 — but nothing in a running system
ever constructed one, so the Risk Guardian's seven portfolio controls had never
executed outside a test or a backtest and every analysis reported
`no_portfolio_store`. This suite covers the composition root that finally opens
one, and the far more important question of when it must *not*.

THE ASSERTIONS THAT MATTER MOST ARE THE FILESYSTEM ONES. `create_memory_engine`
runs `metadata.create_all`, so a database file and its tables appear merely by
constructing a store — there is no "open it but write nothing" middle position.
That makes "did a file appear?" the real specification, and it is checked
against an isolated working directory with `rglob` rather than claimed. Every
refusal path below asserts zero files, because a command that refuses by first
creating the thing it is refusing to read has not refused.

NOTHING HERE REACHES A VENUE. A paper account is a local simulation. The suite
asserts that no verb, flag or help string offers live execution, and that the
only writer is `SqlitePortfolioStore`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from orion.cli import (
    EXIT_ACCOUNT_ERROR,
    EXIT_NO_ACCOUNT,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
    main,
)
from orion.memory.engine import create_memory_engine
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.orchestrator.analysis import AnalysisService, run_analysis
from orion.orchestrator.schemas import AnalysisReport, Intent, RiskAvailability
from orion.paper.store import PortfolioStore
from orion.risk.policy import KillSwitchState, RiskPolicy

from .orchestrator.conftest import (
    NOW,
    FakeMarketDataProvider,
    FakeReasoningProvider,
    make_portfolio,
)

# A credential shaped like a real one, so a leak would be unmistakable. The
# same literal the provider suites use, for the same reason.
FAKE_KEY = "sk-live-supersecret-1234567890"


@pytest.fixture
def account_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated working directory and an `ORION_DB_PATH` inside it.

    `get_settings()` is deliberately uncached, so setting the environment
    variable is enough to move the account for the duration of one test — the
    CLI reads it per invocation exactly as it reads every other setting.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORION_DB_PATH", str(tmp_path / "orion.db"))
    return tmp_path


def files_in(root: Path) -> list[str]:
    """Every file under `root`, relative and sorted. The specification."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class RecordingAccountService(AnalysisService):
    """Captures the store the CLI handed the wiring, and runs nothing."""

    def __init__(self, store: PortfolioStore) -> None:
        self.store = store
        self.calls = 0

    def run(self, intent: Intent, *, now: datetime) -> AnalysisReport:
        self.calls += 1
        raise AssertionError("this test does not run an analysis")


# --------------------------------------------------------------- opening ---


class TestOpeningAnAccount:
    def test_open_creates_the_database_and_the_opening_record(self, account_home: Path) -> None:
        code = main(["account", "open", "--balance", "25000"])

        assert code == EXIT_OK
        # `create_memory_engine` runs SQLite in WAL mode, so the account is the
        # database plus its two sidecars. Asserted as a set rather than a single
        # name so the sidecars are documented rather than a surprise later.
        assert set(files_in(account_home)) == {"orion.db", "orion.db-wal", "orion.db-shm"}

        # Read back through the store's own contract, not by inspecting SQL.
        store = SqlitePortfolioStore(create_memory_engine(str(account_home / "orion.db")))
        record = store.load()
        assert record.state.symbol == "XAUUSD"
        assert record.state.starting_balance == 25000.0
        assert record.state.cash == 25000.0
        assert record.state.equity == 25000.0
        assert record.state.equity_peak == 25000.0
        assert record.state.open_position is None

    def test_the_opening_balance_is_reported_back(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["account", "open", "--balance", "1234.5"]) == EXIT_OK

        out = capsys.readouterr().out
        assert "XAUUSD paper account" in out
        assert "1234.50" in out
        assert "open position" in out and "none" in out

    def test_the_account_is_opened_as_of_an_aware_utc_now(self, account_home: Path) -> None:
        before = datetime.now(UTC)
        assert main(["account", "open", "--balance", "100"]) == EXIT_OK
        after = datetime.now(UTC)

        store = SqlitePortfolioStore(create_memory_engine(str(account_home / "orion.db")))
        as_of = store.load().state.as_of
        assert as_of.tzinfo is not None
        assert before <= as_of <= after

    def test_a_second_open_refuses_and_leaves_the_first_account_untouched(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["account", "open", "--balance", "500"]) == EXIT_OK
        capsys.readouterr()

        assert main(["account", "open", "--balance", "999999"]) == EXIT_ACCOUNT_ERROR

        # The refusal says the reset is the user's own act, and does not do it.
        assert "already exists" in capsys.readouterr().err
        store = SqlitePortfolioStore(create_memory_engine(str(account_home / "orion.db")))
        assert store.load().state.starting_balance == 500.0

    def test_an_unknown_symbol_is_refused_before_any_file_is_created(
        self, account_home: Path
    ) -> None:
        assert main(["account", "open", "--balance", "100", "--symbol", "EURUSD"]) == EXIT_USAGE
        assert files_in(account_home) == []

    def test_the_refusal_names_the_whole_sqlite_set_not_just_the_database(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """WAL mode means an account is three files. Telling a user to delete
        "the database" would leave a write-ahead log a later open could try to
        recover from, so the refusal names the sidecars too."""
        assert main(["account", "open", "--balance", "100"]) == EXIT_OK
        capsys.readouterr()
        assert main(["account", "open", "--balance", "100"]) == EXIT_ACCOUNT_ERROR

        err = capsys.readouterr().err
        assert "-wal" in err
        assert "-shm" in err
        assert "ORION_DB_PATH" in err

    def test_deleting_the_complete_sqlite_set_resets_the_account(self, account_home: Path) -> None:
        """Deletion is the user's own explicit act, and it is the *only* reset.

        ORION has no verb for it, so what this proves is that the documented
        manual procedure genuinely works: remove all three files and the next
        `open` starts clean rather than recovering the old account.
        """
        engines: list[Engine] = []

        def owning_factory(db_path: str) -> PortfolioStore:
            """The shipped factory, with the engine kept so the test can close
            it. `main` is a console entrypoint: in real use the process exits
            and the operating system releases the file, which is when a user
            would delete it. In-process the engine is still holding it open, so
            disposing here is what makes this test the real procedure rather
            than a weaker one."""
            engine = create_memory_engine(db_path)
            engines.append(engine)
            return SqlitePortfolioStore(engine)

        assert (
            main(["account", "open", "--balance", "111"], store_factory=owning_factory) == EXIT_OK
        )
        assert set(files_in(account_home)) == {"orion.db", "orion.db-wal", "orion.db-shm"}

        for engine in engines:
            engine.dispose()
        # A clean close checkpoints the write-ahead log and removes both
        # sidecars, so a properly shut-down account is a single file. They
        # survive only while it is open, or after a crash -- which is exactly
        # why the guidance names them with "any" rather than promising three.
        assert files_in(account_home) == ["orion.db"]

        for name in ("orion.db", "orion.db-wal", "orion.db-shm"):
            (account_home / name).unlink(missing_ok=True)

        assert files_in(account_home) == []
        assert main(["account", "show"]) == EXIT_NO_ACCOUNT

        assert (
            main(["account", "open", "--balance", "222"], store_factory=owning_factory) == EXIT_OK
        )
        record = SqlitePortfolioStore(engines[-1]).load()
        assert record.state.starting_balance == 222.0
        # A genuinely fresh account, not the old one recovered from the log.
        assert record.revision == 0


# -------------------------------------------------------------- balances ---


class TestTheBalanceIsValidated:
    @pytest.mark.parametrize("balance", ["", "-inf", "--symbol"])
    def test_an_option_shaped_balance_is_refused_by_argparse_first(
        self, account_home: Path, balance: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`-inf` and `--symbol` look like options, so argparse rejects them
        before the type callable ever runs. Different message, same contract:
        exit 2, no traceback, and nothing created."""
        code = main(["account", "open", "--balance", balance])

        assert code == EXIT_USAGE
        assert "Traceback" not in capsys.readouterr().err
        assert files_in(account_home) == []

    @pytest.mark.parametrize("balance", ["0", "-1", "-0.01", "abc", "nan", "inf"])
    def test_an_invalid_balance_is_refused_by_name(
        self, account_home: Path, balance: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`nan` and `inf` are the interesting ones: both parse as floats, and
        `inf` would otherwise satisfy `PortfolioState`'s `gt=0` outright."""
        code = main(["account", "open", "--balance", balance])

        assert code == EXIT_USAGE
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "positive, finite number" in captured.err
        assert files_in(account_home) == []

    def test_a_rejected_balance_is_not_echoed_back(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["account", "open", "--balance", "-987654321"])
        assert "987654321" not in capsys.readouterr().err


# ------------------------------------------------------------- reading ----


class TestShowingAnAccount:
    def test_show_reads_the_account_back(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["account", "open", "--balance", "7500"]) == EXIT_OK
        opened = capsys.readouterr().out

        assert main(["account", "show"]) == EXIT_OK
        shown = capsys.readouterr().out

        # Reading is not re-deriving: `show` prints exactly what `open` wrote.
        assert shown == opened

    def test_show_without_an_account_refuses_and_creates_nothing(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The load-bearing one. Opening a store to discover there is no account
        would leave behind the database that discovery created."""
        code = main(["account", "show"])

        assert code == EXIT_NO_ACCOUNT
        assert "No paper account exists" in capsys.readouterr().err
        assert files_in(account_home) == []

    def test_show_never_mutates_the_account(self, account_home: Path) -> None:
        """Asserted against the stored record, not the file's bytes: SQLite in
        WAL mode may legitimately move bytes when a reader checkpoints, and the
        guarantee that matters is that no revision was appended."""
        assert main(["account", "open", "--balance", "300"]) == EXIT_OK

        def snapshot() -> tuple[int, str]:
            store = SqlitePortfolioStore(create_memory_engine(str(account_home / "orion.db")))
            record = store.load()
            return record.revision, record.state.model_dump_json()

        before = snapshot()
        for _ in range(3):
            assert main(["account", "show"]) == EXIT_OK

        assert snapshot() == before

    def test_an_unreadable_account_is_reported_without_naming_the_file(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A file that exists but is not a database at all.
        (account_home / "orion.db").write_bytes(b"this is not sqlite")

        code = main(["account", "show"])

        assert code == EXIT_ACCOUNT_ERROR
        err = capsys.readouterr().err
        assert err.strip() == "ORION could not read the paper account."
        assert "orion.db" not in err
        assert "sqlite" not in err.lower()
        assert "Traceback" not in err


# ------------------------------------------------------ analyze --account --


class TestAnalyzeWithoutTheFlag:
    def test_analyze_writes_nothing_to_disk(self, account_home: Path) -> None:
        """The Mission 12.4 guarantee, re-checked now that a writer exists."""
        code = main(["analyze", "XAUUSD"], service_factory=lambda: None)

        assert code == EXIT_NOT_CONFIGURED
        assert files_in(account_home) == []

    def test_analyze_never_constructs_a_store(self, account_home: Path) -> None:
        opened: list[str] = []

        def store_factory(db_path: str) -> PortfolioStore:
            opened.append(db_path)
            raise AssertionError("no store may be built without --account")

        main(["analyze", "XAUUSD"], service_factory=lambda: None, store_factory=store_factory)

        assert opened == []
        assert files_in(account_home) == []

    def test_an_existing_account_is_ignored_without_the_flag(self, account_home: Path) -> None:
        assert main(["account", "open", "--balance", "100"]) == EXIT_OK

        used: list[PortfolioStore] = []
        main(
            ["analyze", "XAUUSD"],
            service_factory=lambda: None,
            account_service_factory=lambda store: used.append(store) or None,  # type: ignore[func-returns-value]
        )

        assert used == []


class TestAnalyzeWithTheFlag:
    def test_the_flag_hands_the_wiring_a_real_sqlite_store(self, account_home: Path) -> None:
        assert main(["account", "open", "--balance", "4200"]) == EXIT_OK

        captured: list[PortfolioStore] = []

        def account_service_factory(store: PortfolioStore) -> AnalysisService | None:
            captured.append(store)
            return None

        main(["analyze", "XAUUSD", "--account"], account_service_factory=account_service_factory)

        assert len(captured) == 1
        store = captured[0]
        # A real durable store, holding the account that was actually opened --
        # not a stub and not an in-memory stand-in.
        assert isinstance(store, SqlitePortfolioStore)
        assert store.load().state.starting_balance == 4200.0

    def test_without_an_account_the_flag_refuses_and_creates_nothing(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["analyze", "XAUUSD", "--account"])

        assert code == EXIT_NO_ACCOUNT
        assert "No paper account exists" in capsys.readouterr().err
        assert files_in(account_home) == []

    def test_a_real_sqlite_account_yields_a_real_risk_verdict(self, tmp_path: Path) -> None:
        """The point of the whole phase, asserted end to end.

        Every analysis in a running system has reported `no_portfolio_store`
        because `portfolio_store` was always `None`. With a genuine
        SQLite-backed store the Risk Guardian's portfolio controls execute and
        the panel reports `AVAILABLE` with a real verdict on it.
        """
        store = SqlitePortfolioStore(create_memory_engine(str(tmp_path / "orion.db")))
        store.initialize(make_portfolio())

        report = run_analysis(
            intent=Intent.model_validate(
                {"kind": "analyze", "symbol": "XAUUSD", "timeframe": "1h"}
            ),
            market_data=FakeMarketDataProvider(),
            reasoning_provider=FakeReasoningProvider(),
            policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
            portfolio_store=store,
            now=NOW,
        )

        assert report.risk.availability is RiskAvailability.AVAILABLE
        assert report.risk.verdict is not None

    def test_a_stale_account_still_reaches_the_guardian_rather_than_being_hidden(
        self, tmp_path: Path
    ) -> None:
        """A store that loads is handed over whatever it contains; deciding what
        a stale account means is the Risk Guardian's job, not the CLI's."""
        store = SqlitePortfolioStore(create_memory_engine(str(tmp_path / "orion.db")))
        store.initialize(make_portfolio(as_of=NOW - timedelta(days=400)))

        report = run_analysis(
            intent=Intent.model_validate(
                {"kind": "analyze", "symbol": "XAUUSD", "timeframe": "1h"}
            ),
            market_data=FakeMarketDataProvider(),
            reasoning_provider=FakeReasoningProvider(),
            policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
            portfolio_store=store,
            now=NOW,
        )

        # Not NO_PORTFOLIO_STORE: the account was found and read.
        assert report.risk.availability is RiskAvailability.AVAILABLE


# ---------------------------------------------------- hostile arguments ----


class TestNoArgumentBecomesAPath:
    @pytest.mark.parametrize(
        "argument",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "C:\\Windows\\System32\\config\\SAM",
            "orion.db",
            ":memory:",
            "../orion.db",
            "$(whoami)",
            "%2e%2e%2forion.db",
            "\\\\server\\share\\x.db",
        ],
    )
    def test_a_hostile_symbol_never_creates_a_file_anywhere(
        self, account_home: Path, argument: str
    ) -> None:
        """`--symbol` is resolved through the closed alias table, so it can only
        ever become a symbol ORION declares — never a path. The database
        location comes from `ORION_DB_PATH` and from nothing else."""
        code = main(["account", "open", "--balance", "100", "--symbol", argument])

        assert code == EXIT_USAGE
        assert files_in(account_home) == []
        # Nothing was created outside the isolated directory either.
        assert not (account_home.parent / "orion.db").exists()

    def test_the_rejected_symbol_is_not_echoed_back(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["account", "open", "--balance", "100", "--symbol", "../../etc/passwd"])
        assert "etc/passwd" not in capsys.readouterr().err

    def test_shell_metacharacters_around_a_real_alias_resolve_to_the_table_symbol(
        self, account_home: Path
    ) -> None:
        """`gold; rm -rf /` legitimately *contains* a supported alias, so the
        closed table resolves it -- and resolves it to `XAUUSD`, never to any
        part of what was typed. Nothing is executed and no path is built: the
        account still lands at `ORION_DB_PATH` and nowhere else.
        """
        argument = "gold; rm -rf /"
        assert main(["account", "open", "--balance", "100", "--symbol", argument]) == EXIT_OK

        store = SqlitePortfolioStore(create_memory_engine(str(account_home / "orion.db")))
        assert store.load().state.symbol == "XAUUSD"
        assert set(files_in(account_home)) == {"orion.db", "orion.db-wal", "orion.db-shm"}


# ----------------------------------------------------------- credentials ---


class TestNothingLeaks:
    def test_no_credential_reaches_the_terminal_on_any_account_path(
        self,
        account_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for name in ("ANTHROPIC_API_KEY", "TWELVE_DATA_API_KEY", "DEEPGRAM_API_KEY"):
            monkeypatch.setenv(name, FAKE_KEY)

        main(["account", "show"])
        main(["account", "open", "--balance", "100"])
        main(["account", "open", "--balance", "100"])
        main(["account", "show"])
        main(["analyze", "XAUUSD", "--account"], account_service_factory=lambda _: None)

        captured = capsys.readouterr()
        assert FAKE_KEY not in captured.out
        assert FAKE_KEY not in captured.err
        assert "sk-live" not in captured.out
        assert "sk-live" not in captured.err

    def test_a_failing_store_never_prints_the_exception(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def exploding(db_path: str) -> PortfolioStore:
            raise RuntimeError(f"could not open {db_path} using {FAKE_KEY}")

        code = main(["account", "open", "--balance", "100"], store_factory=exploding)

        assert code == EXIT_ACCOUNT_ERROR
        err = capsys.readouterr().err
        assert err.strip() == "ORION could not open the paper account."
        assert FAKE_KEY not in err
        assert "Traceback" not in err

    def test_no_account_output_contains_a_file_path(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["account", "open", "--balance", "100"])
        main(["account", "show"])
        captured = capsys.readouterr()

        for stream in (captured.out, captured.err):
            assert str(account_home) not in stream
            assert "orion.db" not in stream


# ------------------------------------------------------- the surface -------


class TestTheCommandSurfaceStaysHonest:
    def test_there_is_no_live_or_broker_verb_anywhere(self) -> None:
        """A paper account is a simulation. Nothing here may offer, or look
        like it offers, a route to a real venue."""
        parser = build_parser()
        help_text = parser.format_help().lower()

        # No flag, and no verb. The word "broker" does appear -- in the account
        # description, saying it never reaches one -- so the assertion is about
        # what the surface offers rather than about the prose.
        assert "--live" not in help_text
        with pytest.raises(SystemExit):
            parser.parse_args(["analyze", "XAUUSD", "--live"])
        with pytest.raises(SystemExit):
            parser.parse_args(["trade", "XAUUSD"])

    def test_the_account_help_says_the_account_is_simulated(self) -> None:
        parser = build_parser()
        assert "simulated" in parser.format_help().lower()

    def test_the_verbs_are_exactly_analyze_and_account(self) -> None:
        parser = build_parser()
        actions = [
            action
            for action in parser._subparsers._group_actions  # type: ignore[union-attr]
            if hasattr(action, "choices") and action.choices
        ]
        assert set(actions[0].choices) == {"analyze", "account"}

    def test_no_execution_verb_exists_yet(self) -> None:
        """4.2b adds `orion paper`. Until it does, it must not be reachable."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["paper", "XAUUSD"])

    def test_the_cli_is_the_only_module_that_builds_a_concrete_store(self) -> None:
        """`orion.orchestrator` still cannot reach a database.

        The import walls were not moved to make this phase work:
        `build_analysis_service` already took a `PortfolioStore`, and the
        concrete SQLite implementation is constructed in the composition root.
        """
        source = Path("src/orion").resolve()
        definition = "memory/portfolio_store.py"
        builders = [
            path.relative_to(source).as_posix()
            for path in source.rglob("*.py")
            if re.search(r"\bSqlitePortfolioStore\s*\(", path.read_text(encoding="utf-8"))
            and path.relative_to(source).as_posix() != definition
        ]
        assert builders == ["cli.py"]
