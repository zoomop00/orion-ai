"""Static proof that `orion.backtest` has no network, broker, AI-vendor, or
persistence dependency, and cannot import `orion.analysts` -- not "we didn't
call it," but "the import doesn't exist."

Mirrors `tests/risk/test_import_hygiene.py`/`tests/paper/test_import_
hygiene.py`/`tests/memory/test_import_hygiene.py`/`tests/research/test_
import_hygiene.py`'s identical AST-based approach: parsing each source
file's own `import`/`from ... import` statements, not inspecting
`sys.modules` at runtime, so this is a property of the package's source, not
of whatever else the rest of the test suite happened to already import.

`orion.backtest`'s own module docstrings already assert "No Mission 2
persistence, no Mission 5 changes: this module never imports orion.memory or
orion.reflection" (`orion.backtest.engine`) and M6-Q1/M6-Q4 (approved,
`docs/DECISIONS.md`) commit to no network fetch and zero persistence. This
test is what keeps those claims mechanically checked rather than merely
asserted in a docstring -- the gap this codebase had before this entry (no
`tests/backtest/test_import_hygiene.py` existed at all) was flagged during
the Mission 8 investigation and is closed here as independent architectural
hardening, unrelated to Mission 8's own scope. `orion.analysts` is added to
the disallowed set for the same reason Mission 8 must never be importable by
any Mission 0-7 package: a live LLM call must never enter the deterministic,
reproducible backtest replay loop (see docs/DECISIONS.md's Mission 6 entry
on determinism, and the Mission 8 entry on why Macro Analyst is never wired
into orion.backtest).
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.backtest

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
    # AI / market-data / broker providers -- orion.backtest never talks to a
    # live provider directly, only to already-fetched historical bars and
    # already-validated in-process objects.
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    # persistence -- orion.backtest returns BacktestResult in memory only
    # (M6-Q4); a caller who wants Mission 2/5 integration journals the
    # result's contents into a fresh, disposable journal themselves.
    "sqlalchemy",
}

DISALLOWED_ORION_SUBPACKAGES = {
    "orion.memory",
    "orion.reflection",
    "orion.analysts",
}


def _backtest_package_dir() -> Path:
    path = orion.backtest.__file__
    assert path is not None
    return Path(path).parent


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


def _backtest_source_files() -> list[Path]:
    return sorted(_backtest_package_dir().rglob("*.py"))


def test_orion_backtest_package_has_source_files_to_scan() -> None:
    # Guards against this test silently passing over zero files if the
    # package layout ever changes -- data, engine, execution, metrics,
    # schemas, __init__.
    assert len(_backtest_source_files()) >= 5


def test_orion_backtest_has_no_network_broker_or_persistence_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _backtest_source_files():
        found = {m.split(".")[0] for m in _imported_modules(source_path)} & (
            DISALLOWED_TOP_LEVEL_MODULES
        )
        if found:
            offenders[str(source_path.relative_to(_backtest_package_dir().parent.parent))] = found
    assert not offenders, f"orion.backtest imports disallowed modules: {offenders}"


def test_orion_backtest_has_no_memory_reflection_or_analysts_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _backtest_source_files():
        imported = _imported_modules(source_path)
        found = {
            m
            for m in imported
            if any(m == pkg or m.startswith(pkg + ".") for pkg in DISALLOWED_ORION_SUBPACKAGES)
        }
        if found:
            offenders[str(source_path.relative_to(_backtest_package_dir().parent.parent))] = found
    assert not offenders, f"orion.backtest imports a disallowed sibling package: {offenders}"
