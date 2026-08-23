from __future__ import annotations

import pytest
from pydantic import ValidationError

from orion.reasoning.schemas import (
    Action,
    Direction,
    ReasonCode,
    ReasoningOutput,
    ReasoningVerdict,
    RiskLevel,
)

_DUMMY_FINGERPRINT = "a" * 64


def _verdict_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "action": Action.WAIT,
        "direction": Direction.NONE,
        "confidence": 0.5,
        "risk_level": RiskLevel.MEDIUM,
        "reason_codes": [ReasonCode.NO_CLEAR_SIGNAL],
        "insufficient_data": False,
        "rationale": "No clear edge in the current evidence.",
    }
    defaults.update(overrides)
    return defaults


def test_valid_verdict_constructs() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs())
    assert verdict.action is Action.WAIT


def test_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(confidence=1.5))


def test_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(confidence=-0.1))


def test_invalid_action_enum_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(action="hold"))


def test_invalid_reason_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(reason_codes=["made_up_code"]))


def test_insufficient_data_requires_wait() -> None:
    with pytest.raises(ValidationError, match="insufficient_data"):
        ReasoningVerdict(
            **_verdict_kwargs(action=Action.BUY, direction=Direction.LONG, insufficient_data=True)
        )


def test_insufficient_data_with_non_wait_action_is_rejected() -> None:
    with pytest.raises(ValidationError, match="insufficient_data"):
        ReasoningVerdict(
            **_verdict_kwargs(action=Action.SELL, direction=Direction.SHORT, insufficient_data=True)
        )


def test_insufficient_data_with_wait_is_valid() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs(action=Action.WAIT, insufficient_data=True))
    assert verdict.insufficient_data is True


def test_buy_with_long_is_valid() -> None:
    verdict = ReasoningVerdict(
        **_verdict_kwargs(
            action=Action.BUY, direction=Direction.LONG, stop_loss=2390.0, take_profit=2420.0
        )
    )
    assert verdict.action is Action.BUY
    assert verdict.direction is Direction.LONG


def test_buy_with_short_is_rejected() -> None:
    with pytest.raises(ValidationError, match="BUY requires LONG"):
        ReasoningVerdict(**_verdict_kwargs(action=Action.BUY, direction=Direction.SHORT))


def test_buy_with_none_is_rejected() -> None:
    with pytest.raises(ValidationError, match="BUY requires LONG"):
        ReasoningVerdict(**_verdict_kwargs(action=Action.BUY, direction=Direction.NONE))


def test_sell_with_short_is_valid() -> None:
    verdict = ReasoningVerdict(
        **_verdict_kwargs(
            action=Action.SELL, direction=Direction.SHORT, stop_loss=2420.0, take_profit=2390.0
        )
    )
    assert verdict.action is Action.SELL
    assert verdict.direction is Direction.SHORT


def test_buy_requires_stop_loss_and_take_profit() -> None:
    with pytest.raises(ValidationError, match="require both"):
        ReasoningVerdict(**_verdict_kwargs(action=Action.BUY, direction=Direction.LONG))


def test_sell_requires_stop_loss_and_take_profit() -> None:
    with pytest.raises(ValidationError, match="require both"):
        ReasoningVerdict(**_verdict_kwargs(action=Action.SELL, direction=Direction.SHORT))


def test_buy_stop_loss_must_be_below_take_profit() -> None:
    with pytest.raises(ValidationError, match="not below take_profit"):
        ReasoningVerdict(
            **_verdict_kwargs(
                action=Action.BUY, direction=Direction.LONG, stop_loss=2420.0, take_profit=2390.0
            )
        )


def test_sell_stop_loss_must_be_above_take_profit() -> None:
    with pytest.raises(ValidationError, match="not above take_profit"):
        ReasoningVerdict(
            **_verdict_kwargs(
                action=Action.SELL, direction=Direction.SHORT, stop_loss=2390.0, take_profit=2420.0
            )
        )


def test_wait_allows_null_stop_loss_and_take_profit() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs(action=Action.WAIT))
    assert verdict.stop_loss is None
    assert verdict.take_profit is None


def test_sell_with_long_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SELL requires SHORT"):
        ReasoningVerdict(**_verdict_kwargs(action=Action.SELL, direction=Direction.LONG))


def test_sell_with_none_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SELL requires SHORT"):
        ReasoningVerdict(**_verdict_kwargs(action=Action.SELL, direction=Direction.NONE))


def test_wait_with_long_is_valid() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs(action=Action.WAIT, direction=Direction.LONG))
    assert verdict.direction is Direction.LONG


def test_wait_with_short_is_valid() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs(action=Action.WAIT, direction=Direction.SHORT))
    assert verdict.direction is Direction.SHORT


def test_wait_with_none_is_valid() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs(action=Action.WAIT, direction=Direction.NONE))
    assert verdict.direction is Direction.NONE


def test_duplicate_reason_codes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ReasoningVerdict(
            **_verdict_kwargs(reason_codes=[ReasonCode.BULLISH_TREND, ReasonCode.BULLISH_TREND])
        )


def test_empty_reason_codes_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(reason_codes=[]))


def test_too_many_reason_codes_are_rejected() -> None:
    codes = list(ReasonCode)[:7]
    assert len(codes) == 7
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(reason_codes=codes))


def test_rationale_too_long_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(rationale="x" * 401))


def test_verdict_is_frozen() -> None:
    verdict = ReasoningVerdict(**_verdict_kwargs())
    with pytest.raises(ValidationError):
        verdict.action = Action.BUY  # type: ignore[misc]


def test_unexpected_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReasoningVerdict(**_verdict_kwargs(), extra_field="not allowed")  # type: ignore[call-arg]


def test_reasoning_output_carries_provenance() -> None:
    output = ReasoningOutput(
        **_verdict_kwargs(),
        model="claude-opus-5-20260101",
        prompt_version="2026-08-23.1",
        evidence_fingerprint=_DUMMY_FINGERPRINT,
    )
    assert output.model == "claude-opus-5-20260101"
    assert output.prompt_version == "2026-08-23.1"
    assert output.evidence_fingerprint == _DUMMY_FINGERPRINT
    # Still enforces the same invariants as its parent.
    assert output.action is Action.WAIT


def test_reasoning_output_evidence_fingerprint_must_be_64_hex_chars() -> None:
    with pytest.raises(ValidationError):
        ReasoningOutput(
            **_verdict_kwargs(),
            model="claude-opus-5-20260101",
            prompt_version="2026-08-23.1",
            evidence_fingerprint="not-a-hash",
        )


def test_reasoning_output_also_enforces_wait_invariant() -> None:
    with pytest.raises(ValidationError, match="insufficient_data"):
        ReasoningOutput(
            **_verdict_kwargs(
                action=Action.SELL, direction=Direction.SHORT, insufficient_data=True
            ),
            model="claude-opus-5-20260101",
            prompt_version="2026-08-23.1",
            evidence_fingerprint=_DUMMY_FINGERPRINT,
        )


def test_reasoning_output_also_enforces_direction_invariant() -> None:
    with pytest.raises(ValidationError, match="SELL requires SHORT"):
        ReasoningOutput(
            **_verdict_kwargs(action=Action.SELL, direction=Direction.LONG),
            model="claude-opus-5-20260101",
            evidence_fingerprint=_DUMMY_FINGERPRINT,
            prompt_version="2026-08-23.1",
        )
