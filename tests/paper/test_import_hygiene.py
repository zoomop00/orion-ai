"""Static proof that `orion.paper` has no network, broker, or live-execution
dependency in this milestone.

Mirrors `tests/risk/test_import_hygiene.py` exactly: parses each source
file's own AST for `import`/`from ... import` statements (not a
`sys.modules` runtime check) so this is a property of the package's source,
not of whatever else this test run happened to already import. Mission 4
ships an in-memory `PortfolioStore` only (`orion.paper.store.
InMemoryPortfolioStore`) -- no database, no filesystem, no network -- so
`orion.paper`'s core needs nothing from the disallowed list either, even
though it is not held to the exact same purity standard as `orion.risk`
(it is explicitly the layer permitted to mutate account state).
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.paper

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
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    "sqlalchemy",
}


def _paper_package_dir() -> Path:
    path = orion.paper.__file__
    assert path is not None
    return Path(path).parent


def _imported_top_level_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def _paper_source_files() -> list[Path]:
    return sorted(_paper_package_dir().rglob("*.py"))


def test_orion_paper_package_has_source_files_to_scan() -> None:
    assert len(_paper_source_files()) >= 5  # __init__, policy, schemas, store, execution


def test_orion_paper_has_no_network_broker_or_persistence_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _paper_source_files():
        found = _imported_top_level_modules(source_path) & DISALLOWED_TOP_LEVEL_MODULES
        if found:
            offenders[str(source_path.relative_to(_paper_package_dir().parent.parent))] = found
    assert not offenders, f"orion.paper imports disallowed modules: {offenders}"
