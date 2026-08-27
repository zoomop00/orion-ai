"""Static proof of the Mission 10 Phase 2 dependency direction.

Mirrors `tests/voice/test_import_hygiene.py`'s AST approach exactly: this
parses each source file's own `import` statements rather than inspecting
`sys.modules`, so every assertion is a property of the committed source, not
of whatever the rest of the suite happened to import first.

The direction being protected is one-way:

    orion.interface  ->  orion.voice  ->  (Deepgram | in-memory audio)

`orion.interface` may depend on `orion.voice`. Nothing may depend on
`orion.interface` — not `orion.voice`, and not any Mission 0-8 package.
That is what keeps the adapter removable: deleting this package must leave
Missions 0-9 exactly as they were, which is only true if nothing points
back at it.

`orion.config` is the one deliberate exception in the outbound direction.
`orion.interface.app` is the composition root — the place that turns
configuration into constructed objects — and `orion.config`'s own docstring
reserves environment reading for itself ("nothing downstream of this module
should read `os.environ` directly"). `orion.interface.service` still must
not import it: it takes `deepgram_api_key: str`, exactly like
`DeepgramTranscriptionProvider`/`ClaudeReasoningProvider`/
`FinnhubEconomicCalendarProvider` already do, which is what makes it
testable with no environment at all.

Several Mission 9 invariants are re-verified here from the outside rather
than assumed. `orion.voice.runtime` staying free of HTTP/framework imports
matters more now that an HTTP framework is in the dependency tree at all,
and re-checking `httpx`/`sounddevice` confinement from this package proves
Phase 2 did not quietly widen a Mission 9 boundary to make integration
easier. Re-asserting them here rather than editing
`tests/voice/test_import_hygiene.py` also means Phase 2 changed no Mission
9 file, test files included.
"""

from __future__ import annotations

import ast
import tokenize
from pathlib import Path

import orion.interface
import orion.voice

# Frameworks, servers and transports. `orion.interface` is allowed these;
# nothing it depends on is.
WEB_FRAMEWORK_MODULES = {
    "fastapi",
    "starlette",
    "uvicorn",
    "asgiref",
    "flask",
    "django",
    "aiohttp",
    "websockets",
    "socketio",
    "sse_starlette",
}

# Everything `orion.interface` itself must stay clear of. Persistence,
# randomness/id-minting and hardware audio are all out of scope for an HTTP
# adapter, and `logging` is excluded specifically so no future edit can
# start writing request payloads to a log.
INTERFACE_DISALLOWED_MODULES = {
    "sqlalchemy",
    "sqlite3",
    "shelve",
    "pickle",
    "redis",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "pymongo",
    "boto3",
    "logging",
    "tempfile",
    "sounddevice",
    "pyaudio",
    "anthropic",
    "deepgram",
    "uuid",
    "random",
    "secrets",
    "multipart",
    "python_multipart",
}

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

DANGEROUS_CALL_NAMES = {"exec", "eval", "compile"}
DANGEROUS_MODULES = {"subprocess", "os"}


def _package_dir(package: object) -> Path:
    path = getattr(package, "__file__", None)
    assert path is not None
    return Path(path).parent


def _src_dir() -> Path:
    return _package_dir(orion.interface).parent


def _code_without_comments_or_strings(source_path: Path) -> str:
    """The file's executable tokens only -- no comments, no string literals.

    Necessary because several modules here *document* the very things these
    tests forbid ("this file does not import sounddevice", "WebSockets are
    deferred"). A raw substring scan would flag the explanation instead of
    a violation, and deleting the explanation to satisfy the test would be
    exactly backwards.
    """
    skip = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE}
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    if fstring_middle is not None:
        skip.add(fstring_middle)
    pieces: list[str] = []
    with source_path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in skip:
                continue
            pieces.append(token.string)
    return " ".join(pieces)


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


def _interface_files() -> list[Path]:
    return sorted(_package_dir(orion.interface).rglob("*.py"))


def _voice_files() -> list[Path]:
    return sorted(_package_dir(orion.voice).rglob("*.py"))


def test_there_are_interface_source_files_to_scan() -> None:
    assert len(_interface_files()) >= 4


# ------------------------------------------------------ inbound: nobody may


def test_orion_voice_does_not_import_orion_interface() -> None:
    """The adapter points at the runtime, never the other way round.

    A single import in this direction would make `orion.voice` unusable
    without a web framework installed and would let HTTP concerns leak into
    the one component Mission 9 built to be transport-agnostic.
    """
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_files():
        found = {
            module
            for module in _imported_modules(source_path)
            if module == "orion.interface" or module.startswith("orion.interface.")
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.voice imports orion.interface: {offenders}"


def test_no_mission_0_8_package_imports_orion_interface() -> None:
    offenders: dict[str, set[str]] = {}
    for package_name in MISSION_0_8_PACKAGES:
        package_dir = _src_dir() / package_name
        if not package_dir.is_dir():
            continue
        for source_path in sorted(package_dir.rglob("*.py")):
            found = {
                module
                for module in _imported_modules(source_path)
                if module == "orion.interface" or module.startswith("orion.interface.")
            }
            if found:
                offenders[source_path.name] = found
    assert not offenders, f"a Mission 0-8 package imports orion.interface: {offenders}"


def test_shared_top_level_modules_do_not_import_orion_interface() -> None:
    """`orion.config`/`orion.provenance`/`orion.http_errors` are depended on
    *by* the interface; a cycle back would be an architectural inversion."""
    offenders: dict[str, set[str]] = {}
    for name in ("config.py", "provenance.py", "http_errors.py", "__init__.py"):
        source_path = _src_dir() / name
        if not source_path.is_file():
            continue
        found = {
            module
            for module in _imported_modules(source_path)
            if module == "orion.interface" or module.startswith("orion.interface.")
        }
        if found:
            offenders[name] = found
    assert not offenders, f"a shared orion module imports orion.interface: {offenders}"


# ----------------------------------------------------- outbound: what it may


def test_orion_interface_imports_no_mission_0_8_pipeline_package() -> None:
    """This is an adapter over voice, not the ORION orchestrator.

    The moment it can reach `orion.decision`/`orion.risk`/`orion.paper`, an
    HTTP handler is one edit away from making trading decisions. Keeping
    the import impossible is stronger than keeping it unwritten.
    """
    disallowed = {f"orion.{name}" for name in MISSION_0_8_PACKAGES}
    offenders: dict[str, set[str]] = {}
    for source_path in _interface_files():
        found = {
            module
            for module in _imported_modules(source_path)
            if any(module == pkg or module.startswith(pkg + ".") for pkg in disallowed)
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.interface imports a Mission 0-8 package: {offenders}"


def test_orion_interface_imports_nothing_from_the_disallowed_set() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _interface_files():
        top_level = {module.split(".")[0] for module in _imported_modules(source_path)}
        found = top_level & INTERFACE_DISALLOWED_MODULES
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.interface imports a disallowed module: {offenders}"


def test_only_the_composition_root_reads_configuration() -> None:
    """`app.py` may import `orion.config`; `service.py`/`schemas.py` may not."""
    offenders: dict[str, set[str]] = {}
    for source_path in _interface_files():
        if source_path.name == "app.py":
            continue
        found = {
            module
            for module in _imported_modules(source_path)
            if module == "orion.config" or module.startswith("orion.config.")
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a non-composition-root interface file reads config: {offenders}"

    assert "orion.config" in _imported_modules(_package_dir(orion.interface) / "app.py")


def test_the_web_framework_stays_inside_app_py() -> None:
    """Only the HTTP surface knows there is HTTP.

    `service.py` composes and runs a voice turn; if it imported FastAPI it
    could not be reused by a future CLI, scheduler or orchestrator without
    dragging a web server along.
    """
    offenders: dict[str, set[str]] = {}
    for source_path in _interface_files():
        if source_path.name == "app.py":
            continue
        top_level = {module.split(".")[0] for module in _imported_modules(source_path)}
        found = top_level & WEB_FRAMEWORK_MODULES
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a non-app interface file imports a web framework: {offenders}"


def test_no_streaming_transport_is_used_anywhere_in_the_interface() -> None:
    """WebSockets/SSE are explicitly deferred; prove they are absent."""
    offenders: dict[str, set[str]] = {}
    for source_path in _interface_files():
        modules = _imported_modules(source_path)
        found = {
            module
            for module in modules
            if module.split(".")[0] in {"websockets", "sse_starlette", "socketio"}
            or "websocket" in module.lower()
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.interface reaches for a streaming transport: {offenders}"

    for source_path in _interface_files():
        code = _code_without_comments_or_strings(source_path).lower()
        assert "websocket" not in code, f"{source_path.name} uses a websocket API"


def test_orion_interface_has_no_exec_eval_or_subprocess_calls() -> None:
    """Untrusted transcript text passes through this package; nothing here
    may be capable of turning it into executed behavior."""
    offenders: dict[str, list[str]] = {}
    for source_path in _interface_files():
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
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                found.extend(n for n in names if n.split(".")[0] in DANGEROUS_MODULES)
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.interface contains dangerous calls/imports: {offenders}"


# -------------------------------- Mission 9 invariants, re-checked from here


def test_voice_runtime_remains_free_of_http_and_framework_imports() -> None:
    """`runtime.py` must not learn about the web now that one exists.

    Mission 9 proved it imports no vendor or hardware module; this adds the
    frameworks Phase 2 introduced to the process, which that test could not
    have known about.
    """
    runtime_path = _package_dir(orion.voice) / "runtime.py"
    assert runtime_path.is_file()
    top_level = {module.split(".")[0] for module in _imported_modules(runtime_path)}

    forbidden = top_level & (WEB_FRAMEWORK_MODULES | {"httpx", "httpx2", "requests", "sounddevice"})
    assert not forbidden, f"runtime.py imports an HTTP/framework module: {forbidden}"


def test_no_voice_file_imports_a_web_framework() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_files():
        top_level = {module.split(".")[0] for module in _imported_modules(source_path)}
        found = top_level & WEB_FRAMEWORK_MODULES
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.voice imports a web framework: {offenders}"


def test_httpx_remains_confined_to_the_two_deepgram_provider_files() -> None:
    """Phase 2 must not have widened this Mission 9 boundary to integrate."""
    allowed = {"deepgram_stt.py", "deepgram_tts.py"}
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_files():
        top_level = {module.split(".")[0] for module in _imported_modules(source_path)}
        if "httpx" in top_level and source_path.name not in allowed:
            offenders[source_path.name] = {"httpx"}
    assert not offenders, f"httpx escaped the Deepgram provider files: {offenders}"


def test_sounddevice_remains_confined_to_the_two_audio_io_files() -> None:
    allowed = {"audio_input.py", "audio_output.py"}
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_files():
        if source_path.name in allowed:
            continue
        if "sounddevice" in _code_without_comments_or_strings(source_path):
            offenders[source_path.name] = {"sounddevice"}
    assert not offenders, f"sounddevice escaped the audio I/O files: {offenders}"


def test_the_interface_never_touches_hardware_audio() -> None:
    """An HTTP request must never open the server's microphone or speakers.

    `MicrophoneAudioInput`/`SpeakerAudioOutput` exist and are importable;
    reaching for either from a request handler would let a remote caller
    record or play audio on the machine running ORION.
    """
    for source_path in _interface_files():
        code = _code_without_comments_or_strings(source_path)
        assert "MicrophoneAudioInput" not in code, source_path.name
        assert "SpeakerAudioOutput" not in code, source_path.name
        assert "sounddevice" not in code, source_path.name
