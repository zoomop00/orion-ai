"""Static proof of `orion.orchestrator`'s dependency direction.

Mirrors `tests/{voice,interface,analysts}/test_import_hygiene.py`'s AST
approach exactly: each source file's own `import` statements are parsed, so
every assertion is a property of the committed source rather than of
whatever the test process happened to import first.

The direction being protected is one-way, and it is the whole reason this
package exists as a separate package at all:

    orion.interface     ->  orion.orchestrator        (Phase 5.2, and only it)
    orion.orchestrator  ->  orion.{data,analysis,decision,risk,paper,reasoning}
    orion.orchestrator  ->  orion.voice               (the ResponseProvider seam)
    no engine           ->  orion.orchestrator

`orion.voice` is forbidden by its own hygiene test from importing any
engine, so an analysis-backed `ResponseProvider` cannot live there. It lives
here instead, and this file is what keeps the arrow from ever reversing —
deleting this package must leave Missions 0-9's *engines* exactly as they
were, which is only true while none of them points back at it.

PHASE 5.2 NARROWED THE INBOUND RULE RATHER THAN DROPPING IT. Phase 5.1
allowed nothing at all to import this package, because nothing needed to.
The HTTP adapter now does, and that is the whole point of 5.2 — but it is
the *only* thing that may. Every engine, the voice runtime included, still
must not, so the layering the rest of this repo's hygiene tests protect is
unchanged: `orion.interface` may call the orchestrator, and it still may not
reach `orion.decision`/`orion.risk`/`orion.paper` itself.

The outbound bans matter as much as the inbound one. This is a composition
root, and composition roots attract everything: a credential read, a logger,
a database handle, a web framework, a uuid. Each is banned here and belongs
to the layer that already owns it — `orion.interface` owns the HTTP
framework, `orion.memory` owns SQLAlchemy, and the concrete vendor providers
own their SDKs.

ONE FILE MAY READ CONFIGURATION, AND ONLY ONE. Phase 5.2 needs something to
turn `Settings` into constructed providers, and `orion.interface` is
forbidden from being that something (see its own hygiene test's
"an HTTP handler is one edit away from making trading decisions"). So
`wiring.py` does it, and `_ALLOWED_EXTRA_IMPORTS_BY_FILE` below is the
carve-out — the same per-file allowlist idiom `tests/voice/test_import_
hygiene.py` uses for `sounddevice` and the frontend's source-safety test uses
for `fetch`. Every other module in this package still may not read the
environment, and `__init__.py` deliberately does not re-export `wiring`, so
`import orion.orchestrator` still costs no vendor SDK.
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

# The per-file carve-out. `wiring.py` is the one place allowed to read
# `Settings`, because something has to and `orion.interface` may not.
_ALLOWED_EXTRA_ORION_IMPORTS_BY_FILE: dict[str, set[str]] = {
    "wiring.py": {"orion.config"},
}

DANGEROUS_CALL_NAMES = {"exec", "eval", "compile"}
DANGEROUS_MODULES = {"subprocess", "os"}

# Everything that must never import the orchestrator. `interface` is
# deliberately absent from this list and asserted separately below: it is the
# one permitted importer, and stating that as its own test makes the
# exception visible rather than a hole in a list.
ENGINE_PACKAGES = [
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
        allowed = _ALLOWED_EXTRA_ORION_IMPORTS_BY_FILE.get(source_path.name, set())
        found = {
            module
            for module in _imported_modules(source_path)
            if any(
                module == package or module.startswith(package + ".")
                for package in DISALLOWED_ORION_SUBPACKAGES
            )
            and module not in allowed
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.orchestrator imports out-of-scope packages: {offenders}"


def test_only_the_wiring_file_reads_configuration() -> None:
    """`wiring.py` may import `orion.config`; nothing else in the package may.

    The inverse assertion matters as much as the ban: if the carve-out ever
    stops being used, it should be deleted rather than left standing as a
    permission nobody needs.
    """
    for source_path in _source_files():
        reads_config = any(
            module == "orion.config" or module.startswith("orion.config.")
            for module in _imported_modules(source_path)
        )
        if source_path.name == "wiring.py":
            assert reads_config, "wiring.py should be the file that reads Settings"
        else:
            assert not reads_config, f"{source_path.name} must not read configuration"


def test_importing_the_package_costs_no_vendor_sdk() -> None:
    """`__init__.py` must not re-export `wiring`.

    `wiring.py` pulls in the Twelve Data and Anthropic providers, and through
    them `httpx` and `anthropic`. Keeping it off the package's front door
    means `import orion.orchestrator` stays as cheap and as credential-free
    as Phase 5.1 made it, and makes the one file with vendor reach visible at
    every call site that wants it.
    """
    init = _package_dir() / "__init__.py"
    found = {
        module
        for module in _imported_modules(init)
        if module == "orion.orchestrator.wiring" or module.endswith(".wiring")
    }
    assert not found, f"__init__.py re-exports the wiring module: {found}"


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


def test_no_engine_package_imports_the_orchestrator() -> None:
    # The inbound half. Every engine and the voice runtime must be exactly as
    # they were: an import in this direction would invert the layering every
    # other hygiene test in this repo protects, and would make the
    # orchestrator un-deletable from underneath them.
    offenders: dict[str, set[str]] = {}
    for package_name in ENGINE_PACKAGES:
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
    assert not offenders, f"an engine package imports orion.orchestrator: {offenders}"


def test_the_http_adapter_is_the_only_thing_wired_to_the_orchestrator() -> None:
    """Phase 5.2's arrow, asserted from both ends.

    `orion.interface` must reach the orchestrator (otherwise nothing analyses)
    and must still not reach the engines directly (otherwise an HTTP handler
    is one edit away from a trade decision). Both halves are checked here so
    the exception carved out of the inbound rule above stays exactly as wide
    as it was meant to be.
    """
    interface_dir = _repo_src_dir() / "interface"
    imported: set[str] = set()
    for source_path in sorted(interface_dir.rglob("*.py")):
        imported |= _imported_modules(source_path)

    assert any(module.startswith("orion.orchestrator") for module in imported), (
        "orion.interface should reach the orchestrator in Phase 5.2"
    )
    forbidden = {
        module
        for module in imported
        if any(
            module == f"orion.{package}" or module.startswith(f"orion.{package}.")
            for package in ENGINE_PACKAGES
            if package != "voice"
        )
    }
    assert not forbidden, f"orion.interface reaches an engine directly: {forbidden}"


def test_the_static_response_provider_is_still_the_fallback() -> None:
    """Wiring the orchestrator in must not delete the path that existed
    before it. A deployment with no Twelve Data or Anthropic credential still
    answers ordinary turns exactly as Phase 3 did, and the service is the
    file that has to say so."""
    service = (_repo_src_dir() / "interface" / "service.py").read_text(encoding="utf-8")
    assert "StaticResponseProvider" in service
    assert "AnalysisResponseProvider" in service
