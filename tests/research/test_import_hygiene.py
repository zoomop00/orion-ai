"""Static proof that `orion.research` stays a standalone data layer.

Mirrors `tests/risk/test_import_hygiene.py`'s AST-based approach exactly:
parsing each source file's own `import`/`from ... import` statements, not
inspecting `sys.modules` at runtime, so this is a property of the package's
source, not of whatever else the rest of the test suite happened to already
import.

Unlike `orion.risk`/`orion.paper`, `orion.research` is a *live* data
provider layer (mirroring `orion.data`), so `httpx` is expected and
allowed. What must never appear: persistence (Mission 7 v1 is explicitly
zero-persistence), the AI reasoning SDK, or any downstream layer
(analysis/reasoning/decision/risk/paper/memory/reflection/backtest) --
Mission 7 v1 is not wired into any of them yet, per docs/DECISIONS.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.research

DISALLOWED_TOP_LEVEL_MODULES = {
    # AI reasoning -- orion.research never talks to Claude.
    "anthropic",
    # persistence -- Mission 7 v1 is explicitly zero-persistence.
    "sqlalchemy",
}

DISALLOWED_ORION_SUBPACKAGES = {
    "orion.analysis",
    "orion.reasoning",
    "orion.decision",
    "orion.risk",
    "orion.paper",
    "orion.memory",
    "orion.reflection",
    "orion.backtest",
}


def _research_package_dir() -> Path:
    path = orion.research.__file__
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


def _research_source_files() -> list[Path]:
    return sorted(_research_package_dir().rglob("*.py"))


def test_orion_research_package_has_source_files_to_scan() -> None:
    assert len(_research_source_files()) >= 4


def test_orion_research_has_no_disallowed_top_level_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _research_source_files():
        found = {m.split(".")[0] for m in _imported_modules(source_path)} & (
            DISALLOWED_TOP_LEVEL_MODULES
        )
        if found:
            offenders[str(source_path.relative_to(_research_package_dir().parent.parent))] = found
    assert not offenders, f"orion.research imports disallowed modules: {offenders}"


def test_orion_research_has_no_downstream_layer_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _research_source_files():
        found = {
            m
            for imported in _imported_modules(source_path)
            for m in [imported]
            if any(
                imported == pkg or imported.startswith(pkg + ".")
                for pkg in DISALLOWED_ORION_SUBPACKAGES
            )
        }
        if found:
            offenders[str(source_path.relative_to(_research_package_dir().parent.parent))] = found
    assert not offenders, f"orion.research imports downstream layers: {offenders}"
