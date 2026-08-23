from __future__ import annotations

from orion.config import Settings


def test_settings_defaults_are_safe_and_configurable() -> None:
    s = Settings(_env_file=None)

    # The AI model and its reasoning depth are independent, overridable settings —
    # never hardcoded constants used directly by application code.
    assert isinstance(s.anthropic_model, str) and s.anthropic_model
    assert s.anthropic_effort in {"low", "medium", "high", "xhigh", "max"}


def test_settings_read_overrides_from_explicit_values() -> None:
    s = Settings(
        _env_file=None,
        ANTHROPIC_MODEL="claude-sonnet-5",
        ANTHROPIC_EFFORT="high",
    )

    assert s.anthropic_model == "claude-sonnet-5"
    assert s.anthropic_effort == "high"
