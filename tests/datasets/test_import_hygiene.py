"""Static proof that `orion.datasets` sits on the acquisition side of the file
boundary, and never reaches across it.

THE WALL THIS ASSERTS. A dataset is written by one half of ORION and replayed
by another, and the two meet at a CSV on disk rather than in a process. That is
what keeps `orion.backtest` free of vendors, credentials and HTTP clients --
its own hygiene test forbids them -- and what makes a replay reproducible: it
reads a fixed artifact, not whatever a live endpoint answers today. If
`orion.datasets` could import `orion.backtest`, or vice versa, that boundary
would exist only as an intention.

`orion.analysis` is forbidden for a related reason: acquisition must not be
able to compute an indicator, a structure feature or an Evidence. A dataset is
bars and their identity, nothing derived. The strategy and experiment packages
are named here before they exist, so the wall is already standing when they
arrive.

AST, NOT `sys.modules` -- this reads what each file actually says, so a lazily
imported module inside a function cannot slip past it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.datasets

#: `orion.datasets` may talk to a vendor only through the ABC it is handed.
#: It constructs no client, so it needs no HTTP library, no database and no
#: model SDK of its own.
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
    "random",
}

#: The replay side, the derived-data side, and the packages that will sit above
#: this one. None of them may be reached from acquisition.
DISALLOWED_ORION_SUBPACKAGES = {
    "orion.backtest",
    "orion.analysis",
    "orion.strategy",
    "orion.experiments",
    "orion.calibration",
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


def _package_dir() -> Path:
    path = orion.datasets.__file__
    assert path is not None
    return Path(path).parent


def _source_files() -> list[Path]:
    return sorted(_package_dir().rglob("*.py"))


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
    # __init__, manifest, csv_store, builder, loader.
    assert len(_source_files()) >= 5


def test_datasets_has_no_network_vendor_or_persistence_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files():
        found = {m.split(".")[0] for m in _imported_modules(source_path)} & (
            DISALLOWED_TOP_LEVEL_MODULES
        )
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.datasets imports disallowed modules: {offenders}"


def test_datasets_never_imports_backtest_strategy_or_analysis() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files():
        found = {
            m
            for m in _imported_modules(source_path)
            if any(m == pkg or m.startswith(pkg + ".") for pkg in DISALLOWED_ORION_SUBPACKAGES)
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.datasets imports a disallowed sibling package: {offenders}"


def test_datasets_only_reaches_data_and_provenance() -> None:
    # Stated positively as well as negatively: the whole of this package's
    # ORION surface is the data models, the historical ABC, and the one
    # hashing mechanism.
    allowed = {"orion.data", "orion.provenance", "orion.datasets"}
    for source_path in _source_files():
        for module in _imported_modules(source_path):
            if not module.startswith("orion"):
                continue
            root = ".".join(module.split(".")[:2])
            assert root in allowed, f"{source_path.name} imports unexpected {module!r}"


#: The only packages permitted to read a dataset. When Phase 3 shipped this was
#: empty — nothing in `src/` depended on `orion.datasets` at all — and Mission 6
#: Phase 4 added the first entry: a measurement consumes a verified dataset, so
#: `orion.calibration` reads this package and this package still reads nothing
#: of theirs. Widening this set is a deliberate edit, which is the point of
#: naming it rather than dropping the assertion.
ALLOWED_DATASET_CONSUMERS = ("calibration",)


def test_only_named_packages_import_datasets() -> None:
    # The architectural direction, not merely a count. `orion.datasets` sits on
    # the acquisition side and reads nothing above it — `DISALLOWED_ORION_
    # SUBPACKAGES` above already forbids the reverse edge for every consumer
    # named here, so allowing one in cannot make the graph cyclic.
    # Rooted at `src/orion`, so the first path segment is the package name.
    orion_root = _package_dir().parent
    importers: list[str] = []
    for source_path in sorted(orion_root.rglob("*.py")):
        if source_path.is_relative_to(_package_dir()):
            continue
        if any(m.startswith("orion.datasets") for m in _imported_modules(source_path)):
            relative = source_path.relative_to(orion_root)
            if relative.parts[0] not in ALLOWED_DATASET_CONSUMERS:
                importers.append(str(relative))
    assert not importers, (
        f"orion.datasets is imported by {importers}, which is not in {ALLOWED_DATASET_CONSUMERS}"
    )


def test_the_allowed_consumers_are_themselves_walled_off() -> None:
    # The narrowing above is only safe while every permitted consumer is still
    # forbidden in the reverse direction. Asserted rather than assumed, so
    # adding a consumer without adding the matching wall fails here.
    for consumer in ALLOWED_DATASET_CONSUMERS:
        assert f"orion.{consumer}" in DISALLOWED_ORION_SUBPACKAGES
