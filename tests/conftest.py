"""Shared pytest fixtures for the ORION test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from orion.config import Settings


@pytest.fixture
def settings() -> Settings:
    """A Settings instance with safe test defaults, isolated from .env / the environment."""
    return Settings(
        _env_file=None,
        ANTHROPIC_API_KEY="test-anthropic-key",
        ANTHROPIC_MODEL="claude-opus-5",
        ANTHROPIC_EFFORT="medium",
        TWELVE_DATA_API_KEY="test-twelvedata-key",
        ORION_DB_PATH=":memory:",
        ORION_KILL_SWITCH="engaged",
        ORION_MAX_STALENESS_BARS=3.0,
        ORION_MAX_DECISION_AGE_BARS=1.0,
        ORION_MIN_RISK_REWARD=1.0,
        ORION_BLOCK_HIGH_VOLATILITY=False,
    )


@pytest.fixture
def fixed_now() -> datetime:
    """A fixed, timezone-aware timestamp for deterministic tests."""
    return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def account_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated working directory, an `ORION_DB_PATH` in it, and a
    market-data credential -- the one key the Mission 9 verbs legitimately need.

    Lives here rather than in one test module because `orion resolve` and
    `orion monitor` both need it, and importing a fixture between test modules
    shadows the name at the import site. Fixtures are lazy, so defining it
    repo-wide costs nothing to the suites that never ask for it.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORION_DB_PATH", str(tmp_path / "orion.db"))
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "td-test-key")
    return tmp_path


@pytest.fixture
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Anthropic credential, and no way to build an Anthropic client.

    The credential is removed so a code path that checked for one takes the
    unconfigured branch; the constructor is poisoned so a code path that
    ignored the check and built a client anyway fails loudly instead of
    silently making a request. Mission 9's guarantee is that neither happens,
    on the one-shot path and on the supervised one alike.
    """
    import anthropic

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("a Mission 9 path constructed an Anthropic client")

    monkeypatch.setattr(anthropic, "Anthropic", explode)
