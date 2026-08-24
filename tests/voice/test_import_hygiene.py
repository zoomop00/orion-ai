"""Static proof of `orion.voice`'s isolation -- not "we didn't wire it up,"
but "the import doesn't exist."

Mirrors `tests/analysts/test_import_hygiene.py`/`tests/backtest/test_import_
hygiene.py`'s identical AST-based approach exactly: parsing each source
file's own `import`/`from ... import` statements, not inspecting
`sys.modules` at runtime, so this is a property of the package's source, not
of whatever else the rest of the test suite happened to already import.

Four directions:

1. Outbound (vendor/persistence): every file in `orion.voice` except the
   two concrete Deepgram provider files (`deepgram_stt.py`/`deepgram_tts.py`
   -- Mission 9 Phase 2's approved, single vendor) must import none of the
   network/AI-vendor/persistence modules `tests/backtest/test_import_
   hygiene.py` already disallows for the exact same reasons -- reused here
   unchanged, per this codebase's own "don't invent a second list"
   convention. `httpx` is carved out as an exception *only* for those two
   files (see `VENDOR_PROVIDER_FILES`/`_ALLOWED_EXTRA_FOR_VENDOR_FILES`
   below): `orion.voice.provider_base`/`orion.voice.schemas`/
   `orion.voice.__init__` must remain vendor-independent, and even the
   provider files may not import anything else on the disallowed list
   (no `sqlalchemy`, no `anthropic`, etc.) -- Deepgram REST access is the
   only new capability Phase 2 introduces.
2. Outbound (pipeline): `orion.voice` must import no Mission 0-8 pipeline
   package (data/analysis/research/reasoning/analysts/decision/risk/paper/
   memory/reflection/backtest) -- Phase 2 still has zero pipeline
   integration, only concrete provider implementations. `orion.provenance`
   is deliberately NOT in this list: it is a shared, dependency-free
   top-level utility (not a Mission-specific package) that `orion.voice.
   schemas` legitimately imports for `canonical_hash`, exactly as
   `orion.analysts`/`orion.decision`/`orion.risk` already do.
3. Inbound: no Mission 0-8 package imports `orion.voice` -- especially
   `orion.backtest`, called out with its own dedicated test per the task
   brief: a live voice interaction is inherently non-deterministic
   (real-time audio, live vendor calls), and `orion.backtest.engine.
   run_backtest`'s own determinism/no-look-ahead guarantee depends on every
   step being a pure function or a caller-supplied, presumably-
   deterministic implementation -- the same reasoning that already keeps
   `orion.analysts` out of backtest, applied to voice.
4. `orion.config` is NOT imported by either Deepgram provider file: both
   take `api_key: str` as a constructor parameter, exactly like
   `FinnhubEconomicCalendarProvider`/`ClaudeReasoningProvider` -- wiring a
   concrete provider to `Settings.deepgram_api_key` is orchestrator work,
   out of Phase 2's scope, not this file's job.

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
    # network / HTTP -- no file in orion.voice may import an HTTP client
    # except the two concrete Deepgram provider files (see
    # VENDOR_PROVIDER_FILES/_ALLOWED_EXTRA_FOR_VENDOR_FILES below), which
    # need httpx to actually reach Deepgram's REST API.
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
    # AI / market-data / broker / voice-vendor SDKs -- Deepgram is reached
    # via plain httpx (above), not a dedicated SDK, so no vendor SDK import
    # is expected anywhere in orion.voice, including the provider files.
    "anthropic",
    "twelvedata",
    "ccxt",
    "MetaTrader5",
    "oandapyV20",
    "deepgram",
    # persistence -- Mission 9 Phase 2 remains explicitly zero-persistence.
    "sqlalchemy",
}

# The only files allowed to import network modules -- Mission 9 Phase 2's
# two concrete provider implementations. Every other disallowed module
# above (vendor SDKs, persistence) stays forbidden even in these two files:
# httpx is the one new capability Phase 2 introduces, nothing else.
VENDOR_PROVIDER_FILES = {"deepgram_stt.py", "deepgram_tts.py"}
_ALLOWED_EXTRA_FOR_VENDOR_FILES = {"httpx"}

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
        allowed_extra = (
            _ALLOWED_EXTRA_FOR_VENDOR_FILES if source_path.name in VENDOR_PROVIDER_FILES else set()
        )
        found = (modules & DISALLOWED_TOP_LEVEL_MODULES) - allowed_extra
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.voice imports disallowed modules: {offenders}"


def test_only_vendor_provider_files_import_httpx() -> None:
    """`orion.voice.provider_base`/`orion.voice.schemas`/`orion.voice.
    __init__` must remain vendor-independent -- only `deepgram_stt.py`/
    `deepgram_tts.py` may know an HTTP client exists."""
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_source_files():
        if source_path.name in VENDOR_PROVIDER_FILES:
            continue
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        found = modules & _ALLOWED_EXTRA_FOR_VENDOR_FILES
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a non-provider orion.voice file imports httpx: {offenders}"


def test_deepgram_provider_files_do_not_import_orion_config() -> None:
    """Both concrete Deepgram provider files take `api_key: str` as a
    constructor parameter, exactly like `FinnhubEconomicCalendarProvider`/
    `ClaudeReasoningProvider` -- wiring to `Settings.deepgram_api_key` is
    orchestrator work, out of Phase 2's scope."""
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_source_files():
        if source_path.name not in VENDOR_PROVIDER_FILES:
            continue
        imported = _imported_modules(source_path)
        found = {m for m in imported if m == "orion.config" or m.startswith("orion.config.")}
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a Deepgram provider file imports orion.config: {offenders}"


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
