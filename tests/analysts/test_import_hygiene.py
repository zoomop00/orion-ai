"""Static proof of `orion.analysts`'s isolation -- not "we didn't wire it
up," but "the import doesn't exist."

Two directions, both AST-parsed (never a `sys.modules` runtime check, which
would be contaminated by whatever else the test process happens to have
already imported):

1. Outbound: `orion.analysts` itself imports `anthropic` in exactly one file
   (`claude_macro_provider.py`), and never imports any downstream layer
   (`orion.analysis`, `orion.decision`, `orion.risk`, `orion.paper`,
   `orion.memory`, `orion.reflection`, `orion.reasoning`, `orion.backtest`)
   or a persistence library (`sqlalchemy`).
2. Inbound: no Mission 0-7 package imports `orion.analysts` -- Macro
   Analyst output reaches no decision path in this mission.

Also proves the "rationale/note is never consumed downstream" requirement
structurally: no file anywhere in `orion.analysts` calls `exec`, `eval`,
`compile`, or a subprocess/shell function -- the only thing that could turn
model-authored free text into executed behavior.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.analysts

DISALLOWED_TOP_LEVEL_MODULES = {
    # AI reasoning -- only claude_macro_provider.py may import this.
    "anthropic",
    # persistence -- Mission 8 v1 is explicitly zero-persistence.
    "sqlalchemy",
}

ANTHROPIC_ALLOWED_FILE = "claude_macro_provider.py"

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

DANGEROUS_CALL_NAMES = {"exec", "eval", "compile"}
DANGEROUS_MODULES = {"subprocess", "os"}

MISSION_0_7_PACKAGES = [
    "data",
    "analysis",
    "reasoning",
    "decision",
    "risk",
    "paper",
    "memory",
    "reflection",
    "backtest",
    "research",
]


def _analysts_package_dir() -> Path:
    path = orion.analysts.__file__
    assert path is not None
    return Path(path).parent


def _repo_src_dir() -> Path:
    return _analysts_package_dir().parent


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


def _analysts_source_files() -> list[Path]:
    return sorted(_analysts_package_dir().rglob("*.py"))


def test_orion_analysts_package_has_source_files_to_scan() -> None:
    assert len(_analysts_source_files()) >= 3


def test_anthropic_is_imported_only_by_claude_macro_provider() -> None:
    offenders: list[str] = []
    for source_path in _analysts_source_files():
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        if "anthropic" in modules and source_path.name != ANTHROPIC_ALLOWED_FILE:
            offenders.append(source_path.name)
    assert not offenders, f"anthropic imported outside {ANTHROPIC_ALLOWED_FILE}: {offenders}"


def test_orion_analysts_has_no_disallowed_top_level_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _analysts_source_files():
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        found = modules & DISALLOWED_TOP_LEVEL_MODULES
        # anthropic is separately governed (single-file allowlist) above.
        found -= {"anthropic"}
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysts imports disallowed modules: {offenders}"


def test_orion_analysts_has_no_downstream_layer_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _analysts_source_files():
        imported = _imported_modules(source_path)
        found = {
            m
            for m in imported
            if any(m == pkg or m.startswith(pkg + ".") for pkg in DISALLOWED_ORION_SUBPACKAGES)
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysts imports downstream layers: {offenders}"


def test_orion_analysts_has_no_exec_eval_or_subprocess_calls() -> None:
    """The only way model-authored free text (rationale/note) could become
    executed behavior is if something in this package fed it to exec/eval/
    a subprocess. Proven absent, not merely believed absent."""
    offenders: dict[str, list[str]] = {}
    for source_path in _analysts_source_files():
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        found: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in DANGEROUS_CALL_NAMES
            ):
                found.append(node.func.id)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for n in names:
                    if n.split(".")[0] in DANGEROUS_MODULES:
                        found.append(n)
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysts contains dangerous calls/imports: {offenders}"


def test_no_mission_0_7_package_imports_orion_analysts() -> None:
    offenders: dict[str, set[str]] = {}
    for pkg_name in MISSION_0_7_PACKAGES:
        pkg_dir = _repo_src_dir() / pkg_name
        if not pkg_dir.is_dir():
            continue
        for source_path in sorted(pkg_dir.rglob("*.py")):
            imported = _imported_modules(source_path)
            found = {
                m for m in imported if m == "orion.analysts" or m.startswith("orion.analysts.")
            }
            if found:
                offenders[str(source_path.relative_to(_repo_src_dir().parent))] = found
    assert not offenders, f"a Mission 0-7 package imports orion.analysts: {offenders}"
