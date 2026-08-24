"""Static proof of `orion.voice`'s isolation -- not "we didn't wire it up,"
but "the import doesn't exist."

Mirrors `tests/analysts/test_import_hygiene.py`/`tests/backtest/test_import_
hygiene.py`'s identical AST-based approach exactly: parsing each source
file's own `import`/`from ... import` statements, not inspecting
`sys.modules` at runtime, so this is a property of the package's source, not
of whatever else the rest of the test suite happened to already import.

Five directions:

1. Outbound (vendor/hardware/persistence): every file in `orion.voice`
   except a small, explicit per-file allowlist (`_ALLOWED_EXTRA_IMPORTS_BY_
   FILE` below) must import none of the network/AI-vendor/hardware-I-O/
   persistence/randomness modules `DISALLOWED_TOP_LEVEL_MODULES` lists.
   `httpx` is carved out only for `deepgram_stt.py`/`deepgram_tts.py`
   (Mission 9 Phase 2's approved, single vendor); `sounddevice` only for
   `audio_input.py`/`audio_output.py` (Phase 3's real local audio path).
   Every other file -- `schemas.py`, `provider_base.py`, `response.py`,
   `state.py`, and, most importantly, `runtime.py` itself -- stays
   vendor-and-hardware-independent: `test_runtime_does_not_import_concrete_
   vendor_or_hardware_modules` below proves this for `runtime.py`
   specifically and by name, not just via the generic sweep, since "the
   runtime depends only on the Phase 1 ABCs, never a concrete Deepgram
   class" is an explicit architectural requirement for this phase, not an
   incidental property.
2. Outbound (pipeline): `orion.voice` must import no Mission 0-8 pipeline
   package (data/analysis/research/reasoning/analysts/decision/risk/paper/
   memory/reflection/backtest) -- Phase 3 adds a real runtime but still has
   zero pipeline integration: no memory, no database, no trading, no
   orchestrator. `orion.provenance` is deliberately NOT in this list: it is
   a shared, dependency-free top-level utility (not a Mission-specific
   package) that `orion.voice.schemas` legitimately imports for
   `canonical_hash`, exactly as `orion.analysts`/`orion.decision`/
   `orion.risk` already do.
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
   out of scope for both Phase 2 and Phase 3.
5. Randomness/wall-clock: `uuid`, `random`, and `secrets` are disallowed
   everywhere in `orion.voice` (no file has a legitimate reason to mint an
   id or generate randomness -- content-addressed fingerprints and
   caller/provider-supplied timestamps are this codebase's established
   alternative, see `docs/DECISIONS.md`'s Phase 1 entry).

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
    # network / HTTP -- carved out only for deepgram_stt.py/deepgram_tts.py
    # (see _ALLOWED_EXTRA_IMPORTS_BY_FILE).
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
    # local hardware audio I/O -- carved out only for audio_input.py/
    # audio_output.py (see _ALLOWED_EXTRA_IMPORTS_BY_FILE).
    "sounddevice",
    "pyaudio",
    "simpleaudio",
    # persistence -- orion.voice remains explicitly zero-persistence.
    "sqlalchemy",
    "sqlite3",
    # randomness / id-minting -- see reason 5 in the module docstring.
    "uuid",
    "random",
    "secrets",
}

# Per-file carve-outs from DISALLOWED_TOP_LEVEL_MODULES above. Every module
# NOT listed for a given file stays forbidden even there -- e.g. deepgram_
# stt.py may import httpx but still may not import sounddevice/sqlalchemy/
# uuid/etc.
_ALLOWED_EXTRA_IMPORTS_BY_FILE: dict[str, set[str]] = {
    "deepgram_stt.py": {"httpx"},
    "deepgram_tts.py": {"httpx"},
    "audio_input.py": {"sounddevice"},
    "audio_output.py": {"sounddevice"},
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
        allowed_extra = _ALLOWED_EXTRA_IMPORTS_BY_FILE.get(source_path.name, set())
        found = (modules & DISALLOWED_TOP_LEVEL_MODULES) - allowed_extra
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.voice imports disallowed modules: {offenders}"


def test_only_allowlisted_files_import_their_carved_out_vendor_module() -> None:
    """The inverse of the test above: every module named as a carve-out for
    one file must NOT appear in any *other* file -- `httpx` stays confined
    to the two Deepgram provider files, `sounddevice` to the two audio-I/O
    files, matching `orion.voice.provider_base`/`orion.voice.schemas`/
    `orion.voice.runtime`'s documented vendor-independence."""
    offenders: dict[str, set[str]] = {}
    all_carved_out_modules: set[str] = set()
    for extra in _ALLOWED_EXTRA_IMPORTS_BY_FILE.values():
        all_carved_out_modules |= extra

    for source_path in _voice_source_files():
        allowed_extra = _ALLOWED_EXTRA_IMPORTS_BY_FILE.get(source_path.name, set())
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        found = (modules & all_carved_out_modules) - allowed_extra
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a non-allowlisted orion.voice file imports a vendor module: {offenders}"


def test_deepgram_provider_files_do_not_import_orion_config() -> None:
    """Both concrete Deepgram provider files take `api_key: str` as a
    constructor parameter, exactly like `FinnhubEconomicCalendarProvider`/
    `ClaudeReasoningProvider` -- wiring to `Settings.deepgram_api_key` is
    orchestrator work, out of scope."""
    offenders: dict[str, set[str]] = {}
    for source_path in _voice_source_files():
        if source_path.name not in {"deepgram_stt.py", "deepgram_tts.py"}:
            continue
        imported = _imported_modules(source_path)
        found = {m for m in imported if m == "orion.config" or m.startswith("orion.config.")}
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a Deepgram provider file imports orion.config: {offenders}"


def test_runtime_does_not_import_concrete_vendor_or_hardware_modules() -> None:
    """`orion.voice.runtime.VoiceRuntime` depends only on the ABCs
    (`AudioInput`, `TranscriptionProvider`, `ResponseProvider`,
    `SpeechSynthesisProvider`, `AudioOutput`) -- never `DeepgramTranscriptionProvider`
    /`DeepgramSpeechSynthesisProvider`/`MicrophoneAudioInput`/
    `SpeakerAudioOutput` by name, and never `httpx`/`sounddevice` directly.
    Swapping a concrete implementation must only ever require constructing
    `VoiceRuntime` with different arguments."""
    runtime_path = _voice_package_dir() / "runtime.py"
    assert runtime_path.is_file()
    imported = _imported_modules(runtime_path)
    top_level = {m.split(".")[0] for m in imported}

    forbidden_top_level = top_level & {"httpx", "sounddevice", "deepgram"}
    forbidden_concrete_modules = {
        m
        for m in imported
        if m in {"orion.voice.deepgram_stt", "orion.voice.deepgram_tts"}
        or m.startswith("orion.voice.deepgram_stt.")
        or m.startswith("orion.voice.deepgram_tts.")
    }
    assert not forbidden_top_level, (
        f"runtime.py imports a vendor/hardware module: {forbidden_top_level}"
    )
    assert not forbidden_concrete_modules, (
        f"runtime.py imports a concrete Deepgram module: {forbidden_concrete_modules}"
    )


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
