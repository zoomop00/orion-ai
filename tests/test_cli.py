"""The `orion` console entrypoint.

`pyproject.toml` declared `orion = "orion.cli:main"` for twelve missions with
no `orion/cli.py` behind it, so the first test here is the one that would have
caught that: it reads the declared target out of `pyproject.toml` and resolves
it, rather than importing a name this file already knows.

Everything else runs `main` in-process with an injected `service_factory`, so
no test in this module constructs a vendor client, opens a socket, or reads a
credential from the environment.
"""

from __future__ import annotations

import ast
import importlib
import tokenize
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orion.cli import (
    ANALYSIS_FAILED_MESSAGE,
    EXIT_ANALYSIS_FAILED,
    EXIT_NOT_CONFIGURED,
    EXIT_OK,
    EXIT_USAGE,
    NOT_CONFIGURED_MESSAGE,
    UNKNOWN_SYMBOL_MESSAGE,
    _default_service_factory,
    build_parser,
    main,
    render_report,
)
from orion.data.models import DataSourceMeta, MarketSnapshot, Timeframe
from orion.data.provider_base import MarketDataProvider, MarketDataProviderError
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.schemas import AnalysisReport, Intent, RiskAvailability
from orion.reasoning.provider_base import ReasoningProviderError
from orion.risk.policy import KillSwitchState, RiskPolicy
from tests.orchestrator.conftest import (
    FakePortfolioStore,
    FakeReasoningProvider,
    make_bars,
    make_portfolio,
)

# A credential shaped like a real one, planted inside provider error messages
# to prove none of them reaches the terminal. Never a real key.
FAKE_SECRET = "sk-live-supersecret-1234567890"


class LiveFakeMarketDataProvider(MarketDataProvider):
    """Bars ending at the top of the current hour.

    The CLI runs its analysis `as of` `datetime.now(UTC)`, so a fixture pinned
    to a literal date would be rejected by `validate_snapshot` as stale —
    correctly, and uselessly for these tests. Anchoring to the real clock keeps
    the staleness check live without freezing time or stubbing the validator.
    Copied deliberately from `tests/interface/test_analysis_turn.py`, which
    needs the identical thing for the identical reason.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[tuple[str, Timeframe, int]] = []

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        self.calls.append((symbol, timeframe, output_size))
        if self._error is not None:
            raise self._error
        now = datetime.now(UTC)
        end = now.replace(minute=0, second=0, microsecond=0)
        return MarketSnapshot(
            source=DataSourceMeta(
                provider="fake", symbol=symbol, timeframe=timeframe, retrieved_at=now
            ),
            bars=make_bars(end=end),
        )


class RecordingService(AnalysisService):
    """A real `AnalysisService` that remembers how it was called."""

    def __init__(self, market_data: MarketDataProvider) -> None:
        super().__init__(
            market_data=market_data,
            reasoning_provider=FakeReasoningProvider(),
            # Disengaged so the guardian reaches a real verdict rather than
            # vetoing on the kill switch; the CLI supplies no portfolio store,
            # so the panel reports `no_portfolio_store` either way.
            policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
        )
        self.intents: list[Intent] = []
        self.times: list[datetime] = []

    def run(self, intent: Intent, *, now: datetime) -> AnalysisReport:
        self.intents.append(intent)
        self.times.append(now)
        return super().run(intent, now=now)


def build_service(*, error: Exception | None = None) -> RecordingService:
    return RecordingService(LiveFakeMarketDataProvider(error=error))


def reviewing_service() -> AnalysisService:
    """A service with a readable paper account, so the guardian reaches a
    verdict. The CLI itself never wires one — `render_report` still has to
    render one honestly the day something does.

    The account is timestamped against the real clock because
    `mark_to_market` refuses to move a `PortfolioState` backwards.
    """
    return AnalysisService(
        market_data=LiveFakeMarketDataProvider(),
        reasoning_provider=FakeReasoningProvider(),
        policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
        portfolio_store=FakePortfolioStore(
            make_portfolio(as_of=datetime.now(UTC) - timedelta(hours=1))
        ),
    )


def run_cli(
    *argv: str,
    service: AnalysisService | None = None,
) -> int:
    """Invoke `main` with a fixed wiring. Never touches the environment."""
    return main(list(argv), service_factory=lambda: service)


# ------------------------------------------------- the advertised entrypoint


def _pyproject() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_pyproject_declares_the_orion_console_script() -> None:
    scripts = _pyproject()["project"]
    assert isinstance(scripts, dict)
    assert scripts["scripts"] == {"orion": "orion.cli:main"}


def test_the_declared_console_script_target_resolves_and_is_callable() -> None:
    """The regression test for the actual bug.

    Reads the target out of `pyproject.toml` and resolves it, rather than
    importing a name this file already knows — so renaming either half of
    `orion.cli:main` without updating the other fails here.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    scripts = project["scripts"]
    assert isinstance(scripts, dict)
    module_name, _, attribute = str(scripts["orion"]).partition(":")

    module = importlib.import_module(module_name)
    entrypoint = getattr(module, attribute)
    assert callable(entrypoint)


def test_importing_the_cli_needs_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the entrypoint must not read, require or touch a key."""
    for name in ("ANTHROPIC_API_KEY", "TWELVE_DATA_API_KEY", "DEEPGRAM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    module = importlib.import_module("orion.cli")
    assert callable(module.main)


def test_the_default_factory_degrades_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no credentials the shipped wiring returns `None`, which `main`
    turns into a refusal. It must never raise, and must never construct a
    vendor client it cannot authenticate.
    """
    for name in ("ANTHROPIC_API_KEY", "TWELVE_DATA_API_KEY"):
        monkeypatch.setenv(name, "")

    assert _default_service_factory() is None


# ------------------------------------------------------- a valid invocation


def test_analyze_runs_one_analysis_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    service = build_service()

    assert run_cli("analyze", "XAUUSD", service=service) == EXIT_OK

    out = capsys.readouterr().out
    assert "XAUUSD 1h" in out
    assert "risk guardian" in out
    assert "no_portfolio_store" in out
    assert len(service.intents) == 1


def test_the_analysis_runs_on_the_default_chart() -> None:
    service = build_service()

    run_cli("analyze", "XAUUSD", service=service)

    assert service.intents[0].timeframe is Timeframe.H1


def test_the_analysis_is_run_as_of_an_aware_utc_now() -> None:
    service = build_service()
    before = datetime.now(UTC)

    run_cli("analyze", "XAUUSD", service=service)

    now = service.times[0]
    assert now.tzinfo is not None
    assert before <= now <= datetime.now(UTC) + timedelta(seconds=1)


@pytest.mark.parametrize("argument", ["XAUUSD", "xauusd", "gold", "GOLD", "xau/usd"])
def test_every_accepted_spelling_resolves_to_the_table_symbol(argument: str) -> None:
    """The argument is looked up, never passed through.

    `orion.orchestrator.intent` guarantees `Intent.symbol` comes from its own
    alias table, so what reaches the data provider — and from there a vendor
    URL — can only ever be a symbol this build declares.
    """
    service = build_service()

    assert run_cli("analyze", argument, service=service) == EXIT_OK

    assert service.intents[0].symbol == "XAUUSD"


@pytest.mark.parametrize(
    "argument",
    [
        "EURUSD",
        "../../etc/passwd",
        "XAUUSD; rm -rf /",
        "%00",
        "'; DROP TABLE decisions; --",
        "\N{GREEK SMALL LETTER ALPHA}" * 50,
    ],
)
def test_a_hostile_argument_never_reaches_the_data_provider(
    argument: str, capsys: pytest.CaptureFixture[str]
) -> None:
    market_data = LiveFakeMarketDataProvider()
    service = RecordingService(market_data)

    exit_code = run_cli("analyze", argument, service=service)

    if exit_code == EXIT_OK:
        # "XAUUSD; rm -rf /" does contain a real symbol token, so resolving is
        # correct — but only ever to the table's value, never to the argument.
        assert [call[0] for call in market_data.calls] == ["XAUUSD"]
    else:
        assert exit_code == EXIT_USAGE
        assert market_data.calls == []
        assert capsys.readouterr().out == ""


# --------------------------------------------------------- what it prints


def test_the_rendering_is_deterministic() -> None:
    service = build_service()
    run_cli("analyze", "XAUUSD", service=service)
    report = service.run(service.intents[0], now=datetime.now(UTC))

    assert render_report(report) == render_report(report)


def test_the_rendering_names_the_report_and_never_invents_a_number() -> None:
    service = build_service()
    report = service.run(
        Intent.model_validate({"kind": "analyze", "symbol": "XAUUSD", "timeframe": "1h"}),
        now=datetime.now(UTC),
    )

    rendered = render_report(report)

    assert report.analysis_id in rendered
    assert report.reasoning.rationale in rendered
    rsi = report.evidence.indicators.rsi
    if rsi is None:
        assert "not enough history yet" in rendered
    else:
        assert f"{rsi:.1f}" in rendered


def test_a_real_risk_verdict_is_rendered_as_the_verdict_not_the_availability() -> None:
    """With a paper account connected the guardian actually rules, and that
    ruling — not `available` — is what the terminal shows.
    """
    service = reviewing_service()
    report = service.run(
        Intent.model_validate({"kind": "analyze", "symbol": "XAUUSD", "timeframe": "1h"}),
        now=datetime.now(UTC),
    )

    assert report.risk.availability is RiskAvailability.AVAILABLE
    assert report.risk.verdict is not None
    rendered = render_report(report)
    assert report.risk.verdict.outcome.value in rendered
    assert "available" not in rendered


# ------------------------------------------------------- invalid arguments


def test_an_unknown_symbol_exits_two_and_names_what_is_supported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = build_service()

    assert run_cli("analyze", "EURUSD", service=service) == EXIT_USAGE

    captured = capsys.readouterr()
    assert captured.out == ""
    assert UNKNOWN_SYMBOL_MESSAGE in captured.err
    assert "XAUUSD" in captured.err
    assert service.intents == []


def test_the_rejected_argument_is_not_echoed_back(capsys: pytest.CaptureFixture[str]) -> None:
    run_cli("analyze", "EURUSD", service=build_service())

    assert "EURUSD" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [(), ("trade", "XAUUSD"), ("analyze",), ("--nonsense",)])
def test_a_bad_invocation_exits_two_without_a_traceback(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(*argv, service=build_service()) == EXIT_USAGE

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "orion.cli" not in captured.err


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("--help", service=build_service()) == EXIT_OK

    assert "analyze" in capsys.readouterr().out


def test_the_only_verb_is_analyze() -> None:
    """Nothing in this codebase can place an order, so no verb may look like
    it can. Read off the parser itself rather than out of the help text."""
    choices = {
        name
        for action in build_parser()._actions
        for name in getattr(action, "choices", None) or ()
    }
    assert choices == {"analyze"}


# --------------------------------------------------------- failure paths


def test_missing_credentials_exit_three_and_say_which(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_cli("analyze", "XAUUSD", service=None) == EXIT_NOT_CONFIGURED

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == NOT_CONFIGURED_MESSAGE


@pytest.mark.parametrize(
    "error",
    [
        MarketDataProviderError(f"twelvedata returned 401 for key {FAKE_SECRET}"),
        ReasoningProviderError(f"anthropic rejected key {FAKE_SECRET}"),
        RuntimeError(f"unexpected: {FAKE_SECRET}"),
    ],
)
def test_a_provider_failure_prints_one_fixed_sentence_and_no_credential(
    error: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli("analyze", "XAUUSD", service=build_service(error=error)) == EXIT_ANALYSIS_FAILED

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == ANALYSIS_FAILED_MESSAGE
    assert FAKE_SECRET not in captured.err
    assert "sk-live" not in captured.err
    assert "twelvedata" not in captured.err.lower()
    assert "anthropic" not in captured.err.lower()
    assert "Traceback" not in captured.err
    assert type(error).__name__ not in captured.err


# ------------------------------------------------ no persistence, no logging


@pytest.fixture
def empty_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.mark.parametrize(
    "argv",
    [("analyze", "XAUUSD"), ("analyze", "EURUSD"), ("--help",)],
)
def test_the_cli_writes_nothing_to_disk(argv: tuple[str, ...], empty_cwd: Path) -> None:
    """No `.db`, no journal, no cache, no dotfile — on success or on refusal.

    `orion.orchestrator.wiring` never opens a paper account, and this
    entrypoint passes no portfolio store, so nothing reaches
    `create_memory_engine`, which would create a database file as a side
    effect of being called.
    """
    run_cli(*argv, service=build_service())

    assert list(empty_cwd.iterdir()) == []


def test_a_failed_analysis_also_writes_nothing(empty_cwd: Path) -> None:
    run_cli("analyze", "XAUUSD", service=build_service(error=RuntimeError("boom")))

    assert list(empty_cwd.iterdir()) == []


# ---------------------------------------------------------- import hygiene


def _cli_source() -> Path:
    import orion.cli

    path = getattr(orion.cli, "__file__", None)
    assert path is not None
    return Path(path)


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
    return modules


def _code_without_comments_or_strings(source_path: Path) -> str:
    """Executable tokens only — this module documents several things it
    forbids, and a raw substring scan would flag the explanation."""
    skip = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE}
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    if fstring_middle is not None:
        skip.add(fstring_middle)
    pieces: list[str] = []
    with source_path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in skip:
                continue
            pieces.append(token.string)
    return " ".join(pieces)


def test_the_cli_source_file_exists() -> None:
    assert _cli_source().is_file(), "src/orion/cli.py is missing"


def test_the_cli_imports_only_what_an_entrypoint_needs() -> None:
    """A composition root, not a second pipeline.

    It may read `Settings` and reach the orchestrator's own wiring — that is
    what a composition root is for — and nothing else. In particular it may
    not import `orion.memory`/`orion.paper` (persistence),
    `orion.interface` (the server is a different entrypoint), or the Mission
    0-8 packages it would otherwise be one edit away from driving directly.
    """
    allowed = {
        "argparse",
        "sys",
        "collections.abc",
        "datetime",
        "orion.config",
        "orion.orchestrator.analysis",
        "orion.orchestrator.intent",
        "orion.orchestrator.schemas",
        "orion.orchestrator.wiring",
        "__future__",
    }
    assert _imported_modules(_cli_source()) <= allowed


@pytest.mark.parametrize(
    "forbidden",
    ["logging", "sqlite3", "sqlalchemy", "httpx", "requests", "socket", "urllib"],
)
def test_the_cli_has_no_logging_database_or_network_capability(forbidden: str) -> None:
    assert forbidden not in _code_without_comments_or_strings(_cli_source())


@pytest.mark.parametrize("forbidden", ["open", "Path", "pathlib", "shutil", "tempfile"])
def test_the_cli_touches_no_filesystem(forbidden: str) -> None:
    assert forbidden not in _code_without_comments_or_strings(_cli_source())


@pytest.mark.parametrize("forbidden", ["subprocess", "exec", "eval", "compile", "os.environ"])
def test_the_cli_has_no_dangerous_call(forbidden: str) -> None:
    assert forbidden not in _code_without_comments_or_strings(_cli_source())


def test_the_cli_reads_the_environment_only_through_settings() -> None:
    """`orion.config` is the one module allowed to read the environment; this
    file must go through it rather than around it."""
    code = _code_without_comments_or_strings(_cli_source())
    assert "getenv" not in code
    assert "environ" not in code
    assert "get_settings" in code
