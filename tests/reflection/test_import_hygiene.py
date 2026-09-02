"""Static proof that `orion.reflection` is read-only and reaches nothing live.

`orion.reflection`'s module docstring has claimed since Mission 5 that it is a
pure function with "no network, filesystem, randomness, or internal clock",
typed against `DecisionJournal` and "never a `PortfolioStore` in any form, so
there is no path through this function's own signature for it to touch current
account state". Every other engine-adjacent package —  `risk`, `paper`, `memory`,
`research`, `backtest`, `analysts`, `orchestrator` — already had an AST test
proving its equivalent claim. This package did not, and Mission 4 Phase 2c gives
it its first caller in `src/`, which is exactly when an unenforced claim starts
to matter.

Mirrors `tests/paper/test_import_hygiene.py` and `tests/backtest/test_import_
hygiene.py`: parses each source file's own AST rather than inspecting
`sys.modules`, so this is a property of the package's source, not of whatever
else a test run happened to import first.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.reflection

#: Reflection reads a journal it is handed. It opens nothing, calls nothing and
#: contacts nobody -- including the AI vendors, because a reflection that asked
#: a model to grade a trade would be exactly the quality verdict Mission 5's
#: architecture entry exists to refuse.
DISALLOWED_TOP_LEVEL_MODULES = {
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "urllib3",
    "aiohttp",
    "websockets",
    "socket",
    "anthropic",
    "sqlalchemy",
    "sqlite3",
    "logging",
    "random",
    "time",
    "subprocess",
    "shutil",
    "tempfile",
    "pathlib",
    "os",
}

#: The account-state wall, module by module. `orion.reflection` may read the
#: journal's abstract interface and the domain schemas its records are made of,
#: but there must be no import through which it could load, mark or save a
#: portfolio -- nor any through which it could run an analysis of its own.
DISALLOWED_ORION_MODULES = {
    "orion.paper.store",
    "orion.paper.session",
    "orion.paper.execution",
    "orion.memory.portfolio_store",
    "orion.memory.sqlite_journal",
    "orion.memory.engine",
    "orion.risk.guardian",
    "orion.decision.engine",
    "orion.reasoning.claude_provider",
    "orion.orchestrator",
    "orion.interface",
    "orion.analysts",
    "orion.backtest",
    "orion.data",
    "orion.research",
    "orion.config",
}


def _package_dir() -> Path:
    path = orion.reflection.__file__
    assert path is not None
    return Path(path).parent


def _source_files() -> list[Path]:
    return sorted(_package_dir().rglob("*.py"))


def _imports(source_path: Path) -> tuple[set[str], set[str]]:
    """(top-level module names, fully-qualified module names) imported."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    top: set[str] = set()
    full: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top.add(alias.name.split(".")[0])
                full.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            top.add(node.module.split(".")[0])
            full.add(node.module)
    return top, full


def test_the_package_has_source_files_to_scan() -> None:
    assert len(_source_files()) >= 3  # __init__, schemas, analysis


def test_reflection_has_no_network_persistence_or_clock_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _source_files():
        top, _ = _imports(path)
        found = top & DISALLOWED_TOP_LEVEL_MODULES
        if found:
            offenders[path.name] = found
    assert not offenders, f"orion.reflection imports disallowed modules: {offenders}"


def test_reflection_cannot_reach_account_state_or_run_an_analysis() -> None:
    """The docstring's own claim, enforced.

    `reflect_on_closed_position` takes a `DecisionJournal` and nothing else, and
    `DecisionJournal` declares no update or delete method — so history is
    unreachable for writing. This closes the other half: there is no import here
    through which reflection could reach a *live* account, an execution engine
    or a model, so it cannot become a second authority sitting downstream of
    the Risk Guardian with thresholds of its own.
    """
    offenders: dict[str, set[str]] = {}
    for path in _source_files():
        _, full = _imports(path)
        found = {
            module
            for module in full
            for banned in DISALLOWED_ORION_MODULES
            if module == banned or module.startswith(f"{banned}.")
        }
        if found:
            offenders[path.name] = found
    assert not offenders, f"orion.reflection reaches past its layer: {offenders}"


def test_reflection_never_calls_a_clock_itself() -> None:
    """`now` is a required, caller-supplied parameter everywhere in this
    package, matching `decide`/`review`/`execute`. A reflection computed from
    an internal clock would not be reproducible from the journal alone.

    Walks the AST rather than scanning for substrings: these modules' own
    docstrings say the words "datetime.now()" and "random" while explaining why
    they never call them, and a substring scan would flag the explanation.
    """
    banned = {"now", "utcnow", "today", "time", "monotonic", "uuid1", "uuid4"}
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned, (
                    f"{path.name} calls {node.func.attr}() -- `now` is caller-supplied here"
                )
            if isinstance(node, ast.Name):
                assert node.id != "random", f"{path.name} reaches for a random source"


def test_reflection_writes_nothing_anywhere() -> None:
    """No `record_*` call exists in this package, and no file is opened.

    `DecisionJournal` has six write methods; a reflection that called one would
    be appending to the history it is supposed to be reading.
    """
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else ""
                )
                assert not name.startswith("record_"), f"{path.name} writes to the journal"
                assert name not in {"open", "write_text", "write_bytes", "unlink"}, (
                    f"{path.name} touches the filesystem"
                )


def test_no_engine_package_imports_reflection() -> None:
    """The dependency stays one-way. Reflection is downstream of everything and
    upstream of nothing: an engine that read a `TradeReflection` would be a path
    by which one closed trade could change what the system does next, which
    Mission 5's architecture entry rules out explicitly.

    `orion.cli` is the exception, and the only one: it is a composition root,
    it constructs the journal and prints the result, and it decides nothing.
    """
    src = Path(orion.reflection.__file__ or "").parent.parent
    importers: set[str] = set()
    for path in sorted(src.rglob("*.py")):
        relative = path.relative_to(src).as_posix()
        if relative.startswith("reflection/"):
            continue
        _, full = _imports(path)
        if any(
            module == "orion.reflection" or module.startswith("orion.reflection.")
            for module in full
        ):
            importers.add(relative)
    assert importers == {"cli.py"}, f"unexpected consumers of orion.reflection: {importers}"
