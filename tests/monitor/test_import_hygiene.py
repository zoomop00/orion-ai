"""What `orion.monitor` may reach, and where the only loop in ORION lives.

TWO WALLS, BOTH ASSERTED BY PARSING THE SOURCE. Outward: a monitoring pass must
never be one import away from a model, an analyst, the Risk Guardian or an
entry. Inward: the recurring loop and the sleep must exist in exactly one file,
so "everything else does one thing once" stays a fact about the code rather
than a claim in a docstring.

AST, NOT `sys.modules`. A runtime check would pass for a module that imports
something lazily inside a function; these read what the file actually says.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.monitor

#: Nothing in a monitoring pass may reach any of these. `orion.config` and
#: `orion.orchestrator` are here for a different reason from the rest: the
#: monitor takes every collaborator as an argument, which is what lets the
#: caller re-read settings on every pass, and an import of either would be the
#: first step towards caching one.
FORBIDDEN_PREFIXES = (
    "anthropic",
    "orion.reasoning",
    "orion.analysts",
    "orion.risk.guardian",
    "orion.risk.controls",
    "orion.risk.schemas",
    "orion.decision",
    "orion.orchestrator",
    "orion.config",
    "orion.interface",
    "orion.backtest",
    "orion.reflection",
    "orion.thesis",
    "sqlalchemy",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "ccxt",
)

#: The one file allowed to loop or sleep. Named, so adding a second place is a
#: deliberate edit to this list rather than something that slips in.
LOOPING_MODULE = "supervisor.py"

#: Substrings that mean "this file repeats or waits". Matched against the whole
#: source, comments and docstrings included, exactly as
#: `tests/paper/test_session.py` matches its own ban -- prose that describes a
#: loop is close enough to one to be worth failing on.
RECURRENCE_MARKERS = ("time.sleep", "while True", "asyncio", "schedule(")


def _monitor_dir() -> Path:
    path = orion.monitor.__file__
    assert path is not None
    return Path(path).resolve().parent


def _source_files() -> list[Path]:
    return sorted(p for p in _monitor_dir().rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_there_are_monitor_source_files_to_scan() -> None:
    """Guards against this whole suite passing over zero files."""
    assert len(_source_files()) >= 3  # __init__, iteration, supervisor


def test_the_monitor_reaches_no_model_analyst_guardian_or_orchestrator() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _source_files():
        found = {
            module
            for module in _imported_modules(source_path)
            for prefix in FORBIDDEN_PREFIXES
            if module == prefix or module.startswith(f"{prefix}.")
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.monitor imports forbidden modules: {offenders}"


def test_the_supervisor_is_the_only_place_that_loops_or_sleeps() -> None:
    offenders: dict[str, list[str]] = {}
    for source_path in _source_files():
        if source_path.name == LOOPING_MODULE:
            continue
        code = source_path.read_text(encoding="utf-8")
        found = [marker for marker in RECURRENCE_MARKERS if marker in code]
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"a non-supervisor monitor module loops or sleeps: {offenders}"


def test_nothing_outside_the_supervisor_loops_or_sleeps_anywhere_in_src() -> None:
    """The repo-wide half. Every other command does one thing once, and this is
    what keeps that true as the codebase grows."""
    src = _monitor_dir().parent
    offenders: dict[str, list[str]] = {}
    for source_path in sorted(src.rglob("*.py")):
        if "__pycache__" in source_path.parts or source_path.name == LOOPING_MODULE:
            continue
        code = source_path.read_text(encoding="utf-8")
        found = [marker for marker in RECURRENCE_MARKERS if marker in code]
        if found:
            offenders[str(source_path.relative_to(src))] = found
    assert not offenders, f"a loop or sleep exists outside the supervisor: {offenders}"


def _executable_code(source_path: Path) -> str:
    """The file's executable tokens only -- no comments, no docstrings.

    These two tests are about *calls*, and both modules explain in prose what
    they do not do ("starts no thread", "forks nothing"). A raw substring scan
    would flag the explanation, so the same tokenize idiom
    `tests/test_secret_handling.py` uses is used here.
    """
    import tokenize

    skip = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE}
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    if fstring_middle is not None:
        skip.add(fstring_middle)
    with source_path.open("rb") as handle:
        return " ".join(
            token.string for token in tokenize.tokenize(handle.readline) if token.type not in skip
        )


def test_the_iteration_starts_no_thread_and_installs_no_handler() -> None:
    """Only the supervisor may touch signals, and nothing may start a thread:
    a pass runs in the caller's own thread or not at all."""
    iteration = _executable_code(_monitor_dir() / "iteration.py")
    for forbidden in ("threading", "signal", "Thread", "Process", "fork"):
        assert forbidden not in iteration


def test_the_supervisor_starts_no_thread_and_forks_nothing() -> None:
    """`threading.Event` is used for its interruptible wait, never to start
    anything: a supervisor that spawned a worker would be a daemon."""
    supervisor = _executable_code(_monitor_dir() / LOOPING_MODULE)
    for forbidden in ("Thread", "Process", "fork", "subprocess", "multiprocessing"):
        assert forbidden not in supervisor


def test_the_monitor_reaches_the_paper_domain_only_through_its_public_seams() -> None:
    """It may drive the domain; it may not reach around it. `orion.paper` is
    imported for the resolver, the store and the policy -- never for
    `execution`, whose `execute()` opens positions."""
    imported: set[str] = set()
    for source_path in _source_files():
        imported |= _imported_modules(source_path)

    assert "orion.paper.execution" not in imported
    assert any(module.startswith("orion.paper") for module in imported)
