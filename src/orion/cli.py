"""ORION's command line, over the orchestrator and paper engine that exist.

`pyproject.toml` has advertised `orion = "orion.cli:main"` since Mission 0 and
this module was never written, so the installed `orion` command has always
failed with `ModuleNotFoundError` before printing anything. This file is that
entrypoint, and nothing more.

ANALYSIS, A LOCAL PAPER ACCOUNT, AND ONE FORWARD CYCLE. `orion analyze XAUUSD`
runs exactly the pipeline Mission 10 Phase 5.1 assembled — market data,
validation, deterministic analysis, AI reasoning, the decision engine, the Risk
Guardian — through `orion.orchestrator.wiring.build_analysis_service` and
`AnalysisService.run`. It adds no stage, reorders none and skips none. `orion
account open` / `orion account show` manage the simulated account that Mission 4
built and nothing had ever opened. `orion paper XAUUSD` runs that same analysis
and then, if and only if the Risk Guardian approved it, opens a simulated
position through `orion.paper.session.run_cycle`. `orion reflect XAUUSD` reads
back what those cycles actually did.

REFLECTION READS HISTORY AND NOTHING ELSE. `reflect` runs no analysis, contacts
no vendor and needs no credential: it opens the journal the paper cycle wrote,
walks each closed trade back to the verdict and decision that authorised it, and
prints the facts. It records nothing and changes no account. It cannot bring a
database into existence either — like `account show`, it checks the file exists
first — though it shares that verb's one honest caveat: `create_memory_engine`
runs `metadata.create_all`, so opening an *existing* database will add any table
its schema is missing. That is a schema repair on a file the user already has,
not a record, and it is the same seam every other verb here opens through.
`orion.reflection` itself takes a `DecisionJournal` and never a
`PortfolioStore`, so there is no path through it to current account state at
all, and it reports what was predicted beside what happened without scoring
either.

ONE CYCLE PER INVOCATION, AND THEN THE PROCESS EXITS — WITH ONE NAMED
EXCEPTION. `analyze`, `paper`, `resolve`, `reflect` and `account` each do one
thing once and return; there is no retry, no daemon and no background thread any
of them starts without being asked.

`orion monitor` IS THE EXCEPTION, AND IT IS EXPLICIT (Mission 9 Phase 3). It
repeats `resolve` on an interval the user supplies and keeps running until a
signal stops it — because a stop-loss that is only checked when somebody types a
command is not a stop-loss. The loop and the only sleep in `src/orion/` live in
`orion.monitor.supervisor`, never in this file; this module builds the
collaborators for each pass and hands them over, exactly as it does for every
other verb. Nothing schedules that verb either: it runs in the foreground
because someone typed it, and restarting it is a process supervisor's job.
`tests/monitor/test_import_hygiene.py` asserts that the supervisor is the one
place in the package tree that loops or sleeps.

THE REPORT IS UNPACKED HERE, BECAUSE ONLY HERE CAN IT BE.
`tests/orchestrator/test_import_hygiene.py` forbids every engine package,
`orion.paper` included, from importing `orion.orchestrator`. So
`orion.paper.session.run_cycle` takes the domain objects an analysis produced —
`ProposedDecision`, `Evidence`, `RiskVerdict` — and this module, which may see
both sides, takes them off the `AnalysisReport` and hands them over. The session
never learns that an `AnalysisReport` exists.

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

RESOURCES ARE OWNED AND RELEASED, NOT LEFT TO PROCESS EXIT. `orion paper` holds
an account open across a read, an execution and two writes, so
`_default_paper_resources` is a context manager that disposes its engine in a
`finally` — on the failure path too. Left alive, a pooled connection keeps
SQLite's `-wal`/`-shm` sidecars locked, which a user cannot then delete. The two
account verbs still leave their engine to process exit, which is harmless for a
command that immediately exits and is recorded rather than quietly assumed.

NO LOGGING, AND NO NETWORK OF ITS OWN. This module imports no logging framework
and makes no request itself: the only network traffic is the providers' own,
inside `AnalysisService.run`. Nothing the user types is written to the database —
the account holds balances, positions, fills and closed positions, never a
transcript, an argument or a rationale.

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
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from orion.config import get_settings
from orion.data.models import PriceObservation
from orion.data.provider_base import MarketDataProvider
from orion.memory.engine import create_memory_engine
from orion.memory.journal import DecisionJournal
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.monitor.iteration import IterationResult, IterationStatus, resolve_once
from orion.monitor.supervisor import (
    MonitorStop,
    ShutdownSignal,
    Sleeper,
    install_shutdown_handlers,
    run_monitor,
)
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.intent import DEFAULT_TIMEFRAME, known_symbols, parse_intent
from orion.orchestrator.schemas import AnalysisReport, IntentKind, RiskAvailability
from orion.orchestrator.wiring import build_analysis_service, build_market_data_provider
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason
from orion.paper.session import (
    CycleOutcome,
    CycleResult,
    JournalWriter,
    ResolutionCoverage,
    run_cycle,
)
from orion.paper.store import PortfolioRecord, PortfolioStore
from orion.reflection.analysis import reflect_on_recent_closed_positions
from orion.reflection.schemas import ReflectionBatch, TradeReflection
from orion.risk.policy import RiskPolicy
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
#: One forward paper cycle ran, and something in it refused. Distinct from a
#: crash and from a veto, both of which are ordinary outcomes reported on stdout.
EXIT_CYCLE_FAILED = 7

#: The journal could not be opened or read. A journal holding no closed trade
#: yet is not this: an empty history is an ordinary answer, printed and exit 0.
EXIT_REFLECTION_FAILED = 8

#: The market price could not be fetched or was not usable. Separate from
#: `EXIT_ANALYSIS_FAILED` because no analysis was attempted: the only thing that
#: failed is the one request `resolve` makes.
EXIT_PRICE_UNAVAILABLE = 9

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
INVALID_LIMIT_MESSAGE = "must be a whole number of at least 1"
CYCLE_FAILED_MESSAGE = "ORION could not complete the paper cycle."
#: The database holds both the account and the journal, so no file means no
#: trading history to reflect on. Named separately from the account message
#: because `reflect` does not need an account, only a history.
NO_HISTORY_MESSAGE = "No paper history exists. Run 'orion paper SYMBOL' first."
#: `DecisionJournalError` embeds SQLAlchemy text naming the database and its
#: path, and `DecisionJournalIntegrityError` names a stored fingerprint. Neither
#: is bound, formatted or re-raised; this fixed sentence is printed instead.
REFLECTION_FAILED_MESSAGE = "ORION could not read the trading journal."

#: `resolve` needs one credential, not two. Named separately from
#: `NOT_CONFIGURED_MESSAGE` so it never tells a user to set a key it does not
#: use -- see `orion.orchestrator.wiring.market_data_available`.
NOT_CONFIGURED_FOR_PRICES_MESSAGE = (
    "ORION is not configured to fetch prices. Set TWELVE_DATA_API_KEY in .env."
)
#: `MarketDataProviderError` messages name the vendor and can carry response
#: text; the data-quality path names bar timestamps. Neither is formatted here.
PRICE_UNAVAILABLE_MESSAGE = "ORION could not fetch a usable market price."
#: Not a failure and not a refusal: an account with nothing open has nothing to
#: resolve, which is an ordinary answer printed on stdout with exit 0.
NO_OPEN_POSITION_MESSAGE = "No open position. There is nothing to resolve."

#: The smallest interval `orion monitor` accepts, in seconds. A floor, not a
#: recommendation: on the default 1h chart even this is sixty checks per bar,
#: and anything faster is spending a rate-limited vendor quota to learn nothing.
#: There is deliberately no default interval -- see `_monitor_interval`.
MIN_MONITOR_INTERVAL_SECONDS = 60.0
INVALID_INTERVAL_MESSAGE = (
    f"must be a finite number of seconds, at least {MIN_MONITOR_INTERVAL_SECONDS:g}"
)
#: Graceful stop: a signal arrived, the pass in flight finished, nothing was
#: interrupted. Printed on stdout because it is the expected way to end.
MONITOR_STOPPED_MESSAGE = "ORION stopped monitoring. The last check completed before exiting."
#: The ceiling was reached -- see `orion.monitor.supervisor.MAX_CONSECUTIVE_FAILURES`.
MONITOR_CEILING_MESSAGE = (
    "ORION stopped monitoring after repeated failures. The paper account was not changed by them."
)
#: An unexpected exception escaped an iteration. Fixed, like every other
#: failure here: the exception is never bound, formatted or printed.
MONITOR_FAILED_MESSAGE = "ORION could not continue monitoring."

#: How many closed trades `reflect` reports when no limit is given. A default,
#: not a cap: the journal keeps every trade and `--limit` reaches further back.
DEFAULT_REFLECTION_LIMIT = 10

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

#: How `resolve` obtains a price source. Deliberately *not* `ServiceFactory`:
#: an `AnalysisService` carries a reasoning provider, and handing one to the
#: resolution path would put an Anthropic client on it. `None` means "this
#: deployment cannot fetch prices", the same degradation `ServiceFactory` uses.
MarketDataFactory = Callable[[], MarketDataProvider | None]


def _default_service_factory() -> AnalysisService | None:
    """Build one `AnalysisService` from the environment, or `None`.

    Settings are read per invocation rather than captured at import, matching
    `orion.interface.app._default_analysis_factory` and for the same stated
    reason: `RiskPolicy.from_settings` requires a fresh, uncached
    `get_settings()` so engaging the kill switch never needs a restart.
    """
    return build_analysis_service(get_settings())


def _default_market_data_factory() -> MarketDataProvider | None:
    """Build one market-data provider from the environment, or `None`.

    Settings read per invocation, like every other factory here. Nothing else
    is constructed: no reasoning provider, no risk policy, no analyst — see
    `orion.orchestrator.wiring.build_market_data_provider`.
    """
    return build_market_data_provider(get_settings())


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


#: How the paper cycle obtains its two durable collaborators *and* gives them
#: back. A context manager rather than a plain factory because 4.2a recorded
#: that the CLI leaked its engine until process exit: harmless for a command
#: that then exits, but this cycle holds an account open across a read, an
#: execution and two writes, and a leaked handle keeps the SQLite WAL files
#: locked. Ownership is explicit and disposal is in a `finally`.
PaperResources = Callable[[str], AbstractContextManager[tuple[PortfolioStore, JournalWriter]]]


@contextmanager
def _default_paper_resources(db_path: str) -> Iterator[tuple[PortfolioStore, JournalWriter]]:
    """Open the account and its journal on one engine, and always close it.

    ONE ENGINE, DELIBERATELY. The store and the journal are backed by the same
    database file, so two engines would mean two connection pools competing for
    the same SQLite writer lock — and `orion.memory.schema`'s own docstring
    explains that the store's compare-and-swap safety already rests on that
    single-writer behaviour. Sharing one engine does not make the two writes a
    single transaction (Mission 5's Finding A is explicit that they commit
    independently, which is exactly why the ordering in `run_cycle` matters);
    it only means one pool and one thing to dispose.
    """
    engine = create_memory_engine(db_path)
    try:
        yield SqlitePortfolioStore(engine), SqliteDecisionJournal(engine)
    finally:
        # Releases every pooled connection, which is what lets SQLite
        # checkpoint and remove the -wal/-shm sidecars. Runs on the exception
        # path too, so a failed cycle leaks no handle either.
        engine.dispose()


#: How `reflect` obtains a read-only journal, and gives it back. The same
#: ownership discipline as `PaperResources` and for the same reason: a leaked
#: pooled connection keeps SQLite's `-wal`/`-shm` sidecars locked. Typed against
#: `DecisionJournal` -- the ABC -- so this seam cannot hand reflection a store.
ReflectionResources = Callable[[str], AbstractContextManager[DecisionJournal]]


@contextmanager
def _default_reflection_resources(db_path: str) -> Iterator[DecisionJournal]:
    """Open the journal at `db_path` for reading, and always close it.

    No `PortfolioStore` is constructed: reflection reads history and has no
    business with the current account. `create_memory_engine` would create the
    database if it were absent, which is why every caller checks the file exists
    first -- a command that answers "no history" by creating an empty journal
    has not answered anything.
    """
    engine = create_memory_engine(db_path)
    try:
        yield SqliteDecisionJournal(engine)
    finally:
        engine.dispose()


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


def _monitor_interval(text: str) -> float:
    """An `argparse` type for a monitoring interval, in seconds.

    THERE IS NO DEFAULT, AND THAT IS THE POINT. A cadence is a deployment
    decision, and shipping one would make it the number everybody runs without
    ever having chosen it -- the same reason `orion account open` refuses to
    guess a balance. So the flag is required and this only decides which values
    are sane.

    `nan` and `inf` both parse as floats and neither is a duration: `nan` fails
    every comparison silently, and `inf` would mean one pass and then a sleep
    that never ends. Both are refused here, along with anything below
    `MIN_MONITOR_INTERVAL_SECONDS`, so argparse prints its own one-line usage
    error and exits 2 without a traceback.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(INVALID_INTERVAL_MESSAGE) from None
    if not math.isfinite(value) or value < MIN_MONITOR_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(INVALID_INTERVAL_MESSAGE)
    return value


def _positive_int(text: str) -> int:
    """An `argparse` type for a count: a whole number of at least 1.

    `int(text)` refuses `1.5`, `nan` and `inf` outright, so unlike
    `_positive_finite` there is no float edge case to screen for -- only the
    non-positive range. argparse prints its own one-line usage error and exits
    2, with no traceback and without echoing the value back.
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(INVALID_LIMIT_MESSAGE) from None
    if value < 1:
        raise argparse.ArgumentTypeError(INVALID_LIMIT_MESSAGE)
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

    paper = subcommands.add_parser(
        "paper",
        help="Run one forward paper cycle against the local account. Simulated only.",
        description=(
            "Run one analysis of SYMBOL and, if and only if the Risk Guardian approves "
            "it, open a simulated position in the local paper account. Requires an "
            "account opened with 'orion account open'. Runs exactly one cycle and "
            "exits: there is no scheduler, no daemon and no retry. Nothing reaches a "
            "broker or any venue, and no real order is ever placed."
        ),
    )
    paper.add_argument(
        "symbol",
        metavar="SYMBOL",
        help=f"The market to trade on paper. One of: {_known_symbols_text()}",
    )

    resolve = subcommands.add_parser(
        "resolve",
        help="Check the open paper position against the latest price. No analysis.",
        description=(
            "Fetch the latest price for SYMBOL and close the open paper position if it "
            "has reached the stop-loss or take-profit the Risk Guardian approved when "
            "the position was opened. Runs no analysis, calls no AI model and needs no "
            "ANTHROPIC_API_KEY - only TWELVE_DATA_API_KEY. Runs exactly once and exits: "
            "there is no scheduler, no daemon and no retry. It never opens a position, "
            "and nothing reaches a broker or any venue."
        ),
    )
    resolve.add_argument(
        "symbol",
        metavar="SYMBOL",
        help=f"The market whose open position to resolve. One of: {_known_symbols_text()}",
    )

    monitor = subcommands.add_parser(
        "monitor",
        help="Resolve the open position on a repeating interval. Runs until stopped.",
        description=(
            "Run 'orion resolve SYMBOL' every --interval seconds until interrupted. This is "
            "the one command that keeps running: everything else does one thing once. It "
            "calls no AI model and needs no ANTHROPIC_API_KEY, only TWELVE_DATA_API_KEY. It "
            "never opens a position, reaches no broker or venue, and places no real order. "
            "Ctrl-C or SIGTERM stops it after the check in progress finishes, never during "
            "one. It is a foreground process: restarting it is your process supervisor's "
            "job, not ORION's."
        ),
    )
    monitor.add_argument(
        "symbol",
        metavar="SYMBOL",
        help=f"The market whose open position to monitor. One of: {_known_symbols_text()}",
    )
    monitor.add_argument(
        "--interval",
        type=_monitor_interval,
        required=True,
        metavar="SECONDS",
        help=(
            f"Seconds between checks. Required and has no default: at least "
            f"{MIN_MONITOR_INTERVAL_SECONDS:g}. ORION does not choose a cadence for you."
        ),
    )

    reflect = subcommands.add_parser(
        "reflect",
        help="Print what ORION predicted against what actually happened. Reads only.",
        description=(
            "Read the trading journal and print, for each of SYMBOL's most recently "
            "closed paper trades, what was predicted beside what actually happened. "
            "Runs no analysis, contacts no vendor and needs no credential; it "
            "records nothing, creates no database and never changes the account. "
            "ORION reports the facts and scores neither the outcome nor the decision."
        ),
    )
    reflect.add_argument(
        "symbol",
        metavar="SYMBOL",
        help=f"The market to reflect on. One of: {_known_symbols_text()}",
    )
    reflect.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_REFLECTION_LIMIT,
        metavar="N",
        help=(
            f"How many of the most recent closed trades to reflect on. "
            f"Defaults to {DEFAULT_REFLECTION_LIMIT}."
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


def render_specialists(report: AnalysisReport) -> list[str]:
    """The specialist section of a report, as terminal lines.

    WHAT THE TERMINAL GETS THAT THE BROWSER DOES NOT. `orion.interface` drops
    the 64-character evidence fingerprint and the market-data provider from the
    wire, on the allowlist discipline `_to_analysis_summary` has always applied.
    Neither is dropped here: a local operator reading their own machine is the
    party who needs to check that a finding describes the same bars the
    proposal was reasoned from, and the fingerprint is the only thing that
    proves it.

    NOTHING IS RECOMPUTED AND NOTHING IS SUMMARISED. Each finding prints the
    concept, the bias, and the analyst's own `observation` -- the formatted
    numbers -- and the interpretation is deliberately left out of this compact
    view rather than truncated, because a half-sentence of canonical wording
    would misrepresent it. The full text is on the report for any caller that
    wants it.

    Concepts ORION could not evaluate are listed with their reason. That is the
    line that stops this rendering from reading as a claim of completeness.
    """
    output = report.technical.output
    lines: list[str] = []

    for timeframe in output.timeframes:
        lines.append("")
        lines.append(
            f"  {timeframe.timeframe.value} technical  "
            f"{timeframe.bias.value}, volatility {timeframe.volatility.value}, "
            f"{timeframe.bar_count} bars to {timeframe.as_of.isoformat()}"
        )
        lines.append(f"    evidence   {timeframe.evidence_fingerprint}")
        lines.append(f"    source     {timeframe.source_provider}")
        if timeframe.findings:
            width = max(len(item.concept.value) for item in timeframe.findings)
            lines.extend(
                f"    {item.concept.value.ljust(width)}  {item.bias.value:8}  {item.observation}"
                for item in timeframe.findings
            )
        if timeframe.unevaluated:
            lines.append(
                "    not evaluated: "
                + ", ".join(
                    f"{item.concept.value} ({item.reason.value})" for item in timeframe.unevaluated
                )
            )

    thesis = report.thesis.thesis
    lines.append("")
    if thesis is None:
        lines.append(f"  thesis     {report.thesis.detail}")
        return lines

    lines.append(f"  thesis     {thesis.alignment.value}")
    lines.append(f"    reading    technical {thesis.technical_condition.value}")
    if thesis.macro_condition is not None:
        lines.append(f"    macro      {thesis.macro_condition.value}")
    for point in thesis.agreements + thesis.conflicts:
        marker = "agree" if point.agreed else "conflict"
        macro_reading = "" if point.macro_reading is None else f" vs macro {point.macro_reading}"
        lines.append(
            f"    {marker:9}  {point.topic.value}: technical "
            f"{point.technical_reading}{macro_reading}"
        )
    lines.append("")
    lines.append(f"  {thesis.summary}")
    return lines


def render_report(report: AnalysisReport) -> str:
    """A deterministic, terminal-shaped rendering of `report`.

    Deliberately not `orion.orchestrator.response.summarize`, which is written
    for *speech* ("by R S I", "percent") and reads wrong on a terminal. Every
    value below is read straight off the report: nothing is recomputed, and an
    absent reading is said out loud rather than substituted with a number.

    The specialist section is appended by `render_specialists`, after the
    proposal and the Risk Guardian's answer. The order is the point: what the
    Guardian said is read before what the analysts observed, because only one
    of the two decides anything.
    """
    rsi = report.evidence.indicators.rsi
    momentum = "not enough history yet" if rsi is None else f"{rsi:.1f}"

    if report.risk.availability is RiskAvailability.AVAILABLE:
        # Guaranteed non-None by `RiskPanel`'s own validator.
        assert report.risk.verdict is not None
        risk = report.risk.verdict.outcome.value
    else:
        risk = report.risk.availability.value

    technical = report.technical.output
    rows = (
        ("analysed", report.analyzed_at.isoformat()),
        ("analysis id", report.analysis_id),
        ("trend", report.evidence.structure.trend.value),
        ("volatility", report.evidence.structure.volatility.value),
        ("momentum (RSI)", momentum),
        ("proposal", f"{report.decision.action.value} {report.decision.direction.value}"),
        ("confidence", f"{report.reasoning.confidence * 100:.0f}%"),
        ("risk guardian", risk),
        # The analyst's own aggregate, and the count of what it could and could
        # not evaluate. Deliberately not the availability enum's own word:
        # `analyze --account`'s rendering already reserves "available" for the
        # Risk Guardian panel, and two panels using it for different things
        # would make the terminal ambiguous about which one is speaking.
        (
            "technical",
            f"{technical.bias.value} from {len(technical.findings)} findings across "
            f"{len(technical.timeframes)} timeframe(s)",
        ),
    )
    width = max(len(label) for label, _ in rows)
    lines = [f"{report.symbol} {report.timeframe.value}"]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in rows)
    lines.append("")
    lines.append(report.reasoning.rationale)
    lines.extend(render_specialists(report))
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


#: What each `exit_matched_*` combination means in words. A closed mapping over
#: `CloseReason`, so a new close reason fails at import rather than printing an
#: empty cell -- the same discipline `_CYCLE_SUMMARY` applies to `CycleOutcome`.
_EXIT_MATCH: dict[CloseReason, str] = {
    CloseReason.TAKE_PROFIT_HIT: "the predicted take-profit",
    CloseReason.STOP_LOSS_HIT: "the predicted stop-loss",
}

#: Printed where the specialists' reading would go when the journal holds none
#: for that trade -- every trade opened before Mission 8 v2.2, and any analysis
#: where neither specialist had anything to say. Absence is stated, never left
#: as a blank line a reader could mistake for "no opinion".
NO_THESIS_RECORDED = "no thesis was journaled for this trade"


def _thesis_rows(item: TradeReflection) -> tuple[tuple[str, str], ...]:
    """The journaled specialists' reading for one trade, as label/value rows.

    Takes the `TradeReflection` rather than the `MarketThesis` on purpose:
    `orion.cli` deliberately never names that type, so it never imports
    `orion.thesis` either, and `tests/thesis/test_import_hygiene.py` asserts
    the package's importers are exactly the five that must name it. Reading
    fields off an attribute of a report is how this module already handles
    `report.thesis.thesis` in `render_specialists`.

    Absence gets a row of its own. A trade opened before v2.2, or one whose
    analysis formed no thesis, prints `NO_THESIS_RECORDED` -- the same
    "absence is reported, never filled in" rule the Technical Analyst applies
    to a concept it could not evaluate.
    """
    thesis = item.thesis
    if thesis is None:
        return (("specialists", NO_THESIS_RECORDED),)

    reading = f"technical {thesis.technical_bias.value} ({thesis.technical_condition.value})"
    if thesis.macro_condition is not None:
        reading += f", macro {thesis.macro_condition.value}"
    rows = [("specialists", thesis.alignment.value), ("reading", reading)]
    for point in thesis.agreements + thesis.conflicts:
        macro_reading = "" if point.macro_reading is None else f" vs macro {point.macro_reading}"
        rows.append(
            (
                "agree" if point.agreed else "conflict",
                f"{point.topic.value}: technical {point.technical_reading}{macro_reading}",
            )
        )
    # Last of the thesis rows, and the analyst's own sentence: `MarketThesis`
    # validates that `summary` is the canonical wording for its alignment, so
    # this line is looked up rather than written, here as everywhere else.
    rows.append(("thesis", thesis.summary))
    return tuple(rows)


def render_reflections(batch: ReflectionBatch) -> str:
    """A deterministic, terminal-shaped rendering of one `ReflectionBatch`.

    Every value is read straight off a `TradeReflection`, which is itself
    computed from journaled records. NOTHING HERE JUDGES THE TRADE. There is no
    win rate, no score, no "good"/"bad" and no aggregate P&L: `orion.reflection`
    deliberately produces facts rather than a verdict, precisely so nothing
    downstream can turn one closed trade into a strategy change, and a renderer
    that summed them into a league table would reintroduce exactly that. The
    approval margin and the matched exit level are printed side by side because
    that is what tells a bad outcome apart from a bad decision -- the reading is
    the user's.

    THE THESIS IS PRINTED BESIDE THE OUTCOME, AND NOTHING IS CONCLUDED FROM THE
    PAIR. Mission 8 v2.2 journals the specialists' reading with the chain that
    authorised a trade and `orion.reflection` recovers it by `analysis_id`;
    without these lines it would be stored and never read, which is the state
    v2.1 fixed for the analysis path and this fixes for the reflection one.
    Every value comes off the `MarketThesis` itself -- its own enum values and
    the canonical `summary` its schema validated -- so this renderer authors no
    market commentary, exactly like `render_specialists`. It draws no line
    between what the specialists saw and how the trade ended: a bullish thesis
    before a loss is one fact printed beside another, and the reading is the
    user's here too.
    """
    header = [
        ("closed trades", str(batch.total_closed_positions)),
        ("reflected", str(len(batch.reflections))),
        ("not reflectable", str(len(batch.unreflectable_fingerprints))),
        ("reflected at", batch.reflected_at.isoformat()),
    ]
    width = max(len(label) for label, _ in header)
    lines = [f"{batch.symbol} reflection"]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in header)

    for index, item in enumerate(batch.reflections, start=1):
        margin = f"{item.approval_margin:+.2f}"
        fees = "unknown" if item.opening_fee is None else f"{item.opening_fee:.2f}"
        rows = (
            ("predicted", f"{item.predicted_action.value} {item.predicted_direction.value}"),
            ("confidence", f"{item.predicted_confidence * 100:.0f}%"),
            ("risk level", item.predicted_risk_level.value),
            (
                "levels",
                f"stop {item.predicted_stop_loss:.2f}, target {item.predicted_take_profit:.2f}",
            ),
            (
                "risk/reward",
                f"{item.predicted_risk_reward:.2f} predicted against a "
                f"{item.policy_min_risk_reward:.2f} minimum ({margin})",
            ),
            # Between the prediction and the outcome, because that is what it
            # is: what the specialists were reading when this trade was opened.
            *_thesis_rows(item),
            ("exit matched", _EXIT_MATCH[item.close_reason]),
            ("entry / exit", f"{item.entry_price:.2f} / {item.exit_price:.2f}"),
            ("realised", f"{item.realized_pnl:.2f}"),
            ("fees", f"{fees} opening, {item.closing_fee:.2f} closing"),
            ("closed position", item.closed_position_fingerprint),
            ("verdict", item.opened_by_verdict_fingerprint),
            ("decision", item.decision_fingerprint),
        )
        inner = max(len(label) for label, _ in rows)
        lines.append("")
        lines.append(f"  {index}. {item.close_reason.value}")
        lines.extend(f"     {label.ljust(inner)}  {value}" for label, value in rows)

    if batch.unreflectable_fingerprints:
        lines.append("")
        # Named, not counted away: a closed trade ORION cannot explain is a fact
        # about the journal the user is entitled to see and go look at.
        lines.append(
            "  These closed trades have no journaled verdict or decision to trace "
            "back to, so ORION cannot say what it predicted for them:"
        )
        lines.extend(f"     {fingerprint}" for fingerprint in batch.unreflectable_fingerprints)

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


#: What each cycle outcome means on a terminal. Fixed sentences chosen at
#: development time, selected by a closed enum -- never built from an exception.
_CYCLE_SUMMARY: dict[CycleOutcome, str] = {
    CycleOutcome.EXECUTED: "opened a paper position",
    CycleOutcome.CLOSED: "closed the open paper position",
    CycleOutcome.HOLDING: "holding; neither stop nor target was reached",
    CycleOutcome.VETOED: "no trade; the Risk Guardian did not approve it",
    CycleOutcome.NO_VERDICT: "no trade; the Risk Guardian returned no verdict",
    CycleOutcome.NOT_TRADEABLE: "no trade; the approved size is not tradeable",
    CycleOutcome.PORTFOLIO_MISMATCH: "no trade; the account is not the one that was reviewed",
    CycleOutcome.EXECUTION_REFUSED: "no trade; the simulated execution refused it",
    CycleOutcome.ACCOUNT_UNAVAILABLE: "no trade; the paper account could not be read or written",
    CycleOutcome.JOURNAL_UNAVAILABLE: (
        "position saved, but the journal could not record it; the account is correct"
    ),
}

#: Outcomes that mean the cycle could not do its job. A veto is not among them:
#: the Risk Guardian refusing a trade is the system working.
_CYCLE_FAILURES = frozenset(
    {
        CycleOutcome.PORTFOLIO_MISMATCH,
        CycleOutcome.EXECUTION_REFUSED,
        CycleOutcome.ACCOUNT_UNAVAILABLE,
        CycleOutcome.JOURNAL_UNAVAILABLE,
    }
)


def render_cycle(symbol: str, report: AnalysisReport, result: CycleResult) -> str:
    """A deterministic, terminal-shaped rendering of one paper cycle.

    Every value is read straight off the report or off the objects the engines
    produced. Nothing is recomputed and nothing is substituted: a cycle that
    opened no position prints no fill, and a cycle that wrote no journal row
    prints no fingerprint.
    """
    if report.risk.availability is RiskAvailability.AVAILABLE:
        assert report.risk.verdict is not None  # guaranteed by RiskPanel
        risk = report.risk.verdict.outcome.value
    else:
        risk = report.risk.availability.value

    rows = [
        ("outcome", result.outcome.value),
        ("summary", _CYCLE_SUMMARY[result.outcome]),
        ("proposal", f"{report.decision.action.value} {report.decision.direction.value}"),
        ("risk guardian", risk),
    ]

    if result.fill is not None:
        fill = result.fill
        rows.append(
            (
                "fill",
                f"{fill.direction.value} {fill.quantity:g} @ {fill.fill_price:.2f} "
                f"fee {fill.fee:.2f}",
            )
        )
    if result.closed_position is not None:
        closed = result.closed_position
        rows.append(
            (
                "closed",
                f"{closed.direction.value} {closed.quantity:g} @ {closed.exit_price:.2f} "
                f"({closed.close_reason.value}) realised {closed.realized_pnl:.2f}",
            )
        )

    rows.append(("account saved", "yes" if result.account_was_written else "no"))

    # Three genuinely different states, said differently: a row was written, a
    # row was owed and could not be written, or there was nothing to record.
    if result.journal_fingerprint is not None:
        journal_status = result.journal_fingerprint
    elif result.outcome is CycleOutcome.JOURNAL_UNAVAILABLE:
        journal_status = "not recorded"
    else:
        journal_status = "nothing to record"
    rows.append(("journal", journal_status))

    if result.portfolio is not None:
        state = result.portfolio
        held = state.open_position
        position = (
            "none"
            if held is None
            else (
                f"{held.direction.value} {held.quantity:g} @ {held.entry_price:.2f} "
                f"(stop {held.stop_loss:.2f}, target {held.take_profit:.2f})"
            )
        )
        rows.extend(
            [
                ("cash", f"{state.cash:.2f}"),
                ("equity", f"{state.equity:.2f}"),
                ("open position", position),
            ]
        )

    width = max(len(label) for label, _ in rows)
    lines = [f"{symbol} paper cycle"]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in rows)
    return "\n".join(lines)


#: Printed for each coverage state. `COMPLETE` still says what it does *not*
#: promise: the newest bar may be forming, and the market-data provider exposes
#: no flag that would say otherwise.
_COVERAGE_SUMMARY: dict[ResolutionCoverage, str] = {
    ResolutionCoverage.COMPLETE: (
        "every bar since the position opened was examined (the newest may still be forming)"
    ),
    ResolutionCoverage.TRUNCATED: (
        "INCOMPLETE - the window did not reach back to when the position opened; "
        "a level may have been crossed in the bars before it"
    ),
    ResolutionCoverage.GAPPED: (
        "INCOMPLETE - bars are missing inside the window; a level may have been crossed in them"
    ),
}


def render_resolution(symbol: str, price: PriceObservation, result: CycleResult) -> str:
    """A deterministic, terminal-shaped rendering of one resolution pass.

    Shorter than `render_cycle` by exactly what did not happen: there is no
    proposal row and no risk-guardian row, because no proposal was made and no
    verdict was sought. Printing either would imply this command consulted
    something it deliberately did not.

    COVERAGE IS ALWAYS PRINTED WHEN IT APPLIES, AND THAT IS THE POINT.
    `HOLDING` reads as "neither stop nor target was reached", which is only
    true if everything since the entry was examined. Showing the outcome
    without the coverage beside it would let an unexamined window look like a
    quiet market -- so the row is unconditional, never suppressed for the happy
    case, and the incomplete states say INCOMPLETE in words rather than leaving
    a reader to infer it from an enum value.
    """
    rows = [
        ("outcome", result.outcome.value),
        ("summary", _CYCLE_SUMMARY[result.outcome]),
        ("price", f"{price.last_price:.2f} as of {price.as_of.isoformat()}"),
    ]

    if result.coverage is not None:
        rows.append(("coverage", _COVERAGE_SUMMARY[result.coverage]))
    if result.triggering_bar_at is not None:
        rows.append(("triggering bar", result.triggering_bar_at.isoformat()))

    if result.closed_position is not None:
        closed = result.closed_position
        rows.append(
            (
                "closed",
                f"{closed.direction.value} {closed.quantity:g} @ {closed.exit_price:.2f} "
                f"({closed.close_reason.value}) realised {closed.realized_pnl:.2f}",
            )
        )

    rows.append(("account saved", "yes" if result.account_was_written else "no"))

    if result.journal_fingerprint is not None:
        journal_status = result.journal_fingerprint
    elif result.outcome is CycleOutcome.JOURNAL_UNAVAILABLE:
        journal_status = "not recorded"
    else:
        journal_status = "nothing to record"
    rows.append(("journal", journal_status))

    if result.portfolio is not None:
        state = result.portfolio
        held = state.open_position
        position = (
            "none"
            if held is None
            else (
                f"{held.direction.value} {held.quantity:g} @ {held.entry_price:.2f} "
                f"(stop {held.stop_loss:.2f}, target {held.take_profit:.2f})"
            )
        )
        rows.extend(
            [
                ("cash", f"{state.cash:.2f}"),
                ("equity", f"{state.equity:.2f}"),
                ("open position", position),
            ]
        )

    width = max(len(label) for label, _ in rows)
    lines = [f"{symbol} position resolution"]
    lines.extend(f"  {label.ljust(width)}  {value}" for label, value in rows)
    return "\n".join(lines)


def _resolvable_symbol(symbol_argument: str) -> str | None:
    """The known symbol `symbol_argument` names, or `None` after complaining.

    Shared by `resolve` and `monitor` so both refuse an unknown market the same
    way, and so a monitor never enters its loop over a symbol ORION cannot
    fetch -- a preflight failure that no amount of waiting would fix.
    """
    intent = parse_intent(f"analyze {symbol_argument}")
    if intent.kind is not IntentKind.ANALYZE or intent.symbol is None:
        print(UNKNOWN_SYMBOL_MESSAGE, file=sys.stderr)
        print(f"ORION can analyse: {_known_symbols_text()}", file=sys.stderr)
        return None
    return intent.symbol


def _one_iteration(
    symbol: str,
    *,
    market_data_factory: MarketDataFactory,
    paper_resources: PaperResources,
) -> IterationResult:
    """Acquire everything one monitoring pass needs, run it, and release.

    EVERY COLLABORATOR IS BUILT HERE AND DIES HERE. The engine, the store, the
    journal and the vendor client are created for this pass and disposed when
    it returns, so no account state, revision, connection or HTTP client can
    survive an iteration boundary. That is what makes the supervisor's loop
    stateless in fact rather than by intention, and it is why a crash costs at
    most the pass that was in flight.

    SETTINGS ARE RE-READ EVERY PASS, NEVER CAPTURED.
    `orion.risk.policy.RiskPolicy.from_settings` documents that a fresh,
    uncached `get_settings()` per cycle is what gives an operator kill-switch
    response with no restart. A monitor that read `.env` once at startup would
    be the first thing in ORION requiring a restart to respect it.

    The market-data factory is passed *through* rather than called: a pass over
    a flat account must not construct an HTTP client, let alone make a request.
    """
    settings = get_settings()
    with paper_resources(_account_db_path()) as (store, journal):
        return resolve_once(
            symbol=symbol,
            timeframe=DEFAULT_TIMEFRAME,
            store=store,
            journal=journal,
            market_data=market_data_factory,
            policy=PaperExecutionPolicy.from_settings(settings),
            max_staleness_bars=RiskPolicy.from_settings(settings).max_staleness_bars,
            now=datetime.now(UTC),
        )


def _report_iteration(symbol: str, result: IterationResult) -> bool:
    """Print what one pass did, and answer whether it made progress.

    The return value is what the supervisor counts: `True` for a pass that
    established something -- a close, a hold, or an account with nothing open --
    and `False` for one that could not. A veto-like refusal does not exist on
    this path; there is no proposal to refuse.
    """
    if result.status is IterationStatus.NO_POSITION:
        print(NO_OPEN_POSITION_MESSAGE)
        return True
    if result.status is IterationStatus.ACCOUNT_UNREADABLE:
        print(ACCOUNT_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return False
    if result.status is IterationStatus.NOT_CONFIGURED:
        print(NOT_CONFIGURED_FOR_PRICES_MESSAGE, file=sys.stderr)
        return False
    if result.status is IterationStatus.PRICE_UNAVAILABLE:
        print(PRICE_UNAVAILABLE_MESSAGE, file=sys.stderr)
        return False

    assert result.cycle is not None and result.price is not None  # RESOLVED, by construction
    print(render_resolution(symbol, result.price, result.cycle))
    return result.cycle.outcome not in _CYCLE_FAILURES


def _iteration_exit_code(result: IterationResult) -> int:
    """The one-shot exit code for a pass. Unchanged from Phase 2, verb by verb."""
    if result.status is IterationStatus.ACCOUNT_UNREADABLE:
        return EXIT_ACCOUNT_ERROR
    if result.status is IterationStatus.NOT_CONFIGURED:
        return EXIT_NOT_CONFIGURED
    if result.status is IterationStatus.PRICE_UNAVAILABLE:
        return EXIT_PRICE_UNAVAILABLE
    if result.cycle is not None and result.cycle.outcome in _CYCLE_FAILURES:
        return EXIT_CYCLE_FAILED
    return EXIT_OK


def _run_resolve(
    symbol_argument: str,
    *,
    market_data_factory: MarketDataFactory,
    paper_resources: PaperResources,
) -> int:
    """Resolve the open position against every bar since it opened. No analysis.

    THE WHOLE POINT IS WHAT IS ABSENT. No `AnalysisService` is built, no
    `build_evidence` is called and no reasoning provider is constructed, so this
    path makes at most one vendor request and needs exactly one credential. A
    deployment with no `ANTHROPIC_API_KEY` resolves stops normally, which was
    not true of `orion paper`.

    THE ACCOUNT IS READ BEFORE THE VENDOR IS ASKED (Mission 9 Phase 2), for two
    reasons. The window to request is derived from the open position's
    `opened_at`, so it cannot be known earlier; and an account with nothing open
    now costs zero vendor requests instead of one, which matters for a command
    meant to be run often against a rate-limited free tier.

    THE WINDOW IS DERIVED, THEN CAPPED. One bar per elapsed interval since the
    entry, plus a small margin so at least one bar sits at or before it — that
    margin is what lets `window_coverage` prove the window reached back rather
    than assume it. `MAX_RESOLUTION_BARS` bounds it so a position left open for
    a year cannot turn one command into an unbounded request; a window that hits
    the cap is reported `TRUNCATED`, never silently treated as complete.

    VALIDATION REFUSES ON EVERYTHING EXCEPT A GAP, AND THAT ASYMMETRY IS
    DELIBERATE. Empty, stale, out-of-order and duplicated data all mean the
    answer cannot be trusted at all, so this refuses rather than resolving. A
    *gap* means something narrower and survivable: some bars are missing, so
    coverage is incomplete — which `ResolutionCoverage.GAPPED` says out loud.
    Refusing on a gap instead would leave a real stop unhonoured every time the
    window spans a market weekend, which is exactly the failure this command
    exists to remove. See `orion.paper.session.window_coverage`.
    """
    symbol = _resolvable_symbol(symbol_argument)
    if symbol is None:
        return EXIT_USAGE
    if not _account_exists(_account_db_path()):
        print(NO_ACCOUNT_MESSAGE, file=sys.stderr)
        return EXIT_NO_ACCOUNT

    try:
        result = _one_iteration(
            symbol, market_data_factory=market_data_factory, paper_resources=paper_resources
        )
    except Exception:
        print(CYCLE_FAILED_MESSAGE, file=sys.stderr)
        return EXIT_CYCLE_FAILED

    _report_iteration(symbol, result)
    return _iteration_exit_code(result)


def _run_monitor(
    symbol_argument: str,
    interval_seconds: float,
    *,
    market_data_factory: MarketDataFactory,
    paper_resources: PaperResources,
    max_iterations: int | None = None,
    sleeper: Sleeper | None = None,
) -> int:
    """Resolve the open position every `interval_seconds` until asked to stop.

    THE LOOP IS HERE BECAUSE SOMEBODY TYPED THIS VERB. Nothing in ORION starts
    it, schedules it or daemonises it; this is a foreground process that runs
    until a signal arrives, and every other command still does one thing once.

    PREFLIGHT RUNS BEFORE THE LOOP, DELIBERATELY. An unknown symbol, a missing
    account and a missing market-data credential cannot heal by waiting.
    Discovering one of them on the first pass and then sleeping an interval
    would be the worst possible behaviour, so they are checked up front and the
    command exits with the same codes `orion resolve` returns -- before any
    vendor request and before any sleep.

    EACH PASS IS INDEPENDENT AND STATELESS. `_one_iteration` builds and
    disposes every collaborator, so the loop below holds nothing but a counter.
    A pass that fails is not retried: the next one starts fresh from the store,
    which is a stronger position than a retry, because by then the account may
    legitimately have been changed by something else.

    `max_iterations` and `sleeper` are test seams and nothing else -- neither is
    reachable from the command line, so no invocation can bound the loop or
    change how it waits.
    """
    symbol = _resolvable_symbol(symbol_argument)
    if symbol is None:
        return EXIT_USAGE
    if not _account_exists(_account_db_path()):
        print(NO_ACCOUNT_MESSAGE, file=sys.stderr)
        return EXIT_NO_ACCOUNT
    if market_data_factory() is None:
        # Checked once, up front. A deployment with no market-data credential
        # cannot resolve anything, and a loop that discovered that once per
        # interval forever would be noise, not supervision.
        print(NOT_CONFIGURED_FOR_PRICES_MESSAGE, file=sys.stderr)
        return EXIT_NOT_CONFIGURED

    shutdown = ShutdownSignal()
    if sleeper is None:
        # Only installed for a real run. A test supplying its own sleeper is
        # driving the clock itself and must not have process-wide signal
        # handlers replaced underneath it.
        install_shutdown_handlers(shutdown)

    def iteration() -> bool:
        return _report_iteration(
            symbol,
            _one_iteration(
                symbol,
                market_data_factory=market_data_factory,
                paper_resources=paper_resources,
            ),
        )

    try:
        stop = run_monitor(
            iteration=iteration,
            interval_seconds=interval_seconds,
            shutdown=shutdown,
            max_iterations=max_iterations,
            sleeper=sleeper,
        )
    except Exception:
        # An unexpected exception, never an operational failure -- those are
        # already a `False` from `iteration`. The loop is over; the message is
        # fixed, like every other failure this module reports.
        print(MONITOR_FAILED_MESSAGE, file=sys.stderr)
        return EXIT_CYCLE_FAILED

    if stop is MonitorStop.FAILURE_CEILING:
        print(MONITOR_CEILING_MESSAGE, file=sys.stderr)
        return EXIT_CYCLE_FAILED
    print(MONITOR_STOPPED_MESSAGE)
    return EXIT_OK


def _run_paper(
    symbol_argument: str,
    *,
    account_service_factory: AccountServiceFactory,
    paper_resources: PaperResources,
) -> int:
    """Run one forward paper cycle. Never raises, never places a real order."""
    intent = parse_intent(f"analyze {symbol_argument}")
    if intent.kind is not IntentKind.ANALYZE:
        print(UNKNOWN_SYMBOL_MESSAGE, file=sys.stderr)
        print(f"ORION can analyse: {_known_symbols_text()}", file=sys.stderr)
        return EXIT_USAGE

    db_path = _account_db_path()
    if not _account_exists(db_path):
        # An account is required and is never created here: opening one is a
        # deliberate act with a balance the user chooses.
        print(NO_ACCOUNT_MESSAGE, file=sys.stderr)
        return EXIT_NO_ACCOUNT

    settings = get_settings()
    now = datetime.now(UTC)

    try:
        with paper_resources(db_path) as (store, journal):
            service = account_service_factory(store)
            if service is None:
                print(NOT_CONFIGURED_MESSAGE, file=sys.stderr)
                return EXIT_NOT_CONFIGURED

            try:
                report = service.run(intent, now=now)
            except Exception:
                # Message-free for the same reason the analysis path is: the
                # stages underneath name vendors, URLs and raw responses.
                print(ANALYSIS_FAILED_MESSAGE, file=sys.stderr)
                return EXIT_ANALYSIS_FAILED

            # The orchestrator's report is unpacked *here*, in the composition
            # root: `orion.paper` may not import `orion.orchestrator`, so the
            # session takes the domain objects rather than the report.
            result = run_cycle(
                decision=report.decision,
                evidence=report.evidence,
                reasoning=report.reasoning,
                # Advisory evidence, journaled with the chain and never read by
                # the cycle. `None` when neither specialist had anything to say.
                thesis=report.thesis.thesis,
                verdict=report.risk.verdict,
                store=store,
                journal=journal,
                policy=PaperExecutionPolicy.from_settings(settings),
                risk_per_trade_fraction=RiskPolicy.from_settings(
                    settings
                ).max_risk_per_trade_fraction,
                now=now,
            )
            rendering = render_cycle(intent.symbol or symbol_argument, report, result)
    except Exception:
        # The resource manager itself failed to open or close. Nothing ran.
        print(CYCLE_FAILED_MESSAGE, file=sys.stderr)
        return EXIT_CYCLE_FAILED

    print(rendering)
    return EXIT_CYCLE_FAILED if result.outcome in _CYCLE_FAILURES else EXIT_OK


def _run_reflect(
    symbol_argument: str,
    limit: int,
    *,
    reflection_resources: ReflectionResources,
) -> int:
    """Read the journal and print what happened against what was predicted.

    Never raises, never writes, never creates a database, and never runs an
    analysis. A journal with nothing closed in it yet is an ordinary answer, not
    a failure: it prints a batch of zero and exits 0.
    """
    intent = parse_intent(f"analyze {symbol_argument}")
    if intent.kind is not IntentKind.ANALYZE:
        print(UNKNOWN_SYMBOL_MESSAGE, file=sys.stderr)
        print(f"ORION can analyse: {_known_symbols_text()}", file=sys.stderr)
        return EXIT_USAGE
    symbol = intent.symbol
    assert symbol is not None  # guaranteed by IntentKind.ANALYZE

    db_path = _account_db_path()
    if not _account_exists(db_path):
        # Refused rather than answered with a journal this command created in
        # order to find it empty -- the same rule `account show` follows.
        print(NO_HISTORY_MESSAGE, file=sys.stderr)
        return EXIT_NO_ACCOUNT

    try:
        with reflection_resources(db_path) as journal:
            batch = reflect_on_recent_closed_positions(
                journal, symbol, limit=limit, now=datetime.now(UTC)
            )
    except Exception:
        # Deliberately broad and deliberately message-free, like every other
        # failure path here. `DecisionJournalError` names the database and its
        # path, `DecisionJournalIntegrityError` names a stored fingerprint, and
        # `ReflectionError` names one too. A corrupted record is reported as
        # "could not read" rather than as an empty history: a journal ORION
        # cannot verify must never be presented as a journal with nothing in it.
        print(REFLECTION_FAILED_MESSAGE, file=sys.stderr)
        return EXIT_REFLECTION_FAILED

    print(render_reflections(batch))
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
    paper_resources: PaperResources | None = None,
    reflection_resources: ReflectionResources | None = None,
    market_data_factory: MarketDataFactory | None = None,
    monitor_max_iterations: int | None = None,
) -> int:
    """The `orion` console entrypoint. Returns a process exit code.

    `argv` defaults to the real command line. The factories are test seams and
    nothing else: none is reachable from the command line, so no invocation can
    swap out how ORION is wired or where its account lives. `store_factory`,
    `paper_resources` and `reflection_resources` in particular cannot be used to
    redirect the database — each is handed the path `_account_db_path` resolved
    from `ORION_DB_PATH`, and never one built from an argument.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits directly — 0 for `--help`, 2 for a bad invocation.
        # Turning that back into a return keeps every path out of this function
        # uniform, and keeps the message argparse already wrote to stderr.
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    if args.command == "reflect":
        return _run_reflect(
            str(args.symbol),
            int(args.limit),
            reflection_resources=reflection_resources or _default_reflection_resources,
        )

    if args.command == "paper":
        return _run_paper(
            str(args.symbol),
            account_service_factory=account_service_factory or _account_service_factory,
            paper_resources=paper_resources or _default_paper_resources,
        )

    if args.command == "resolve":
        return _run_resolve(
            str(args.symbol),
            market_data_factory=market_data_factory or _default_market_data_factory,
            paper_resources=paper_resources or _default_paper_resources,
        )

    if args.command == "monitor":
        return _run_monitor(
            str(args.symbol),
            float(args.interval),
            market_data_factory=market_data_factory or _default_market_data_factory,
            paper_resources=paper_resources or _default_paper_resources,
            max_iterations=monitor_max_iterations,
        )

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
