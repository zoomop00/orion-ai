"""ORION's command line: one verb, over the orchestrator that already exists.

`pyproject.toml` has advertised `orion = "orion.cli:main"` since Mission 0 and
this module was never written, so the installed `orion` command has always
failed with `ModuleNotFoundError` before printing anything. This file is that
entrypoint, and nothing more.

ANALYSIS, AND A LOCAL PAPER ACCOUNT. `orion analyze XAUUSD` runs exactly the
pipeline Mission 10 Phase 5.1 assembled — market data, validation, deterministic
analysis, AI reasoning, the decision engine, the Risk Guardian — through
`orion.orchestrator.wiring.build_analysis_service` and `AnalysisService.run`. It
adds no stage, reorders none and skips none. `orion account open` / `orion
account show` manage the simulated account that Mission 4 built and nothing had
ever opened.

NO VERB IN THIS FILE PLACES AN ORDER WITH A BROKER, and none ever will. Mission
12.4 stated that as "no trade or execute verb, because nothing in this codebase
can place an order"; the account verbs below do not change that, because a paper
account is a local simulation and reaches no venue. What that original sentence
was protecting against — a command that *looked* like it could move real money —
is still forbidden, and there is deliberately no `--live` flag, no broker
adapter and no code path toward one. There is no `serve` verb either, because
`run()` is already reachable by the documented one-liner and a second spelling
of it is a second thing to keep honest.

A COMPOSITION ROOT, LIKE `app.py` AND `wiring.py`. This module reads `Settings`
because something has to; every module it calls still takes its dependencies as
arguments. `service_factory` is the seam that keeps that true under test — the
same shape as `create_app(analysis_factory=...)` — so the CLI's own tests
construct no vendor client and open no socket.

THE SYMBOL IS NEVER PASSED THROUGH. What the user typed is handed to
`orion.orchestrator.intent.parse_intent`, whose module docstring is explicit
that `Intent.symbol` is "a value looked up in `_SYMBOL_ALIASES` and returned
from the table", not a substring of the input. So the string that reaches
`MarketDataProvider.fetch_ohlcv`, and from there a vendor URL, can only ever be
a symbol this build declares. An unrecognised argument is refused here, by the
same closed table the voice path uses, rather than forwarded to fail at the
vendor one call later.

PERSISTENCE IS OPT-IN, CLI-ONLY, AND NEVER A SIDE EFFECT. Mission 12.4 could
say "no `.db` appears because somebody ran the CLI" without qualification. That
is now true of every path except the two account verbs and `analyze --account`,
which is the whole point of the flag: `orion.memory.engine.create_memory_engine`
creates a database file and its tables merely by being called, so the seam is
binary and the only honest design is an explicit opt-in. Without `--account`,
`analyze` behaves exactly as it did — no store is constructed, that function is
never reached, and the risk panel reports `no_portfolio_store` as it does over
HTTP. `orion account show` and `analyze --account` check for the file before
opening an engine, so neither can bring a database into existence as a side
effect of being asked to read one.

THE DATABASE IS THE USER'S, AND ORION NEVER REMOVES IT. `orion.db` — its path is
`ORION_DB_PATH`, never anything derived from a command-line argument — is
retained indefinitely. There is no cleanup, expiry, rotation, compaction or
background job anywhere in this file, and no verb deletes or resets an account:
that is the user's own explicit act, with their own file manager. It follows
that the account grows without bound as it accumulates revisions — the store is
append-only, one permanent row per revision — which is the correct trade for an
audit trail and is said out loud rather than silently managed.

DELETING AN ACCOUNT MEANS DELETING THE WHOLE SQLITE SET.
`orion.memory.engine.create_memory_engine` runs SQLite in WAL mode, so while an
account is open it is `orion.db` plus the `orion.db-wal` and `orion.db-shm`
sidecars. A clean shutdown checkpoints the log and removes both, so a
properly-closed account is a single file — but a crash, or a process still
holding it, leaves them behind, and removing only the database would leave a
write-ahead log a later open could try to recover from. The guidance therefore
names them with "any" rather than promising a fixed count, and it is stated in
the two places a user meets it: the refusal when an account already exists, and
here. ORION will not do it for them.

THE CONCRETE STORE IS BUILT HERE, NOT IN THE ORCHESTRATOR.
`tests/orchestrator/test_import_hygiene.py` bans `orion.memory`, `sqlalchemy`
and `sqlite3` from `orion.orchestrator` and permits `wiring.py` exactly three
extra modules, none of them a store. `build_analysis_service` already accepts a
`PortfolioStore` — the ABC from `orion.paper.store` — and has since Mission 10
Phase 5.1. So this module, which is a composition root like `app.py`, constructs
`SqlitePortfolioStore` and hands it through that existing parameter. No import
wall moved, and `orion.orchestrator` still cannot reach a database.

NO LOGGING, AND NO NETWORK OF ITS OWN. This module imports no logging framework
and makes no request itself: the only network traffic is the providers' own,
inside `AnalysisService.run`. Nothing the user types is written to the database —
the account holds balances and positions, never a transcript or an argument.

FAILURES ARE MESSAGE-FREE, FOR THE SAME REASON THEY ARE OVER HTTP. Every stage
underneath raises a vendor-specific error whose message may name a vendor, a
URL, a database path or a raw provider response — `orion.http_errors` exists
because one of them once embedded an API key in a URL.
`orion.orchestrator.response.AnalysisResponseProvider` answers all of them with
one fixed sentence, and so does this: the exception is never formatted, never
printed, and never allowed to reach the user as a traceback.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from orion.config import get_settings
from orion.memory.engine import create_memory_engine
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.intent import DEFAULT_TIMEFRAME, known_symbols, parse_intent
from orion.orchestrator.schemas import AnalysisReport, IntentKind, RiskAvailability
from orion.orchestrator.wiring import build_analysis_service
from orion.paper.store import PortfolioRecord, PortfolioStore
from orion.risk.portfolio import initial_portfolio_state

#: Exit codes. 0 and 2 are the conventional success/usage pair (2 is argparse's
#: own, so a bad invocation exits the same way whoever rejected it). 3 and 4 are
#: separated on purpose: only the first is fixed by editing `.env`.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_CONFIGURED = 3
EXIT_ANALYSIS_FAILED = 4
#: The account exists or does not; a caller can tell those apart from "the
#: account is there but unreadable" without parsing a sentence.
EXIT_NO_ACCOUNT = 5
EXIT_ACCOUNT_ERROR = 6

#: Fixed sentences. Nothing in this file is ever built from an exception, a
#: vendor response, or the argument the user supplied.
UNKNOWN_SYMBOL_MESSAGE = "ORION cannot analyse that symbol."
NOT_CONFIGURED_MESSAGE = (
    "ORION is not configured to analyse. Set ANTHROPIC_API_KEY and TWELVE_DATA_API_KEY in .env."
)
ANALYSIS_FAILED_MESSAGE = "ORION could not complete the analysis."
NO_ACCOUNT_MESSAGE = "No paper account exists. Run 'orion account open --balance N' first."
#: Names the whole SQLite set, not just the database: WAL mode means an account
#: is three files, and deleting one of them is not deleting the account.
ACCOUNT_EXISTS_MESSAGE = (
    "A paper account already exists. ORION never resets one for you - to start "
    "over, delete the database named by ORION_DB_PATH along with any -wal and "
    "-shm files beside it."
)
#: `PortfolioStoreError`'s own messages embed SQLAlchemy text, which can name the
#: database file and its path. Like every other failure here, the exception is
#: neither bound nor formatted, and this fixed sentence is printed instead.
ACCOUNT_UNAVAILABLE_MESSAGE = "ORION could not read the paper account."
ACCOUNT_NOT_WRITTEN_MESSAGE = "ORION could not open the paper account."
INVALID_BALANCE_MESSAGE = "must be a positive, finite number"

#: How `main` obtains an `AnalysisService`. `None` means "this deployment
#: cannot analyse", which `build_analysis_service` already returns for a
#: missing credential and which is a refusal here rather than a crash.
ServiceFactory = Callable[[], AnalysisService | None]

#: The same seam for the `--account` path, which needs a second wiring rather
#: than a second implementation. `ServiceFactory` deliberately keeps its
#: zero-argument shape: it is what every existing caller and test passes, and
#: widening it would have edited tests that are not about accounts at all.
AccountServiceFactory = Callable[[PortfolioStore], AnalysisService | None]

#: How the account verbs reach a store. Returns the configured path and opens it
#: separately, because "does this account exist?" must be answerable *without*
#: calling `create_memory_engine`, which would answer it by creating one.
StoreFactory = Callable[[str], PortfolioStore]


def _default_service_factory() -> AnalysisService | None:
    """Build one `AnalysisService` from the environment, or `None`.

    Settings are read per invocation rather than captured at import, matching
    `orion.interface.app._default_analysis_factory` and for the same stated
    reason: `RiskPolicy.from_settings` requires a fresh, uncached
    `get_settings()` so engaging the kill switch never needs a restart.
    """
    return build_analysis_service(get_settings())


def _account_service_factory(store: PortfolioStore) -> AnalysisService | None:
    """`_default_service_factory`, with a real paper account behind it.

    `portfolio_store` has been a parameter of `build_analysis_service` since
    Mission 10 Phase 5.1 and has defaulted to `None` ever since, which is why
    the Risk Guardian has only ever reported `no_portfolio_store` in a running
    system. This passes it a store; nothing in `orion.orchestrator` changed.
    """
    return build_analysis_service(get_settings(), portfolio_store=store)


def _default_store_factory(db_path: str) -> PortfolioStore:
    """Open the SQLite store at `db_path`.

    `create_memory_engine` runs `metadata.create_all`, so calling this creates
    the file when it is absent. Every caller below has already decided that
    creating it is what the user asked for.
    """
    return SqlitePortfolioStore(create_memory_engine(db_path))


def _account_db_path() -> str:
    """Where the paper account lives. `ORION_DB_PATH`, and nothing else.

    Read per invocation, like every other setting this module uses. It is never
    built from an argument: no value a user can type on the command line reaches
    a filesystem path, which is what stops `orion account show` from being a
    file-read primitive.
    """
    return get_settings().orion_db_path


def _account_exists(db_path: str) -> bool:
    """Whether an account database is already there, without creating one.

    An in-memory database never persists between invocations, so it is reported
    as absent rather than as an account whose contents vanished.
    """
    if db_path == ":memory:" or db_path.startswith("file::memory:"):
        return False
    return Path(db_path).is_file()


def _positive_finite(text: str) -> float:
    """An `argparse` type for a balance: a real, positive, finite number.

    `float("nan")` and `float("inf")` both parse, and both would reach
    `PortfolioState`'s `gt=0` validator as something it cannot sensibly refuse —
    `nan > 0` is False so it would be rejected with a Pydantic error, and `inf`
    would be accepted outright. Refusing both here means argparse prints its own
    one-line usage error and exits 2, with no traceback and without echoing the
    value back.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(INVALID_BALANCE_MESSAGE) from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(INVALID_BALANCE_MESSAGE)
    return value


def _known_symbols_text() -> str:
    """Every symbol this build can analyse, from the parser's own table."""
    return ", ".join(sorted(known_symbols()))


def _resolve_symbol(argument: str) -> str | None:
    """The table symbol `argument` names, or `None`.

    Goes through `parse_intent`, so what comes back is a value looked up in
    `_SYMBOL_ALIASES` rather than any part of what the user typed — the same
    guarantee the analyze path has always had, now shared with the account
    verbs so an account can only ever be opened for a symbol ORION declares.
    """
    intent = parse_intent(f"analyze {argument}")
    return intent.symbol if intent.kind is IntentKind.ANALYZE else None


def _default_account_symbol() -> str:
    """The symbol `account open` uses when none is given."""
    return sorted(known_symbols())[0]


def build_parser() -> argparse.ArgumentParser:
    """The whole command surface, in one place."""
    parser = argparse.ArgumentParser(
        prog="orion",
        description="ORION - autonomous market intelligence. Analyses only; never trades.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True, metavar="command")
    analyze = subcommands.add_parser(
        "analyze",
        help="Run one full analysis of SYMBOL and print the result.",
        description=(
            f"Run one analysis of SYMBOL on the {DEFAULT_TIMEFRAME.value} chart: market data, "
            "validation, deterministic analysis, AI reasoning, decision engine and Risk "
            "Guardian. No order is placed, and nothing is written to disk."
        ),
    )
    analyze.add_argument(
        "symbol",
        metavar="SYMBOL",
        help=f"The market to analyse. One of: {_known_symbols_text()}",
    )
    analyze.add_argument(
        "--account",
        action="store_true",
        help=(
            "Review the proposal against the local paper account, so the Risk "
            "Guardian returns a real verdict instead of no_portfolio_store. "
            "Requires an account opened with 'orion account open'. Without this "
            "flag nothing is read from or written to disk."
        ),
    )

    account = subcommands.add_parser(
        "account",
        help="Open or inspect the local paper account. Simulated only; never a broker.",
        description=(
            "Manage the local paper-trading account ORION reviews proposals against. "
            "The account is a simulation held in the database at ORION_DB_PATH: it "
            "reaches no venue, holds no money and places no order. It is kept until "
            "you delete that file yourself - ORION never removes or resets it."
        ),
    )
    account_actions = account.add_subparsers(
        dest="account_command", required=True, metavar="action"
    )

    account_open = account_actions.add_parser(
        "open",
        help="Create the paper account with an opening balance.",
        description=(
            "Create the paper account and its opening balance. Refuses if one already "
            "exists, because re-opening a live account is a deliberate act and this "
            "command will not perform it silently."
        ),
    )
    account_open.add_argument(
        "--balance",
        type=_positive_finite,
        required=True,
        metavar="N",
        help="Opening balance, in account currency. A positive, finite number.",
    )
    account_open.add_argument(
        "--symbol",
        metavar="SYMBOL",
        default=_default_account_symbol(),
        help=(
            f"The market this account trades. One of: {_known_symbols_text()}. "
            f"Defaults to {_default_account_symbol()}."
        ),
    )

    account_actions.add_parser(
        "show",
        help="Print the current paper account. Reads only; never creates or changes one.",
        description=(
            "Print the current paper account. This command never creates a database, "
            "never opens an account and never modifies one."
        ),
    )
    return parser


def render_report(report: AnalysisReport) -> str:
    """A deterministic, terminal-shaped rendering of `report`.

    Deliberately not `orion.orchestrator.response.summarize`, which is written
    for *speech* ("by R S I", "percent") and reads wrong on a terminal. Every
    value below is read straight off the report: nothing is recomputed, and an
    absent reading is said out loud rather than substituted with a number.
    """
    rsi = report.evidence.indicators.rsi
    momentum = "not enough history yet" if rsi is None else f"{rsi:.1f}"

    if report.risk.availability is RiskAvailability.AVAILABLE:
        # Guaranteed non-None by `RiskPanel`'s own validator.
        assert report.risk.verdict is not None
        risk = report.risk.verdict.outcome.value
    else:
        risk = report.risk.availability.value

    rows = (
        ("analysed", report.analyzed_at.isoformat()),
        ("analysis id", report.analysis_id),
        ("trend", report.evidence.structure.trend.value),
        ("volatility", report.evidence.structure.volatility.value),
        ("momentum (RSI)", momentum),
        ("proposal", f"{report.decision.action.value} {report.decision.direction.value}"),
        ("confidence", f"{report.reasoning.confidence * 100:.0f}%"),
        ("risk guardian", risk),
    )
    width = max(len(label) for label, _ in rows)
    lines = [f"{report.symbol} {report.timeframe.value}"]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in rows)
    lines.append("")
    lines.append(report.reasoning.rationale)
    return "\n".join(lines)


def render_account(record: PortfolioRecord) -> str:
    """A deterministic, terminal-shaped rendering of one account snapshot.

    Every value is read straight off the stored `PortfolioState`: nothing is
    recomputed, re-marked or derived, so what prints is exactly what the Risk
    Guardian would be handed. Money is fixed to two places rather than formatted
    by locale, for the same reason the frontend refuses locale formatting — a
    number that renders differently on two machines is not a record.
    """
    state = record.state
    if state.open_position is None:
        position = "none"
    else:
        held = state.open_position
        position = (
            f"{held.direction.value} {held.quantity:g} @ {held.entry_price:.2f} "
            f"(stop {held.stop_loss:.2f}, target {held.take_profit:.2f})"
        )

    rows = (
        ("as of", state.as_of.isoformat()),
        ("revision", str(record.revision)),
        ("cash", f"{state.cash:.2f}"),
        ("equity", f"{state.equity:.2f}"),
        ("starting balance", f"{state.starting_balance:.2f}"),
        ("equity peak", f"{state.equity_peak:.2f}"),
        ("open position", position),
    )
    width = max(len(label) for label, _ in rows)
    lines = [f"{state.symbol} paper account"]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in rows)
    return "\n".join(lines)


def _run_account_open(
    symbol_argument: str,
    balance: float,
    *,
    store_factory: StoreFactory,
) -> int:
    """Create the paper account. Never raises, never overwrites."""
    symbol = _resolve_symbol(symbol_argument)
    if symbol is None:
        print(UNKNOWN_SYMBOL_MESSAGE, file=sys.stderr)
        print(f"ORION can analyse: {_known_symbols_text()}", file=sys.stderr)
        return EXIT_USAGE

    db_path = _account_db_path()
    if _account_exists(db_path):
        # Checked before the engine is built, so a second `open` cannot append a
        # revision to a live account. `initialize()` would refuse too, but only
        # after `create_memory_engine` had already touched the file.
        print(ACCOUNT_EXISTS_MESSAGE, file=sys.stderr)
        return EXIT_ACCOUNT_ERROR

    state = initial_portfolio_state(
        symbol=symbol,
        starting_balance=balance,
        now=datetime.now(UTC),
    )
    try:
        record = store_factory(db_path).initialize(state)
    except Exception:
        # Broad and message-free, exactly like the analysis path. A
        # `PortfolioStoreError` embeds SQLAlchemy text that can name the file and
        # its path, and an unreadable directory raises an OSError that names it
        # outright. Neither is bound, formatted or re-raised.
        print(ACCOUNT_NOT_WRITTEN_MESSAGE, file=sys.stderr)
        return EXIT_ACCOUNT_ERROR

    print(render_account(record))
    return EXIT_OK


def _run_account_show(*, store_factory: StoreFactory) -> int:
    """Print the paper account. Reads only; creates nothing."""
    db_path = _account_db_path()
    if not _account_exists(db_path):
        # The existence check is what keeps `show` non-mutating: opening a store
        # to discover there is no account would leave behind the database that
        # discovery created.
        print(NO_ACCOUNT_MESSAGE, file=sys.stderr)
        return EXIT_NO_ACCOUNT

    try:
        record = store_factory(db_path).load()
    except Exception:
        print(ACCOUNT_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return EXIT_ACCOUNT_ERROR

    print(render_account(record))
    return EXIT_OK


def _run_analysis(
    symbol: str,
    *,
    service_factory: ServiceFactory,
    account_service_factory: AccountServiceFactory,
    store_factory: StoreFactory,
    use_account: bool,
) -> int:
    """Resolve `symbol`, run one analysis, print it. Never raises."""
    intent = parse_intent(f"analyze {symbol}")
    if intent.kind is not IntentKind.ANALYZE:
        # The argument is not echoed back. It is the user's own shell input and
        # harmless here, but this codebase does not reflect input it rejected
        # (see `orion.interface.app._handle_validation_error`), and naming the
        # supported symbols is the more useful half of the message anyway.
        print(UNKNOWN_SYMBOL_MESSAGE, file=sys.stderr)
        print(f"ORION can analyse: {_known_symbols_text()}", file=sys.stderr)
        return EXIT_USAGE

    if use_account:
        db_path = _account_db_path()
        if not _account_exists(db_path):
            # Refused rather than answered with a database this command created
            # in order to find it empty. `--account` asks ORION to review against
            # an account, and there is not one.
            print(NO_ACCOUNT_MESSAGE, file=sys.stderr)
            return EXIT_NO_ACCOUNT
        try:
            store = store_factory(db_path)
        except Exception:
            print(ACCOUNT_UNAVAILABLE_MESSAGE, file=sys.stderr)
            return EXIT_ACCOUNT_ERROR
        service = account_service_factory(store)
    else:
        # Byte-for-byte the Mission 12.4 path: no store, no engine, no file.
        service = service_factory()

    if service is None:
        print(NOT_CONFIGURED_MESSAGE, file=sys.stderr)
        return EXIT_NOT_CONFIGURED

    try:
        report = service.run(intent, now=datetime.now(UTC))
    except Exception:
        # Deliberately broad, and deliberately message-free. `MarketDataProvider
        # Error`, `ReasoningProviderError`, `DecisionEngineError`,
        # `RiskGuardianError` and `AnalysisDataQualityError` all carry messages
        # that may name a vendor, a URL or a raw provider response. The
        # exception is not bound, not formatted and not re-raised, so nothing it
        # knows can reach the terminal.
        print(ANALYSIS_FAILED_MESSAGE, file=sys.stderr)
        return EXIT_ANALYSIS_FAILED

    print(render_report(report))
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    service_factory: ServiceFactory | None = None,
    account_service_factory: AccountServiceFactory | None = None,
    store_factory: StoreFactory | None = None,
) -> int:
    """The `orion` console entrypoint. Returns a process exit code.

    `argv` defaults to the real command line. The three factories are test seams
    and nothing else: none is reachable from the command line, so no invocation
    can swap out how ORION is wired or where its account lives. `store_factory`
    in particular cannot be used to redirect the database — it is handed the path
    `_account_db_path` resolved from `ORION_DB_PATH`, and never one built from an
    argument.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits directly — 0 for `--help`, 2 for a bad invocation.
        # Turning that back into a return keeps every path out of this function
        # uniform, and keeps the message argparse already wrote to stderr.
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    if args.command == "account":
        # `add_subparsers(required=True)` guarantees both levels were given.
        if args.account_command == "open":
            return _run_account_open(
                str(args.symbol),
                float(args.balance),
                store_factory=store_factory or _default_store_factory,
            )
        return _run_account_show(store_factory=store_factory or _default_store_factory)

    return _run_analysis(
        str(args.symbol),
        service_factory=service_factory or _default_service_factory,
        account_service_factory=account_service_factory or _account_service_factory,
        store_factory=store_factory or _default_store_factory,
        use_account=bool(args.account),
    )
