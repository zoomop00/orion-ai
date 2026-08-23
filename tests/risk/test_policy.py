from __future__ import annotations

import pytest
from pydantic import ValidationError

from orion.analysis.structure import VolatilityLevel
from orion.config import Settings
from orion.risk.policy import KillSwitchState, RiskPolicy


def test_zero_argument_policy_engages_the_kill_switch() -> None:
    # The fail-safe default: a policy built with no arguments blocks trading.
    policy = RiskPolicy()
    assert policy.kill_switch is KillSwitchState.ENGAGED


def test_default_policy_blocks_only_unknown_volatility() -> None:
    policy = RiskPolicy()
    assert policy.blocked_volatility_levels == (VolatilityLevel.UNKNOWN,)


def test_default_min_risk_reward_is_one() -> None:
    assert RiskPolicy().min_risk_reward == 1.0


def test_min_risk_reward_below_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskPolicy(min_risk_reward=0.99)


def test_blocked_volatility_levels_are_canonicalized_regardless_of_input_order() -> None:
    policy_a = RiskPolicy(blocked_volatility_levels=(VolatilityLevel.HIGH, VolatilityLevel.UNKNOWN))
    policy_b = RiskPolicy(blocked_volatility_levels=(VolatilityLevel.UNKNOWN, VolatilityLevel.HIGH))
    assert policy_a.blocked_volatility_levels == policy_b.blocked_volatility_levels


def test_blocked_volatility_levels_are_deduplicated() -> None:
    policy = RiskPolicy(blocked_volatility_levels=(VolatilityLevel.HIGH, VolatilityLevel.HIGH))
    assert policy.blocked_volatility_levels == (VolatilityLevel.HIGH,)


def test_policy_is_frozen() -> None:
    policy = RiskPolicy()
    with pytest.raises(ValidationError):
        policy.kill_switch = KillSwitchState.DISENGAGED  # type: ignore[misc]


def test_unexpected_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RiskPolicy(extra_field="nope")  # type: ignore[call-arg]


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "ANTHROPIC_API_KEY": "k",
        "TWELVE_DATA_API_KEY": "k",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_from_settings_default_kill_switch_is_engaged() -> None:
    policy = RiskPolicy.from_settings(_settings())
    assert policy.kill_switch is KillSwitchState.ENGAGED


def test_from_settings_reads_disengaged() -> None:
    policy = RiskPolicy.from_settings(_settings(ORION_KILL_SWITCH="disengaged"))
    assert policy.kill_switch is KillSwitchState.DISENGAGED


def test_from_settings_invalid_kill_switch_value_raises_at_settings_construction() -> None:
    with pytest.raises(ValidationError):
        _settings(ORION_KILL_SWITCH="off")


def test_from_settings_block_high_volatility_false_by_default() -> None:
    policy = RiskPolicy.from_settings(_settings())
    assert policy.blocked_volatility_levels == (VolatilityLevel.UNKNOWN,)


def test_from_settings_block_high_volatility_true_adds_high() -> None:
    policy = RiskPolicy.from_settings(_settings(ORION_BLOCK_HIGH_VOLATILITY=True))
    assert set(policy.blocked_volatility_levels) == {VolatilityLevel.UNKNOWN, VolatilityLevel.HIGH}


def test_from_settings_thresholds_pass_through() -> None:
    policy = RiskPolicy.from_settings(
        _settings(
            ORION_MAX_STALENESS_BARS=5.0,
            ORION_MAX_DECISION_AGE_BARS=2.0,
            ORION_MIN_RISK_REWARD=1.5,
        )
    )
    assert policy.max_staleness_bars == 5.0
    assert policy.max_decision_age_bars == 2.0
    assert policy.min_risk_reward == 1.5
