"""Static proof that measurement reads the analysis layer and never the other
way round.

THE EDGE THAT WOULD CYCLE. `orion.calibration` imports `orion.analysis` to
reuse the shipped ATR, and `orion.analysis` sits below it in the graph — so an
import back would be a genuine cycle, and it is also the shape a well-meaning
future edit would take ("just have `classify_volatility` read the study"). Both
directions are asserted here: what this package may reach, and that
`orion.analysis` does not reach it.

THE OTHER WALL. A study must not be able to open a position, ask a model, or
reach the replay loop. `orion.backtest`, `orion.paper`, `orion.monitor`,
`orion.reasoning` and the vendor SDKs are all unreachable from here, and
`orion.datasets` is the *only* sibling package this one may read — measurement
consumes a verified dataset and nothing else.

AST, NOT `sys.modules` -- this reads what each file actually says, so a lazily
imported module inside a function cannot slip past it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.calibration

DISALLOWED_TOP_LEVEL_MODULES = {
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
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    "sqlalchemy",
    "sqlite3",
    # A measurement is deterministic or it is not a measurement. `random` is
    # reserved for a future robustness package that will own it explicitly.
    "random",
}

DISALLOWED_ORION_SUBPACKAGES = {
    "orion.backtest",
    "orion.paper",
    "orion.monitor",
    "orion.reasoning",
    "orion.decision",
    "orion.risk",
    "orion.analysts",
    "orion.memory",
    "orion.reflection",
    "orion.orchestrator",
    "orion.interface",
    "orion.thesis",
    "orion.research",
    "orion.config",
}

#: The whole of this package's ORION surface. Stated positively as well as
#: negatively, so an unexpected new edge fails even if nobody thought to add it
#: to the list above.
ALLOWED_ORION_ROOTS = {
    "orion.analysis",
    "orion.data",
    "orion.datasets",
    "orion.provenance",
    "orion.calibration",
}


def _package_dir() -> Path:
    path = orion.calibration.__file__
    assert path is not None
    return Path(path).parent


def _src_dir() -> Path:
    return _package_dir().parent.parent


def _source_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


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


def test_there_are_source_files_to_scan() -> None:
    # __init__, atr_series, volatility.
    assert len(_source_files(_package_dir())) >= 3


def test_calibration_has_no_network_vendor_or_persistence_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files(_package_dir()):
        found = {m.split(".")[0] for m in _imported_modules(source_path)} & (
            DISALLOWED_TOP_LEVEL_MODULES
        )
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.calibration imports disallowed modules: {offenders}"


def test_calibration_never_reaches_execution_reasoning_or_replay() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files(_package_dir()):
        found = {
            m
            for m in _imported_modules(source_path)
            if any(m == pkg or m.startswith(pkg + ".") for pkg in DISALLOWED_ORION_SUBPACKAGES)
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.calibration imports a disallowed sibling package: {offenders}"


def test_calibration_only_reaches_analysis_data_datasets_and_provenance() -> None:
    for source_path in _source_files(_package_dir()):
        for module in _imported_modules(source_path):
            if not module.startswith("orion"):
                continue
            root = ".".join(module.split(".")[:2])
            assert root in ALLOWED_ORION_ROOTS, f"{source_path.name} imports unexpected {module!r}"


def test_analysis_never_imports_calibration() -> None:
    # The one edge that would cycle. `orion.analysis` is below this package and
    # must stay there: a threshold is chosen by editing `structure.py` against a
    # cited study, never by `structure.py` reading one at runtime.
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files(_src_dir() / "analysis"):
        found = {m for m in _imported_modules(source_path) if m.startswith("orion.calibration")}
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysis imports orion.calibration: {offenders}"


def test_calibration_does_not_modify_or_import_the_volatility_thresholds() -> None:
    # Phase 4 measures; it does not classify. `classify_volatility` and its cut
    # points are Phase 5's business, and a study that imported them could not
    # honestly be cited when they change.
    for source_path in _source_files(_package_dir()):
        imported = _imported_modules(source_path)
        assert "orion.analysis.structure" not in imported, (
            f"{source_path.name} imports orion.analysis.structure; a measurement must not "
            "reach the classification it exists to inform"
        )


def test_no_orion_package_imports_calibration_yet() -> None:
    # A leaf, exactly as `orion.datasets` and `orion.backtest` are leaves in
    # their own directions. Nothing in `src/` depends on it, so adding it
    # cannot have changed any existing behaviour.
    src = _src_dir()
    importers = [
        str(path.relative_to(src))
        for path in _source_files(src)
        if not path.is_relative_to(_package_dir())
        and any(m.startswith("orion.calibration") for m in _imported_modules(path))
    ]
    assert not importers, f"orion.calibration is imported by {importers}"
