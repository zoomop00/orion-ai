"""The one file allowed to read `Settings`, and what it refuses to do.

Constructing the real vendor providers is cheap and offline — an
`httpx.Client` and an `anthropic.Anthropic` are objects, not connections —
so these tests build the genuine article rather than mocking the thing under
test. What they assert is the wiring's judgement: when to decline, what to
rebuild per call, and what it must never quietly create on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orion.config import Settings
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.wiring import analysis_available, build_analysis_service
from orion.paper.store import InMemoryPortfolioStore
from orion.risk.policy import KillSwitchState

from .conftest import make_portfolio


def settings_for(**overrides: object) -> Settings:
    """Settings isolated from `.env` and from the developer's environment."""
    base: dict[str, object] = {
        "_env_file": None,
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "TWELVE_DATA_API_KEY": "test-twelvedata-key",
        "ORION_KILL_SWITCH": "engaged",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestDecliningIsNotFailing:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"ANTHROPIC_API_KEY": ""},
            {"TWELVE_DATA_API_KEY": ""},
            {"ANTHROPIC_API_KEY": "", "TWELVE_DATA_API_KEY": ""},
        ],
    )
    def test_returns_none_when_a_credential_is_missing(self, overrides: dict[str, object]) -> None:
        # `None` rather than a raise: a machine with only a Deepgram key must
        # keep serving ordinary conversational turns exactly as it did before
        # this phase existed.
        assert build_analysis_service(settings_for(**overrides)) is None

    def test_availability_is_a_boolean_and_never_the_key(self) -> None:
        configured = settings_for()
        assert analysis_available(configured) is True
        assert analysis_available(settings_for(ANTHROPIC_API_KEY="")) is False

        # The function returns a bool, not something a key could hide inside.
        assert isinstance(analysis_available(configured), bool)


class TestWhatItBuilds:
    def test_returns_a_usable_service_when_both_credentials_are_present(self) -> None:
        service = build_analysis_service(settings_for())
        assert isinstance(service, AnalysisService)

    def test_builds_a_fresh_service_every_call(self) -> None:
        # Nothing is cached on a module global: the vendor clients inside one
        # service die with it, matching the interface's one-client-per-request
        # posture.
        settings = settings_for()
        first = build_analysis_service(settings)
        second = build_analysis_service(settings)
        assert first is not None and second is not None
        assert first is not second

    def test_the_risk_policy_reflects_the_settings_it_was_handed(self) -> None:
        # The kill switch must be readable from `.env` per request, not
        # frozen at process start — that is the whole reason this is a
        # factory rather than a singleton.
        engaged = build_analysis_service(settings_for(ORION_KILL_SWITCH="engaged"))
        disengaged = build_analysis_service(settings_for(ORION_KILL_SWITCH="disengaged"))
        assert engaged is not None and disengaged is not None

        assert engaged._policy.kill_switch is KillSwitchState.ENGAGED
        assert disengaged._policy.kill_switch is KillSwitchState.DISENGAGED

    def test_passes_a_supplied_portfolio_store_through_untouched(self) -> None:
        store = InMemoryPortfolioStore()
        store.initialize(make_portfolio())
        service = build_analysis_service(settings_for(), portfolio_store=store)

        assert service is not None
        assert service._portfolio_store is store

    def test_defaults_to_no_portfolio_store(self) -> None:
        service = build_analysis_service(settings_for())
        assert service is not None
        assert service._portfolio_store is None


class TestItOpensNoPaperAccount:
    def test_never_creates_a_database_file(self, tmp_path: Path) -> None:
        """`create_memory_engine` runs `metadata.create_all` — it *makes* a
        database. A composition root that did that as a side effect of
        starting up would break the HTTP surface's zero-persistence
        guarantee, so this one does not construct a store at all."""
        db_path = tmp_path / "orion.db"
        build_analysis_service(settings_for(ORION_DB_PATH=str(db_path)))

        assert not db_path.exists()
        assert list(tmp_path.iterdir()) == []

    def test_imports_no_persistence_library(self) -> None:
        import orion.orchestrator.wiring as wiring_module

        source = Path(wiring_module.__file__ or "").read_text(encoding="utf-8")
        # Belt and braces alongside the hygiene test: the words appear in the
        # docstring explaining why they are absent from the code, so this
        # checks the import statements specifically.
        import ast

        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "sqlalchemy" not in imported
        assert imported <= {"__future__", "orion"}
