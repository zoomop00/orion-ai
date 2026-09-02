"""Static proof of `orion.analysts`'s isolation -- not "we didn't wire it
up," but "the import doesn't exist."

Two directions, both AST-parsed (never a `sys.modules` runtime check, which
would be contaminated by whatever else the test process happens to have
already imported):

1. Outbound: `orion.analysts` itself imports `anthropic` in exactly one file
   (`claude_macro_provider.py`), and never imports the decision or execution
   chain (`orion.decision`, `orion.risk`, `orion.paper`, `orion.memory`,
   `orion.reflection`, `orion.reasoning`, `orion.backtest`) or a persistence
   library (`sqlalchemy`).
2. Inbound: no Mission 0-7 package imports `orion.analysts` -- analyst output
   reaches no decision path, and the Risk Guardian cannot see one. Mission 8
   v2.2 added `orion.thesis` to that list: it is the schema package the storage
   and reflection layers reach a `MarketThesis` through, so it must not itself
   be a route to an analyst.

MISSION 8 v2 NARROWED THE OUTBOUND RULE RATHER THAN DROPPING IT.
`orion.analysis` was on the banned list under the heading "downstream layers",
which was a misclassification: `orion.analysis` is *upstream* of everything --
pure computation over bars, importing only `orion.data` -- and the Macro
Analyst simply had no reason to read it. The Technical Analyst is a specialist
over exactly that computation, and the alternative to letting it read
`orion.analysis` was reimplementing every indicator inside this package, which
is worse by every measure this repo cares about.

So the ban became a *per-file allowlist* naming the three upstream modules
`technical.py`, `technical_schemas.py` and `synthesis.py` may read, in the same
style `tests/orchestrator/test_import_hygiene.py` uses for `wiring.py`. It is
narrower than what it replaced for every other file in the package: the macro
files still cannot reach `orion.analysis` at all, and no file may reach it
package-wide. The direction is upstream-only and acyclic --
`orion.analysts` -> `orion.analysis` -> `orion.data` -- and the inbound rule
below still forbids `orion.analysis` from importing back, so the arrow cannot
reverse. Every ban that actually protects the Risk Guardian's authority --
`orion.decision`, `orion.risk`, `orion.paper`, `orion.memory`,
`orion.reflection`, `orion.backtest` -- is untouched, and two more
(`orion.orchestrator`, `orion.interface`) were added.

Also proves the "rationale/note is never consumed downstream" requirement
structurally: no file anywhere in `orion.analysts` calls `exec`, `eval`,
`compile`, or a subprocess/shell function -- the only thing that could turn
model-authored free text into executed behavior.
"""

from __future__ import annotations

import ast
from pathlib import Path

import orion.analysts

DISALLOWED_TOP_LEVEL_MODULES = {
    # AI reasoning -- only claude_macro_provider.py may import this.
    "anthropic",
    # persistence -- Mission 8 v1 is explicitly zero-persistence.
    "sqlalchemy",
}

ANTHROPIC_ALLOWED_FILE = "claude_macro_provider.py"

DISALLOWED_ORION_SUBPACKAGES = {
    # Upstream, and reachable only through the per-file allowlist below.
    "orion.analysis",
    # The decision and execution chain. These are the bans that matter: an
    # analyst able to reach any of them could reach the thing that authorises
    # a trade. None has ever been relaxed and none may be.
    "orion.reasoning",
    "orion.decision",
    "orion.risk",
    "orion.paper",
    "orion.memory",
    "orion.reflection",
    "orion.backtest",
    # A composition root and an HTTP layer are not an analyst's business
    # either. Stated here rather than assumed; neither was imported before and
    # neither is now. `orion.config` is deliberately absent: the Macro Analyst
    # provider has read `Settings` for its model and effort since Mission 8 v1,
    # and banning it here would be an unrelated tightening rather than part of
    # this seam.
    "orion.orchestrator",
    "orion.interface",
}

#: The per-file carve-out, named module by module rather than package by
#: package. The Technical Analyst is a specialist over deterministic market
#: computation, so it reads that computation instead of repeating it; nothing
#: else in this package may, and no file may reach `orion.analysis` wholesale.
#: See this module's docstring for why the blanket ban became this.
_ALLOWED_EXTRA_ORION_IMPORTS_BY_FILE: dict[str, set[str]] = {
    "technical.py": {
        "orion.analysis.evidence",
        "orion.analysis.price_action",
        "orion.analysis.structure",
    },
    "technical_schemas.py": {"orion.analysis.structure"},
    "synthesis.py": {"orion.analysis.structure"},
}

#: The read-only view helpers the allowlist admits, spelled out so a reviewer
#: can see there is no engine, no store and no provider among them: `evidence`
#: builds and fingerprints Evidence, `structure` and `price_action` are pure
#: functions over bars.
ALLOWED_ANALYSIS_MODULES = {
    "orion.analysis.evidence",
    "orion.analysis.price_action",
    "orion.analysis.structure",
}

DANGEROUS_CALL_NAMES = {"exec", "eval", "compile"}
DANGEROUS_MODULES = {"subprocess", "os"}

MISSION_0_7_PACKAGES = [
    "data",
    "analysis",
    "reasoning",
    "decision",
    "risk",
    "paper",
    "memory",
    "reflection",
    "backtest",
    "research",
    # Mission 8 v2.2. `orion.thesis` holds the `MarketThesis` schema so that
    # `orion.paper`, `orion.memory` and `orion.reflection` can name one without
    # importing an analyst. That only closes the wall if `orion.thesis` cannot
    # reach an analyst either -- otherwise the ban is enforced at the front door
    # and open at the side. This entry is what keeps the transitive path shut.
    "thesis",
]


def _analysts_package_dir() -> Path:
    path = orion.analysts.__file__
    assert path is not None
    return Path(path).parent


def _repo_src_dir() -> Path:
    return _analysts_package_dir().parent


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


def _analysts_source_files() -> list[Path]:
    return sorted(_analysts_package_dir().rglob("*.py"))


def test_orion_analysts_package_has_source_files_to_scan() -> None:
    assert len(_analysts_source_files()) >= 3


def test_anthropic_is_imported_only_by_claude_macro_provider() -> None:
    offenders: list[str] = []
    for source_path in _analysts_source_files():
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        if "anthropic" in modules and source_path.name != ANTHROPIC_ALLOWED_FILE:
            offenders.append(source_path.name)
    assert not offenders, f"anthropic imported outside {ANTHROPIC_ALLOWED_FILE}: {offenders}"


def test_orion_analysts_has_no_disallowed_top_level_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _analysts_source_files():
        modules = {m.split(".")[0] for m in _imported_modules(source_path)}
        found = modules & DISALLOWED_TOP_LEVEL_MODULES
        # anthropic is separately governed (single-file allowlist) above.
        found -= {"anthropic"}
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysts imports disallowed modules: {offenders}"


def test_orion_analysts_has_no_downstream_layer_imports() -> None:
    offenders: dict[str, set[str]] = {}
    for source_path in _analysts_source_files():
        allowed = _ALLOWED_EXTRA_ORION_IMPORTS_BY_FILE.get(source_path.name, set())
        imported = _imported_modules(source_path)
        found = {
            m
            for m in imported
            if any(m == pkg or m.startswith(pkg + ".") for pkg in DISALLOWED_ORION_SUBPACKAGES)
            and m not in allowed
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysts imports downstream layers: {offenders}"


def test_the_decision_and_execution_chain_is_banned_from_every_file() -> None:
    """The half of the ban above that has no carve-out and must never get one.

    The per-file allowlist admits three read-only `orion.analysis` modules and
    nothing else, so this restates the important guarantee separately: not one
    file in this package -- allowlisted or not -- may reach the reasoning
    provider, the decision engine, the Risk Guardian, the paper account, the
    journal, reflection, the backtest loop, the orchestrator or the HTTP layer.
    An analyst annotates; it never decides, and it never sees what does.
    """
    never_allowed = DISALLOWED_ORION_SUBPACKAGES - {"orion.analysis"}
    offenders: dict[str, set[str]] = {}
    for source_path in _analysts_source_files():
        found = {
            m
            for m in _imported_modules(source_path)
            if any(m == pkg or m.startswith(pkg + ".") for pkg in never_allowed)
        }
        if found:
            offenders[source_path.name] = found
    assert not offenders, f"orion.analysts reaches the decision chain: {offenders}"


def test_only_the_technical_files_read_orion_analysis() -> None:
    """The carve-out is exactly as wide as it was meant to be, and no wider.

    Checked in both directions, like the orchestrator's own config carve-out:
    an allowlisted file that stops needing the permission should lose it, and a
    file that was never granted one must not quietly acquire it. The Macro
    Analyst in particular still cannot see `orion.analysis` at all.
    """
    for source_path in _analysts_source_files():
        reads_analysis = {
            m for m in _imported_modules(source_path) if m.startswith("orion.analysis")
        }
        allowed = _ALLOWED_EXTRA_ORION_IMPORTS_BY_FILE.get(source_path.name, set())
        if allowed:
            assert reads_analysis, (
                f"{source_path.name} is allowlisted for orion.analysis but does not use it; "
                "the permission should be deleted rather than left standing"
            )
        assert reads_analysis <= allowed, (
            f"{source_path.name} reads orion.analysis modules it is not allowlisted for: "
            f"{reads_analysis - allowed}"
        )
        assert reads_analysis <= ALLOWED_ANALYSIS_MODULES, (
            f"{source_path.name} reads an orion.analysis module outside the read-only set: "
            f"{reads_analysis - ALLOWED_ANALYSIS_MODULES}"
        )


def test_no_analysts_file_imports_orion_analysis_wholesale() -> None:
    """A bare `import orion.analysis` satisfies no allowlist entry and is a
    doorway rather than a seam."""
    for source_path in _analysts_source_files():
        assert "orion.analysis" not in _imported_modules(source_path), (
            f"{source_path.name} imports the orion.analysis package wholesale"
        )


def test_orion_analysts_has_no_exec_eval_or_subprocess_calls() -> None:
    """The only way model-authored free text (rationale/note) could become
    executed behavior is if something in this package fed it to exec/eval/
    a subprocess. Proven absent, not merely believed absent."""
    offenders: dict[str, list[str]] = {}
    for source_path in _analysts_source_files():
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
    assert not offenders, f"orion.analysts contains dangerous calls/imports: {offenders}"


def test_no_mission_0_7_package_imports_orion_analysts() -> None:
    offenders: dict[str, set[str]] = {}
    for pkg_name in MISSION_0_7_PACKAGES:
        pkg_dir = _repo_src_dir() / pkg_name
        if not pkg_dir.is_dir():
            continue
        for source_path in sorted(pkg_dir.rglob("*.py")):
            imported = _imported_modules(source_path)
            found = {
                m for m in imported if m == "orion.analysts" or m.startswith("orion.analysts.")
            }
            if found:
                offenders[str(source_path.relative_to(_repo_src_dir().parent))] = found
    assert not offenders, f"a Mission 0-7 package imports orion.analysts: {offenders}"
