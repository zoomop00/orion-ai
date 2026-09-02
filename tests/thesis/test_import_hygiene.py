"""Static proof that `orion.thesis` is a schema package and nothing more.

WHY THIS FILE IS LOAD-BEARING. Mission 8 v2.2 let `orion.paper`,
`orion.memory` and `orion.reflection` name a `MarketThesis` by moving the
schema into this package instead of moving the wall that stops those three
importing `orion.analysts`. That trade is only honest while this package stays
inert: the moment it imports an analyst, an engine, a store or a provider, it
becomes a side door into exactly what the wall was protecting, and
`orion.paper -> orion.thesis -> ...` would reach it transitively.

So this suite pins the whole import list, not a blocklist. A package whose
entire dependency set is three named upstream modules cannot become a route to
anything, and any addition has to be argued for here rather than noticed later.

The complementary half lives in `tests/analysts/test_import_hygiene.py`, which
now lists `thesis` among the packages forbidden from importing
`orion.analysts`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.thesis

#: The complete permitted import list. `orion.analysis.structure` supplies
#: `VolatilityLevel`, `orion.data.models` supplies `Timeframe`, and
#: `orion.provenance` is the one shared hashing mechanism whose own docstring
#: states every layer may import it. Nothing else, ever.
ALLOWED_ORION_MODULES = {
    "orion.analysis.structure",
    "orion.data.models",
    "orion.provenance",
}

#: Third-party and standard-library modules a schema legitimately needs.
ALLOWED_TOP_LEVEL_MODULES = {
    "__future__",
    "collections.abc",
    "datetime",
    "enum",
    "types",
    "pydantic",
    "orion",
}


def _package_dir() -> Path:
    path = orion.thesis.__file__
    assert path is not None
    return Path(path).parent


def _source_files() -> list[Path]:
    return sorted(_package_dir().rglob("*.py"))


def _imports(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module)
    return modules


def test_there_are_source_files_to_scan() -> None:
    assert len(_source_files()) >= 2  # __init__, schemas


def test_the_entire_orion_import_list_is_the_allowed_one() -> None:
    """AN ALLOWLIST, NOT A BLOCKLIST. A blocklist only stops the dependencies
    someone thought of; this stops all of them."""
    for source_path in _source_files():
        reached = {module for module in _imports(source_path) if module.startswith("orion")}
        assert reached <= ALLOWED_ORION_MODULES, (
            f"{source_path.name} imports orion modules outside the allowed set: "
            f"{reached - ALLOWED_ORION_MODULES}"
        )


def test_nothing_outside_the_standard_library_and_pydantic_is_imported() -> None:
    for source_path in _source_files():
        roots = {module.split(".")[0] for module in _imports(source_path)}
        extra = roots - {name.split(".")[0] for name in ALLOWED_TOP_LEVEL_MODULES}
        assert not extra, f"{source_path.name} imports {extra}"


def test_the_thesis_package_never_reaches_an_analyst() -> None:
    """The side door, closed explicitly.

    Covered by the allowlist above, and restated on its own because it is the
    single property this package's existence depends on: `orion.paper` may
    import `orion.thesis`, so `orion.thesis` importing `orion.analysts` would
    hand the execution path a specialist by two hops.
    """
    for source_path in _source_files():
        for module in _imports(source_path):
            assert not module.startswith("orion.analysts"), (
                f"{source_path.name} reaches an analyst; the wall is open at the side"
            )


def test_the_thesis_package_reaches_no_engine_store_or_orchestrator() -> None:
    banned = (
        "orion.decision",
        "orion.risk",
        "orion.paper",
        "orion.memory",
        "orion.reflection",
        "orion.backtest",
        "orion.orchestrator",
        "orion.interface",
        "orion.research",
        "orion.reasoning",
        "orion.voice",
        "orion.config",
    )
    for source_path in _source_files():
        for module in _imports(source_path):
            assert not module.startswith(banned), f"{source_path.name} imports {module}"


def test_it_has_no_network_persistence_clock_or_filesystem_dependency() -> None:
    for source_path in _source_files():
        roots = {module.split(".")[0] for module in _imports(source_path)}
        for forbidden in (
            "httpx",
            "requests",
            "urllib",
            "socket",
            "anthropic",
            "sqlalchemy",
            "sqlite3",
            "logging",
            "random",
            "time",
            "os",
            "pathlib",
            "subprocess",
        ):
            assert forbidden not in roots, f"{source_path.name} imports {forbidden}"


def test_it_computes_nothing_and_reads_no_clock() -> None:
    """A schema module holds shapes and canonical wording. `formed_at` is
    supplied by a caller, exactly as every other timestamp in this codebase is,
    so nothing here may reach for a clock or a random source."""
    banned = {"now", "utcnow", "today", "time", "monotonic", "uuid1", "uuid4"}
    for source_path in _source_files():
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned, f"{source_path.name} calls {node.func.attr}()"
            if isinstance(node, ast.Name):
                assert node.id != "random", f"{source_path.name} reaches for randomness"


def test_no_engine_may_import_the_thesis_package_except_the_named_ones() -> None:
    """The inbound side, stated so the permission stays visible.

    A `MarketThesis` is produced by `orion.analysts.synthesis`, carried by
    `orion.paper.session`, stored by `orion.memory`, read by
    `orion.reflection` and attached to a report by `orion.orchestrator`.
    `orion.cli` is absent because it never names the type -- it reads the
    thesis off the report and hands it on. **`orion.decision` and `orion.risk`
    are absent too, and that is the point**: the layers that propose and
    authorise a trade cannot see a thesis at all, so no specialist reading can
    reach either.
    """
    src = _package_dir().parent
    importers: set[str] = set()
    for path in sorted(src.rglob("*.py")):
        relative = path.relative_to(src).as_posix()
        if relative.startswith("thesis/"):
            continue
        if any(module.startswith("orion.thesis") for module in _imports(path)):
            importers.add(relative.split("/")[0] if "/" in relative else relative)

    assert importers == {"analysts", "memory", "orchestrator", "paper", "reflection"}
    assert "decision" not in importers
    assert "risk" not in importers
