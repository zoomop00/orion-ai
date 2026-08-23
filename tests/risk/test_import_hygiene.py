"""Static proof that `orion.risk` has no network, broker, or persistence
dependency -- not "we didn't call it," but "the import doesn't exist."

`orion.risk.guardian.review` is documented as a pure function (no network,
filesystem, or randomness), and the module docstrings across this package
repeatedly assert "no broker, network, execution, or live-trading
integration." Parsing each source file's own `import`/`from ... import`
statements, rather than inspecting `sys.modules` at runtime, is what makes
this a property of the package's source -- not of whatever else happened to
already be imported by the rest of the test suite when this test runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.risk

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
    # AI / market-data / broker providers -- orion.risk never talks to a
    # provider directly, only to already-validated in-process objects.
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    # persistence -- Mission 2 (decision memory) is a separate, unbuilt
    # layer; orion.risk must not gain a database dependency to run.
    "sqlalchemy",
}


def _risk_package_dir() -> Path:
    path = orion.risk.__file__
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


def _risk_source_files() -> list[Path]:
    return sorted(_risk_package_dir().rglob("*.py"))


def test_orion_risk_package_has_source_files_to_scan() -> None:
    # Guards against this test silently passing over zero files if the
    # package layout ever changes (e.g. a future package -> single-module
    # collapse) -- policy, schemas, controls, guardian, __init__.
    assert len(_risk_source_files()) >= 5


def test_orion_risk_has_no_network_broker_or_persistence_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _risk_source_files():
        found = _imported_top_level_modules(source_path) & DISALLOWED_TOP_LEVEL_MODULES
        if found:
            offenders[str(source_path.relative_to(_risk_package_dir().parent.parent))] = found
    assert not offenders, f"orion.risk imports disallowed modules: {offenders}"
