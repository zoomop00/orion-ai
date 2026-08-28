"""Static proof of `orion.orchestrator`'s dependency direction.

Mirrors `tests/{voice,interface,analysts}/test_import_hygiene.py`'s AST
approach exactly: each source file's own `import` statements are parsed, so
every assertion is a property of the committed source rather than of
whatever the test process happened to import first.

The direction being protected is one-way, and it is the whole reason this
package exists as a separate package at all:

    orion.orchestrator  ->  orion.{data,analysis,decision,risk,paper,reasoning}
    orion.orchestrator  ->  orion.voice        (the ResponseProvider ABC only)
    nothing             ->  orion.orchestrator

`orion.voice` is forbidden by its own hygiene test from importing any
engine, so an analysis-backed `ResponseProvider` cannot live there. It lives
here instead, and this file is what keeps the arrow from ever reversing —
deleting this package must leave Missions 0-9 exactly as they were, which is
only true while nothing points back at it.

The outbound bans matter as much as the inbound one. This is a composition
root, and composition roots attract everything: a credential read, a logger,
a database handle, a web framework, a uuid. Each is banned here and belongs
to the layer that already owns it — `orion.config` reads the environment,
`orion.interface` owns the HTTP framework, `orion.memory` owns SQLAlchemy,
and the concrete vendor providers own their SDKs. This package receives
constructed objects and nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.orchestrator

# Vendor SDKs, transports, frameworks, persistence and id-minting. Every one
# of these belongs to a layer that already owns it; a composition root that
# imports them stops being injectable and starts being un-testable.
DISALLOWED_TOP_LEVEL_MODULES = {
    "anthropic",
    "httpx",
    "requests",
    "sqlalchemy",
    "sqlite3",
    "shelve",
    "pickle",
    "redis",
    "boto3",
    "fastapi",
    "starlette",
    "uvicorn",
    "flask",
    "django",
    "websockets",
    "socketio",
    "sounddevice",
    "pyaudio",
    "logging",
    "tempfile",
    "uuid",
    "random",
    "secrets",
    "pandas",
    "numpy",
}

# Packages this one must not reach into. `orion.config` is the important
# entry: reading the environment is the composition root *above* this one's
# job (`orion.interface.app` already plays that role for Mission 10), and
# keeping it out is what lets every test here run with no `.env` at all.
DISALLOWED_ORION_SUBPACKAGES = {
    "orion.config",
    "orion.interface",
    "orion.memory",
    "orion.backtest",
    "orion.reflection",
    # Deferred out of Phase 5.1's approved scope. Present here so wiring the
    # Macro Analyst in is a deliberate edit to this list, not a quiet import.
    "orion.analysts",
    "orion.research",
}

DANGEROUS_CALL_NAMES = {"exec", "eval", "compile"}
DANGEROUS_MODULES = {"subprocess", "os"}

MISSION_0_9_PACKAGES = [
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
    "interface",
]


def _package_dir() -> Path:
    path = orion.orchestrator.__file__
    assert path is not None
    return Path(path).parent


def _repo_src_dir() -> Path:
    return _package_dir().parent


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


def _source_files() -> list[Path]:
    return sorted(_package_dir().rglob("*.py"))


def test_there_are_orchestrator_source_files_to_scan() -> None:
    assert len(_source_files()) >= 4


def test_orchestrator_imports_no_vendor_sdk_transport_or_persistence() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files():
        modules = {module.split(".")[0] for module in _imported_modules(source_path)}
        found = modules & DISALLOWED_TOP_LEVEL_MODULES
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.orchestrator imports disallowed modules: {offenders}"


def test_orchestrator_reads_no_configuration_and_no_out_of_scope_package() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files():
        found = {
            module
            for module in _imported_modules(source_path)
            if any(
                module == package or module.startswith(package + ".")
                for package in DISALLOWED_ORION_SUBPACKAGES
            )
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.orchestrator imports out-of-scope packages: {offenders}"


def test_orchestrator_touches_orion_voice_only_for_the_response_seam() -> None:
    # The single legitimate reason to depend on Mission 9 at all: the
    # `ResponseProvider` ABC it must implement, and the `Transcript` that
    # ABC is handed. Anything else — the runtime, the state machine, the
    # Deepgram providers, audio I/O — would mean the orchestrator had
    # started driving voice rather than answering it.
    allowed = {"orion.voice.response", "orion.voice.schemas"}
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files():
        found = {
            module
            for module in _imported_modules(source_path)
            if module.startswith("orion.voice") and module not in allowed
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.orchestrator reaches into orion.voice: {offenders}"


def test_orchestrator_has_no_exec_eval_or_subprocess_calls() -> None:
    # The AI-authored `rationale` and the untrusted transcript both pass
    # through this package. Nothing here may turn either into behaviour.
    offenders: dict[str, list[str]] = {}
    for source_path in _source_files():
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        found: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in DANGEROUS_CALL_NAMES
            ):
                found.append(f"{node.func.id}() at line {node.lineno}")
        modules = {module.split(".")[0] for module in _imported_modules(source_path)}
        found.extend(f"import {module}" for module in sorted(modules & DANGEROUS_MODULES))
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.orchestrator contains dangerous calls/imports: {offenders}"


def test_no_mission_0_9_package_imports_the_orchestrator() -> None:
    # The inbound half. Missions 0-9 must be exactly as they were: an
    # engine, the voice runtime or the HTTP adapter importing this package
    # would make the orchestrator un-deletable and would invert the layering
    # every other hygiene test in this repo protects.
    offenders: dict[str, set[str]] = {}
    for package_name in MISSION_0_9_PACKAGES:
        package_dir = _repo_src_dir() / package_name
        if not package_dir.is_dir():
            continue
        for source_path in sorted(package_dir.rglob("*.py")):
            found = {
                module
                for module in _imported_modules(source_path)
                if module == "orion.orchestrator" or module.startswith("orion.orchestrator.")
            }
            if found:
                offenders[str(source_path.relative_to(_repo_src_dir().parent))] = found
    assert not offenders, f"a Mission 0-9 package imports orion.orchestrator: {offenders}"


def test_the_orchestrator_is_still_unwired_in_phase_5_1() -> None:
    # Phase 5.1 builds the orchestrator; Phase 5.2 connects it. Until then
    # `orion.interface` must still construct Mission 9's static provider, so
    # that "the HTTP surface is unchanged" is a checked fact rather than a
    # claim in a commit message.
    service = (_repo_src_dir() / "interface" / "service.py").read_text(encoding="utf-8")
    assert "StaticResponseProvider" in service
    assert "AnalysisResponseProvider" not in service
