from __future__ import annotations

import pytest
from pydantic import ValidationError

from orion.config import Settings
from orion.paper.policy import PaperExecutionPolicy


def test_default_starting_balance_is_positive() -> None:
    policy = PaperExecutionPolicy()
    assert policy.starting_balance == 10_000.0


def test_zero_cost_policy_is_constructible() -> None:
    # Zero spread/slippage/fee is a legitimate (if unrealistic) policy --
    # never a risk limit, so nothing here needs a fail-closed floor.
    policy = PaperExecutionPolicy(spread_bps=0.0, slippage_bps=0.0, fee_bps=0.0)
    assert policy.spread_bps == 0.0
    assert policy.slippage_bps == 0.0
    assert policy.fee_bps == 0.0


def test_policy_is_frozen() -> None:
    policy = PaperExecutionPolicy()
    with pytest.raises(ValidationError):
        policy.spread_bps = 5.0  # type: ignore[misc]


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "ANTHROPIC_API_KEY": "k",
        "TWELVE_DATA_API_KEY": "k",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_from_settings_defaults() -> None:
    policy = PaperExecutionPolicy.from_settings(_settings())
    assert policy.starting_balance == 10_000.0
    assert policy.spread_bps == 2.0
    assert policy.slippage_bps == 1.0
    assert policy.fee_bps == 1.0


def test_from_settings_reads_overrides() -> None:
    policy = PaperExecutionPolicy.from_settings(
        _settings(
            PAPER_STARTING_BALANCE=50_000.0,
            PAPER_SPREAD_BPS=5.0,
            PAPER_SLIPPAGE_BPS=2.5,
            PAPER_FEE_BPS=0.5,
        )
    )
    assert policy.starting_balance == 50_000.0
    assert policy.spread_bps == 5.0
    assert policy.slippage_bps == 2.5
    assert policy.fee_bps == 0.5
