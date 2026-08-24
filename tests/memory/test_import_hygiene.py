"""Static proof that adding `orion.memory` did not create a reverse
dependency from `orion.risk` or `orion.paper` onto it.

Mirrors `tests/risk/test_import_hygiene.py` and `tests/paper/test_import_
hygiene.py`'s AST-based approach. Those two tests already prove `orion.risk`/
`orion.paper` have no `sqlalchemy` import; this test proves the complementary
fact those two cannot: neither package imports `orion.memory` itself (which
could reintroduce a persistence dependency indirectly, without importing
`sqlalchemy` directly). `orion.memory` is the only package permitted to
depend on both `orion.paper` (it implements `orion.paper.store.
PortfolioStore`) and `orion.risk`/`orion.decision`/`orion.reasoning`/`orion.
analysis` (it journals their domain objects) -- dependencies point one way,
inward from `orion.memory`, never back out.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.paper
import orion.risk


def _first_two_dotted_parts(dotted_name: str) -> str:
    parts = dotted_name.split(".")
    return ".".join(parts[:2]) if len(parts) > 1 else parts[0]


def _imported_top_level_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(_first_two_dotted_parts(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            modules.add(_first_two_dotted_parts(node.module))
    return modules


def _source_files(package_dir: Path) -> list[Path]:
    return sorted(package_dir.rglob("*.py"))


def _package_dir(package: object) -> Path:
    path = package.__file__  # type: ignore[attr-defined]
    assert path is not None
    return Path(path).parent


def _offenders_importing_orion_memory(package_dir: Path) -> dict[str, set[str]]:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files(package_dir):
        found = _imported_top_level_modules(source_path) & {"orion.memory"}
        if found:
            offenders[str(source_path.relative_to(package_dir.parent.parent))] = found
    return offenders


def test_orion_risk_does_not_import_orion_memory() -> None:
    offenders = _offenders_importing_orion_memory(_package_dir(orion.risk))
    assert not offenders, f"orion.risk imports orion.memory: {offenders}"


def test_orion_paper_does_not_import_orion_memory() -> None:
    offenders = _offenders_importing_orion_memory(_package_dir(orion.paper))
    assert not offenders, f"orion.paper imports orion.memory: {offenders}"
