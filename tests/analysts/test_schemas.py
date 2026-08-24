"""Tests for `orion.analysts.schemas` -- construction invariants, closed
schema enforcement, and the structural guarantee that no field can encode a
trade instruction, provenance the model didn't compute, or executable
content.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orion.analysts.schemas import (
    MacroAnalystOutput,
    MacroAnalystVerdict,
    MacroEventAssessment,
    MacroPolicyPressure,
    MarketRelevance,
    SurpriseDirection,
)

BASE_TIME = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
_EVENT_ID_A = "a" * 64
_EVENT_ID_B = "b" * 64


def _assessment(event_id: str = _EVENT_ID_A, **overrides: object) -> MacroEventAssessment:
    defaults: dict[str, object] = {
        "event_id": event_id,
        "surprise": SurpriseDirection.ABOVE_ESTIMATE,
        "note": "actual above estimate",
    }
    defaults.update(overrides)
    return MacroEventAssessment(**defaults)


def _verdict(**overrides: object) -> MacroAnalystVerdict:
    defaults: dict[str, object] = {
        "policy_pressure": MacroPolicyPressure.TIGHTENING_PRESSURE,
        "market_relevance": MarketRelevance.HIGH,
        "confidence": 0.7,
        "key_event_assessments": [_assessment()],
        "rationale": "CPI came in above estimate.",
    }
    defaults.update(overrides)
    return MacroAnalystVerdict(**defaults)


def _output(**overrides: object) -> MacroAnalystOutput:
    defaults: dict[str, object] = {
        "policy_pressure": MacroPolicyPressure.TIGHTENING_PRESSURE,
        "market_relevance": MarketRelevance.HIGH,
        "confidence": 0.7,
        "key_event_assessments": [_assessment()],
        "rationale": "CPI came in above estimate.",
        "model": "claude-opus-5-20260101",
        "prompt_version": "2026-08-24.1",
        "input_fingerprint": "c" * 64,
        "source_provider": "finnhub",
        "query_start": BASE_TIME,
        "query_end": BASE_TIME,
        "retrieved_at": BASE_TIME,
        "analyzed_at": BASE_TIME,
    }
    defaults.update(overrides)
    return MacroAnalystOutput(**defaults)


# --- MacroEventAssessment ---


def test_event_assessment_constructs_with_valid_fields() -> None:
    assessment = _assessment()
    assert assessment.surprise is SurpriseDirection.ABOVE_ESTIMATE


def test_event_assessment_rejects_bad_event_id_shape() -> None:
    with pytest.raises(ValidationError):
        _assessment(event_id="not-a-hash")


def test_event_assessment_rejects_blank_note() -> None:
    with pytest.raises(ValidationError):
        _assessment(note="")


def test_event_assessment_rejects_oversized_note() -> None:
    with pytest.raises(ValidationError):
        _assessment(note="x" * 161)


def test_event_assessment_is_frozen() -> None:
    assessment = _assessment()
    with pytest.raises(ValidationError):
        assessment.note = "changed"  # type: ignore[misc]


def test_event_assessment_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        MacroEventAssessment(
            event_id=_EVENT_ID_A,
            surprise=SurpriseDirection.IN_LINE,
            note="x",
            unexpected="nope",
        )  # type: ignore[call-arg]


# --- MacroAnalystVerdict ---


def test_verdict_constructs_with_valid_fields() -> None:
    verdict = _verdict()
    assert verdict.policy_pressure is MacroPolicyPressure.TIGHTENING_PRESSURE


def test_verdict_is_frozen() -> None:
    verdict = _verdict()
    with pytest.raises(ValidationError):
        verdict.confidence = 0.9  # type: ignore[misc]


def test_verdict_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        MacroAnalystVerdict(
            policy_pressure=MacroPolicyPressure.MIXED,
            market_relevance=MarketRelevance.LOW,
            confidence=0.5,
            key_event_assessments=[_assessment()],
            rationale="x",
            recommended_action="BUY",
        )  # type: ignore[call-arg]


def test_verdict_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        _verdict(confidence=1.5)
    with pytest.raises(ValidationError):
        _verdict(confidence=-0.1)


def test_verdict_rejects_blank_rationale() -> None:
    with pytest.raises(ValidationError):
        _verdict(rationale="")


def test_verdict_rejects_oversized_rationale() -> None:
    with pytest.raises(ValidationError):
        _verdict(rationale="x" * 501)


def test_verdict_accepts_rationale_at_exactly_the_cap() -> None:
    verdict = _verdict(rationale="x" * 500)
    assert len(verdict.rationale) == 500


def test_verdict_rejects_empty_key_event_assessments() -> None:
    with pytest.raises(ValidationError):
        _verdict(key_event_assessments=[])


def test_verdict_rejects_more_than_fifty_key_event_assessments() -> None:
    too_many = [_assessment(event_id=f"{i:064x}") for i in range(51)]
    with pytest.raises(ValidationError):
        _verdict(key_event_assessments=too_many)


def test_verdict_rejects_duplicate_event_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate event_id"):
        _verdict(key_event_assessments=[_assessment(_EVENT_ID_A), _assessment(_EVENT_ID_A)])


def test_verdict_accepts_distinct_event_ids() -> None:
    verdict = _verdict(key_event_assessments=[_assessment(_EVENT_ID_A), _assessment(_EVENT_ID_B)])
    assert len(verdict.key_event_assessments) == 2


def test_verdict_has_no_field_capable_of_encoding_a_trade_instruction() -> None:
    """Structural proof, not a convention: no field name on the model-
    authored schema resembles an action/direction/execution parameter."""
    forbidden_substrings = (
        "action",
        "buy",
        "sell",
        "direction",
        "position",
        "size",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "leverage",
        "order",
    )
    field_names = set(MacroAnalystVerdict.model_fields)
    for name in field_names:
        lowered = name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, f"field {name!r} looks like a trade instruction"


def test_verdict_has_no_provenance_fields() -> None:
    """The model has no field through which to self-report model/prompt_
    version/input_fingerprint -- these exist only on MacroAnalystOutput,
    stamped by the provider."""
    field_names = set(MacroAnalystVerdict.model_fields)
    assert "model" not in field_names
    assert "prompt_version" not in field_names
    assert "input_fingerprint" not in field_names


# --- MacroAnalystOutput ---


def test_output_constructs_with_valid_fields() -> None:
    output = _output()
    assert output.model == "claude-opus-5-20260101"
    assert output.input_fingerprint == "c" * 64


def test_output_is_frozen() -> None:
    output = _output()
    with pytest.raises(ValidationError):
        output.model = "other"  # type: ignore[misc]


def test_output_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _output(unexpected="nope")


def test_output_rejects_bad_input_fingerprint_shape() -> None:
    with pytest.raises(ValidationError):
        _output(input_fingerprint="not-a-hash")


def test_output_still_enforces_verdict_invariants() -> None:
    """MacroAnalystOutput(MacroAnalystVerdict) must inherit the duplicate-
    event_id check, not just the extra provenance fields."""
    with pytest.raises(ValidationError, match="duplicate event_id"):
        _output(key_event_assessments=[_assessment(_EVENT_ID_A), _assessment(_EVENT_ID_A)])
