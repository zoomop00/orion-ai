"""Shared pytest fixtures for the ORION test suite."""

from __future__ import annotations

from datetime import UTC, datetime

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
