"""ORION's command line: one verb, over the orchestrator that already exists.

`pyproject.toml` has advertised `orion = "orion.cli:main"` since Mission 0 and
this module was never written, so the installed `orion` command has always
failed with `ModuleNotFoundError` before printing anything. This file is that
entrypoint, and nothing more.

ONE VERB, AND ONLY THE ONE THE README ALREADY PROMISED. `orion analyze XAUUSD`
runs exactly the pipeline Mission 10 Phase 5.1 assembled — market data,
validation, deterministic analysis, AI reasoning, the decision engine, the Risk
Guardian — through `orion.orchestrator.wiring.build_analysis_service` and
`AnalysisService.run`. It adds no stage, reorders none and skips none. There is
deliberately no `trade` or `execute` verb: nothing in this codebase can place an
order, and a command that looked like it could would be a lie with a shell
prompt in front of it. There is no `serve` verb either, because `run()` is
already reachable by the documented one-liner and a second spelling of it is a
second thing to keep honest.

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

NO PERSISTENCE, NO LOGGING, NO NETWORK OF ITS OWN. No portfolio store is
constructed, so `orion.memory.engine.create_memory_engine` — which creates a
database file and its tables as a side effect — is never reached, and no `.db`
appears because somebody ran the CLI. The risk panel reports
`no_portfolio_store` honestly instead, exactly as it does over HTTP. This module
imports no logging framework, opens no file and makes no request itself: the
only network traffic is the providers' own, inside `AnalysisService.run`.

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
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from orion.config import get_settings
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.intent import DEFAULT_TIMEFRAME, known_symbols, parse_intent
from orion.orchestrator.schemas import AnalysisReport, IntentKind, RiskAvailability
from orion.orchestrator.wiring import build_analysis_service

#: Exit codes. 0 and 2 are the conventional success/usage pair (2 is argparse's
#: own, so a bad invocation exits the same way whoever rejected it). 3 and 4 are
#: separated on purpose: only the first is fixed by editing `.env`.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_CONFIGURED = 3
EXIT_ANALYSIS_FAILED = 4

#: Fixed sentences. Nothing in this file is ever built from an exception, a
#: vendor response, or the argument the user supplied.
UNKNOWN_SYMBOL_MESSAGE = "ORION cannot analyse that symbol."
NOT_CONFIGURED_MESSAGE = (
    "ORION is not configured to analyse. Set ANTHROPIC_API_KEY and TWELVE_DATA_API_KEY in .env."
)
ANALYSIS_FAILED_MESSAGE = "ORION could not complete the analysis."

#: How `main` obtains an `AnalysisService`. `None` means "this deployment
#: cannot analyse", which `build_analysis_service` already returns for a
#: missing credential and which is a refusal here rather than a crash.
ServiceFactory = Callable[[], AnalysisService | None]


def _default_service_factory() -> AnalysisService | None:
    """Build one `AnalysisService` from the environment, or `None`.

    Settings are read per invocation rather than captured at import, matching
    `orion.interface.app._default_analysis_factory` and for the same stated
    reason: `RiskPolicy.from_settings` requires a fresh, uncached
    `get_settings()` so engaging the kill switch never needs a restart.
    """
    return build_analysis_service(get_settings())


def _known_symbols_text() -> str:
    """Every symbol this build can analyse, from the parser's own table."""
    return ", ".join(sorted(known_symbols()))


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


def _run_analysis(symbol: str, *, service_factory: ServiceFactory) -> int:
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
) -> int:
    """The `orion` console entrypoint. Returns a process exit code.

    `argv` defaults to the real command line. `service_factory` is a test seam
    and nothing else: it is not reachable from the command line, so no
    invocation can swap out how ORION is wired.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits directly — 0 for `--help`, 2 for a bad invocation.
        # Turning that back into a return keeps every path out of this function
        # uniform, and keeps the message argparse already wrote to stderr.
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    # One verb today, and `add_subparsers(required=True)` guarantees it was
    # given; a second verb would branch here.
    return _run_analysis(
        str(args.symbol),
        service_factory=service_factory or _default_service_factory,
    )
