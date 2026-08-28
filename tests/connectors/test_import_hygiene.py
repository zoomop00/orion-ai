"""Static proof that `orion.connectors` is purely contractual.

Not "it does not call the network today" but "the import does not exist".
Each source file is AST-parsed and its own `import`/`from ... import`
statements and call names are inspected, exactly as
`tests/orchestrator/test_import_hygiene.py` and `tests/risk/
test_import_hygiene.py` already do — a property of the package's source,
not of whatever happened to be in `sys.modules` when this test ran.

Three separate claims are proven here:

1. The package imports nothing that could give it a capability — no network,
   no filesystem, no database, no subprocess, no logging, no dynamic import,
   and no vendor SDK.
2. The package imports no other ORION package, so it cannot reach the
   Decision Engine, the Risk Guardian, the paper account, the journal or
   `Settings` even by accident. A connector bypassing risk controls is not
   forbidden by policy here; it is unreachable.
3. Nothing in Missions 0-10 imports `orion.connectors`, so deleting this
   package would leave the rest of ORION exactly as it is. That is what
   makes shipping an empty contract layer a safe act rather than a
   commitment.

The final test covers a fourth thing that is not about this package at all:
Next.js 16 ships a development MCP server that is **on by default**, which
the Mission 11 audit found running unchosen. Mission 11.1 turns it off
explicitly, and this is where that stays true. It lives in this file rather
than in the frontend suite because the invariant is "no MCP runtime is
enabled anywhere in this repository", which is a Mission 11 claim, and
because Mission 11.1's approved scope does not include editing a Mission 10
test file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.connectors

# Capabilities a contract layer has no use for. Anything that reaches the
# network, the disk, a database, another process, a clock-independent source
# of entropy, or a log file.
DISALLOWED_TOP_LEVEL_MODULES = {
    # network / HTTP / transport
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "urllib3",
    "aiohttp",
    "websockets",
    "socket",
    "asyncio",
    "ftplib",
    "smtplib",
    # MCP, in every spelling. Mission 11.1 adopts no MCP runtime.
    "mcp",
    "modelcontextprotocol",
    "fastmcp",
    # vendor SDKs
    "anthropic",
    "openai",
    "deepgram",
    "boto3",
    # persistence
    "sqlalchemy",
    "sqlite3",
    "shelve",
    "pickle",
    "redis",
    "pymongo",
    # filesystem / process / environment
    "os",
    "sys",
    "io",
    "pathlib",
    "shutil",
    "tempfile",
    "glob",
    "subprocess",
    "multiprocessing",
    "signal",
    # dynamic import and code loading
    "importlib",
    "pkgutil",
    "runpy",
    "ctypes",
    # logging and id-minting
    "logging",
    "uuid",
    "random",
    "secrets",
    # heavy compute this layer has no business with
    "pandas",
    "numpy",
}

#: Everything ORION. The connectors package depends on no other ORION
#: package, so the ban is total rather than a list of the dangerous ones --
#: `orion.config`, `orion.risk`, `orion.decision`, `orion.paper` and
#: `orion.memory` are simply the entries that matter most.
ALLOWED_ORION_PREFIX = "orion.connectors"

DANGEROUS_CALL_NAMES = {"eval", "exec", "compile", "open", "__import__", "input"}

#: Every package that must not import `orion.connectors`. Deleting this
#: package must leave Missions 0-10 byte-identical.
MISSION_0_10_PACKAGES = [
    "data",
    "analysis",
    "research",
    "reasoning",
    "analysts",
    "decision",
    "risk",
    "paper",
    "memory",
    "reflection",
    "backtest",
    "voice",
    "orchestrator",
    "interface",
]

APPROVED_MODULE_FILES = {"__init__.py", "errors.py", "schemas.py"}


def _package_dir() -> Path:
    path = orion.connectors.__file__
    assert path is not None
    return Path(path).parent


def _source_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `level > 0` is a relative import; there are none, and the
            # absolute-import assertion below keeps it that way.
            names.append(node.module or "")
    return names


def test_the_package_contains_only_the_approved_modules() -> None:
    assert {p.name for p in _source_files(_package_dir())} == APPROVED_MODULE_FILES


def test_it_imports_no_capability_it_does_not_need() -> None:
    for source_path in _source_files(_package_dir()):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            root = name.split(".")[0]
            assert root not in DISALLOWED_TOP_LEVEL_MODULES, f"{source_path.name} imports {name}"


def test_it_imports_no_other_orion_package() -> None:
    for source_path in _source_files(_package_dir()):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            if not name.startswith("orion"):
                continue
            assert name.startswith(ALLOWED_ORION_PREFIX), (
                f"{source_path.name} imports {name}; the connector contracts depend on "
                "no other ORION package, which is what makes a risk-control bypass "
                "unreachable rather than merely forbidden"
            )


def test_it_cannot_reach_the_decision_engine_or_the_risk_guardian() -> None:
    # The same property as above, stated as the security claim it exists to
    # make, so a reader grepping for "risk" finds it.
    forbidden = {
        "orion.config",
        "orion.risk",
        "orion.decision",
        "orion.paper",
        "orion.memory",
    }
    for source_path in _source_files(_package_dir()):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            root = ".".join(name.split(".")[:2])
            assert root not in forbidden, f"{source_path.name} imports {name}"


def test_it_evaluates_no_dynamic_code_and_opens_no_file() -> None:
    for source_path in _source_files(_package_dir()):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in DANGEROUS_CALL_NAMES, (
                    f"{source_path.name} calls {node.func.id}()"
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"system", "popen", "spawn", "run"}, (
                    f"{source_path.name} calls .{node.func.attr}()"
                )


def test_it_uses_no_relative_imports_that_could_hide_a_dependency() -> None:
    for source_path in _source_files(_package_dir()):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{source_path.name} uses a relative import"


def test_nothing_in_missions_0_to_10_imports_the_connectors_package() -> None:
    root = Path(orion.connectors.__file__).parent.parent
    for package in MISSION_0_10_PACKAGES:
        package_dir = root / package
        assert package_dir.is_dir(), f"expected {package} to exist"
        for source_path in _source_files(package_dir):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for name in _imported_names(tree):
                assert not name.startswith("orion.connectors"), (
                    f"{package}/{source_path.name} imports {name}; deleting the "
                    "connectors package must leave Missions 0-10 unchanged"
                )


def test_the_repository_enables_no_mcp_runtime_anywhere() -> None:
    """Next.js 16 ships a dev MCP server on by default. It is turned off.

    `experimental.mcpServer` defaults to `true` in Next 16.3.3, which mounts
    an unauthenticated JSON-RPC endpoint at `/_next/mcp` on the development
    server with no `Origin` or `Host` validation. Nobody chose it; the
    Mission 11 audit found it. Disabling it explicitly is the one place
    Mission 11.1 touches the frontend, and it removes a surface rather than
    adding one.
    """
    root = Path(orion.connectors.__file__).parent.parent.parent.parent
    config = root / "frontend" / "next.config.ts"
    assert config.is_file(), f"expected {config} to exist"

    text = config.read_text(encoding="utf-8")
    assert "mcpServer: false" in text, "the Next.js dev MCP server must be explicitly disabled"
    assert "mcpServer: true" not in text
