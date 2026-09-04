"""Static proof about `orion.data.sessions`, and about how the gap tolerance
it exists to protect is used across `src/`.

Two separate guarantees, both AST-based on the package's own source rather
than on runtime `sys.modules` inspection -- mirroring `tests/risk/test_import_
hygiene.py` and its siblings.

1. `orion.data.sessions` is a leaf. It declares recurring market closures and
   nothing else, so it must stay importable from anywhere in ORION without
   dragging in a network client, a vendor SDK, a database, or any other
   `orion` package. `orion.data.validation` imports it, and `orion.data.
   validation` is imported by the analysis, orchestrator and backtest layers
   alike; a dependency acquired here would propagate to all of them.

2. No caller anywhere in `src/` raises `gap_tolerance_bars`. That is the
   entire point of the session mechanism: a scheduled closure is accepted by
   declaring the closure, never by widening the tolerance, which would blind
   the GAP check to genuinely missing bars at every timeframe. A future edit
   that "fixes" a gap finding by passing a bigger tolerance instead of a
   session is exactly what this test exists to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.data

DISALLOWED_TOP_LEVEL_MODULES = {
    # network / HTTP
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "urllib3",
    "aiohttp",
    "websockets",
    "socket",
    "ftplib",
    "smtplib",
    "asyncio",
    # AI / market-data / broker providers
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    # persistence
    "sqlalchemy",
    "sqlite3",
    # heavyweight analysis stack -- a weekly calendar needs none of it
    "pandas",
    "numpy",
    "pandas_ta_classic",
}


def _src_dir() -> Path:
    path = orion.data.__file__
    assert path is not None
    return Path(path).parent.parent


def _sessions_source() -> Path:
    return _src_dir() / "data" / "sessions.py"


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


def test_sessions_module_exists_to_scan() -> None:
    # Guards against this whole file silently passing over a renamed module.
    assert _sessions_source().is_file()


def test_sessions_has_no_network_vendor_or_persistence_imports() -> None:
    found = {m.split(".")[0] for m in _imported_modules(_sessions_source())}
    offenders = found & DISALLOWED_TOP_LEVEL_MODULES
    assert not offenders, f"orion.data.sessions imports disallowed modules: {offenders}"


def test_sessions_imports_no_orion_package_at_all() -> None:
    # A leaf in the strongest sense: stdlib and pydantic only. `orion.data.
    # validation` may import this module; this module may import nothing of
    # ORION's, so that edge can never become a cycle.
    offenders = {m for m in _imported_modules(_sessions_source()) if m.split(".")[0] == "orion"}
    assert not offenders, f"orion.data.sessions imports an orion package: {offenders}"


def _validate_snapshot_calls() -> list[tuple[Path, ast.Call]]:
    calls: list[tuple[Path, ast.Call]] = []
    for source_path in sorted(_src_dir().rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "validate_snapshot":
                calls.append((source_path, node))
    return calls


def test_there_are_validate_snapshot_call_sites_to_check() -> None:
    # The orchestrator's live path and the backtest engine's up-front check.
    assert len(_validate_snapshot_calls()) >= 2


def test_no_caller_in_src_raises_the_gap_tolerance() -> None:
    offenders = [
        str(source_path.relative_to(_src_dir()))
        for source_path, call in _validate_snapshot_calls()
        if any(keyword.arg == "gap_tolerance_bars" for keyword in call.keywords)
    ]
    assert not offenders, (
        "a caller passes gap_tolerance_bars to validate_snapshot: "
        f"{offenders}. A scheduled market closure is accepted by declaring a "
        "TradingSession, never by widening the tolerance -- see orion.data.sessions."
    )


def test_the_gap_tolerance_default_is_unchanged() -> None:
    # Pinned rather than assumed: the session mechanism is only meaningful
    # while this stays strict.
    import inspect

    from orion.data.validation import validate_snapshot

    assert inspect.signature(validate_snapshot).parameters["gap_tolerance_bars"].default == 1.5


# ---------------------------------------------------------------- historical
#
# `orion.data.historical` is the vendor half of Mission 6 Phase 3. It is
# *allowed* an HTTP client -- acquiring history is a network call and pretending
# otherwise would be theatre -- so its wall is drawn elsewhere: it must not
# reach the replay side, the derived-data side, a database, or a model. The
# replay side's own hygiene test forbids the reverse direction, and between the
# two the CSV file stays the only place acquisition and replay meet.

HISTORICAL_DISALLOWED_TOP_LEVEL = {
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    "sqlalchemy",
    "sqlite3",
    "random",
    "pandas",
    "numpy",
    "pandas_ta_classic",
}

HISTORICAL_DISALLOWED_ORION = {
    "orion.backtest",
    "orion.datasets",
    "orion.analysis",
    "orion.strategy",
    "orion.experiments",
    "orion.analysts",
    "orion.reasoning",
    "orion.decision",
    "orion.risk",
    "orion.paper",
    "orion.monitor",
    "orion.memory",
    "orion.reflection",
    "orion.orchestrator",
    "orion.interface",
    "orion.config",
}


def _historical_source() -> Path:
    return _src_dir() / "data" / "historical.py"


def test_historical_module_exists_to_scan() -> None:
    assert _historical_source().is_file()


def test_historical_has_no_vendor_sdk_persistence_or_analysis_imports() -> None:
    found = {m.split(".")[0] for m in _imported_modules(_historical_source())}
    offenders = found & HISTORICAL_DISALLOWED_TOP_LEVEL
    assert not offenders, f"orion.data.historical imports disallowed modules: {offenders}"


def test_historical_never_reaches_the_replay_side() -> None:
    imported = _imported_modules(_historical_source())
    offenders = {
        m
        for m in imported
        if any(m == pkg or m.startswith(pkg + ".") for pkg in HISTORICAL_DISALLOWED_ORION)
    }
    assert not offenders, f"orion.data.historical imports a disallowed package: {offenders}"


def test_historical_does_not_extend_the_live_fetch_contract() -> None:
    # It may *import* MarketDataProviderError -- the failure is identical in
    # kind, "a provider could not return real market data" -- but nothing here
    # may subclass MarketDataProvider or define `fetch_ohlcv`, whose three live
    # consumers depend on it meaning exactly what it means today. Checked by
    # introspection rather than substring matching, so the class named
    # `HistoricalMarketDataProvider` cannot trip it.
    from orion.data.historical import HistoricalMarketDataProvider, TwelveDataHistoricalProvider
    from orion.data.provider_base import MarketDataProvider

    for cls in (HistoricalMarketDataProvider, TwelveDataHistoricalProvider):
        assert MarketDataProvider not in cls.__mro__
        assert not hasattr(cls, "fetch_ohlcv")


def test_only_the_acquisition_side_imports_historical() -> None:
    # `orion.datasets` imports the ABC, which is the point of it. Nothing on
    # the live or replay side may: Phase 3 adds a sibling beside the live
    # provider and wires it into no existing path, so it cannot have changed
    # any existing behaviour.
    src = _src_dir()
    importers = [
        str(path.relative_to(src))
        for path in sorted(src.rglob("*.py"))
        if path != _historical_source()
        and any(m.startswith("orion.data.historical") for m in _imported_modules(path))
    ]
    unexpected = [p for p in importers if not p.replace("\\", "/").startswith("datasets/")]
    assert not unexpected, f"orion.data.historical is imported outside orion.datasets: {unexpected}"
