"""Static proof of `orion.voice`'s isolation -- not "we didn't wire it up,"
but "the import doesn't exist."

Mirrors `tests/analysts/test_import_hygiene.py`/`tests/backtest/test_import_
hygiene.py`'s identical AST-based approach exactly: parsing each source
file's own `import`/`from ... import` statements, not inspecting
`sys.modules` at runtime, so this is a property of the package's source, not
of whatever else the rest of the test suite happened to already import.

Three directions:

1. Outbound (vendor/persistence): Phase 1 has zero concrete provider and
   zero new dependency, so `orion.voice` must import none of the network/
   AI-vendor/persistence modules `tests/backtest/test_import_hygiene.py`
   already disallows for the exact same reasons -- reused here unchanged,
   per this codebase's own "don't invent a second list" convention.
2. Outbound (pipeline): `orion.voice` must import no Mission 0-8 pipeline
   package (data/analysis/research/reasoning/analysts/decision/risk/paper/
   memory/reflection/backtest) -- Phase 1 has zero pipeline integration.
   `orion.provenance` is deliberately NOT in this list: it is a shared,
   dependency-free top-level utility (not a Mission-specific package) that
   `orion.voice.schemas` legitimately imports for `canonical_hash`, exactly
   as `orion.analysts`/`orion.decision`/`orion.risk` already do.
3. Inbound: no Mission 0-8 package imports `orion.voice` -- especially
   `orion.backtest`, called out with its own dedicated test per the task
   brief: a live voice interaction is inherently non-deterministic
   (real-time audio, live vendor calls), and `orion.backtest.engine.
   run_backtest`'s own determinism/no-look-ahead guarantee depends on every
   step being a pure function or a caller-supplied, presumably-
   deterministic implementation -- the same reasoning that already keeps
   `orion.analysts` out of backtest, applied to voice.

Also proves the "transcript text is never consumed as an instruction"
requirement structurally: no file anywhere in `orion.voice` calls `exec`,
`eval`, `compile`, or a subprocess/shell function -- the only thing that
could turn untrusted transcript text into executed behavior.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.voice

DISALLOWED_TOP_LEVEL_MODULES = {
    # network / HTTP -- Phase 1 has no concrete provider, so no HTTP client
    # dependency is expected at all yet.
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
    # AI / market-data / broker / voice-vendor SDKs -- no vendor is chosen
    # in Phase 1 (see docs/DECISIONS.md); vendor selection is deliberately
    # a separate, later decision.
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    # persistence -- Phase 1 is explicitly zero-persistence.
    "sqlalchemy",
}

DISALLOWED_ORION_SUBPACKAGES = {
    "orion.data",
    "orion.analysis",
    "orion.research",
    "orion.reasoning",
    "orion.analysts",
    "orion.decision",
    "orion.risk",
    "orion.paper",
    "orion.memory",
    "orion.reflection",
    "orion.backtest",
}

DANGEROUS_CALL_NAMES = {"exec", "eval", "compile"}
DANGEROUS_MODULES = {"subprocess", "os"}

MISSION_0_8_PACKAGES = [
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
]


def _voice_package_dir() -> Path:
    path = orion.voice.__file__
    assert path is not None
    return Path(path).parent


def _repo_src_dir() -> Path:
    return _voice_package_dir().parent


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


def _voice_source_files() -> list[Path]:
    return sorted(_voice_package_dir().rglob("*.py"))


def test_orion_voice_package_has_source_files_to_scan() -> None:
    assert len(_voice_source_files()) >= 3


def test_orion_voice_has_no_disallowed_top_level_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_source_files():
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        found = modules & DISALLOWED_TOP_LEVEL_MODULES
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.voice imports disallowed modules: {offenders}"


def test_orion_voice_has_no_pipeline_package_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_source_files():
        imported = _imported_modules(source_path)
        found = {
            m
            for m in imported
            if any(m == pkg or m.startswith(pkg + ".") for pkg in DISALLOWED_ORION_SUBPACKAGES)
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.voice imports a Mission 0-8 pipeline package: {offenders}"


def test_orion_voice_has_no_exec_eval_or_subprocess_calls() -> None:
    """The only way untrusted transcript text could become executed
    behavior is if something in this package fed it to exec/eval/a
    subprocess. Proven absent, not merely believed absent."""
    offenders: dict[str, list[str]] = {}
    for source_path in _voice_source_files():
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
    assert not offenders, f"orion.voice contains dangerous calls/imports: {offenders}"


def test_no_mission_0_8_package_imports_orion_voice() -> None:
    offenders: dict[str, set[str]] = {}
    for pkg_name in MISSION_0_8_PACKAGES:
        pkg_dir = _repo_src_dir() / pkg_name
        if not pkg_dir.is_dir():
            continue
        for source_path in sorted(pkg_dir.rglob("*.py")):
            imported = _imported_modules(source_path)
            found = {m for m in imported if m == "orion.voice" or m.startswith("orion.voice.")}
            if found:
                offenders[str(source_path.relative_to(_repo_src_dir().parent))] = found
    assert not offenders, f"a Mission 0-8 package imports orion.voice: {offenders}"


def test_orion_backtest_specifically_cannot_import_orion_voice() -> None:
    """Called out with its own dedicated test, distinct from the general
    sweep above: `orion.backtest.engine.run_backtest`'s determinism/no-
    look-ahead guarantee depends on every step being a pure function or a
    caller-supplied, presumably-deterministic implementation. A live voice
    interaction (real-time audio, live vendor calls) can never be either --
    this is not an accidental coupling to guard against, it is a
    structural incompatibility, the same reasoning that already keeps
    `orion.analysts` out of backtest (see tests/backtest/test_import_
    hygiene.py and docs/DECISIONS.md)."""
    backtest_dir = _repo_src_dir() / "backtest"
    offenders: dict[str, set[str]] = {}
    for source_path in sorted(backtest_dir.rglob("*.py")):
        imported = _imported_modules(source_path)
        found = {m for m in imported if m == "orion.voice" or m.startswith("orion.voice.")}
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.backtest imports orion.voice: {offenders}"
