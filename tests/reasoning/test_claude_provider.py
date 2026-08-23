from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest
from pydantic import TypeAdapter

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.analysis.indicators import IndicatorSnapshot
from orion.analysis.structure import StructureSnapshot, TrendDirection, VolatilityLevel
from orion.data.models import Timeframe
from orion.reasoning.claude_provider import PROMPT_VERSION, ClaudeReasoningProvider
from orion.reasoning.provider_base import ReasoningProviderError
from orion.reasoning.schemas import (
    Action,
    Direction,
    ReasonCode,
    ReasoningVerdict,
    RiskLevel,
)

_API_KEY = "test-anthropic-key"
_MODEL = "claude-opus-5"


def _evidence() -> Evidence:
    return Evidence(
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        as_of=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        last_price=2400.0,
        bar_count=60,
        indicators=IndicatorSnapshot(
            ema_fast=2405.0,
            ema_slow=2390.0,
            sma_slow=2380.0,
            rsi=62.0,
            atr=8.5,
            ema_fast_length=20,
            ema_slow_length=50,
            sma_length=50,
            rsi_length=14,
            atr_length=14,
        ),
        structure=StructureSnapshot(
            trend=TrendDirection.BULLISH,
            volatility=VolatilityLevel.MEDIUM,
            nearest_support=2385.0,
            nearest_resistance=2415.0,
            swing_high_count=3,
            swing_low_count=2,
        ),
    )


def _verdict(**overrides: object) -> ReasoningVerdict:
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
    return ReasoningVerdict(**defaults)


def _mock_client() -> MagicMock:
    """A bare MagicMock standing in for anthropic.Anthropic.

    respx (used to mock TwelveDataProvider's httpx.Client) cannot intercept
    this SDK: anthropic 1.x is built on the separate httpx2 package. Injecting
    a mock client is the documented, supported seam instead — see
    ClaudeReasoningProvider.__init__'s `client` parameter.
    """
    return MagicMock()


def _provider(mock_client: MagicMock) -> ClaudeReasoningProvider:
    return ClaudeReasoningProvider(
        api_key=_API_KEY,
        model=_MODEL,
        effort="medium",
        client=cast(anthropic.Anthropic, mock_client),
    )


def _respond(
    parsed_output: ReasoningVerdict | None,
    *,
    model: str = "claude-opus-5-20260101",
    stop_reason: str = "end_turn",
) -> SimpleNamespace:
    return SimpleNamespace(parsed_output=parsed_output, model=model, stop_reason=stop_reason)


def _http_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _http_response(status_code: int) -> httpx2.Response:
    return httpx2.Response(status_code, request=_http_request(), json={"error": "boom"})


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(ReasoningProviderError):
        ClaudeReasoningProvider(api_key="", model=_MODEL, effort="medium")


def test_missing_model_raises_immediately() -> None:
    with pytest.raises(ReasoningProviderError):
        ClaudeReasoningProvider(api_key=_API_KEY, model="", effort="medium")


def test_valid_buy_output() -> None:
    verdict = _verdict(
        action=Action.BUY,
        direction=Direction.LONG,
        confidence=0.82,
        reason_codes=[ReasonCode.BULLISH_TREND, ReasonCode.MOMENTUM_CONFIRMS],
        rationale="Uptrend confirmed by momentum with room before resistance.",
        stop_loss=2385.0,
        take_profit=2420.0,
    )
    client = _mock_client()
    client.messages.parse.return_value = _respond(verdict)

    output = _provider(client).reason(_evidence())

    assert output.action is Action.BUY
    assert output.direction is Direction.LONG
    assert output.confidence == 0.82
    assert output.reason_codes == [ReasonCode.BULLISH_TREND, ReasonCode.MOMENTUM_CONFIRMS]
    assert output.stop_loss == 2385.0
    assert output.take_profit == 2420.0


def test_valid_sell_output() -> None:
    verdict = _verdict(
        action=Action.SELL,
        direction=Direction.SHORT,
        confidence=0.7,
        risk_level=RiskLevel.HIGH,
        reason_codes=[ReasonCode.BEARISH_TREND, ReasonCode.RESISTANCE_NEARBY],
        rationale="Rejected at resistance with bearish momentum.",
        stop_loss=2415.0,
        take_profit=2385.0,
    )
    client = _mock_client()
    client.messages.parse.return_value = _respond(verdict)

    output = _provider(client).reason(_evidence())

    assert output.action is Action.SELL
    assert output.direction is Direction.SHORT
    assert output.risk_level is RiskLevel.HIGH
    assert output.stop_loss == 2415.0
    assert output.take_profit == 2385.0


def test_valid_wait_output_with_directional_bias() -> None:
    # The brief's own example: WAIT paired with a LONG bias is legitimate.
    verdict = _verdict(
        action=Action.WAIT,
        direction=Direction.LONG,
        confidence=0.78,
        reason_codes=[ReasonCode.BULLISH_TREND, ReasonCode.INSUFFICIENT_RR],
        rationale="Bullish, but risk/reward is not favorable yet.",
    )
    client = _mock_client()
    client.messages.parse.return_value = _respond(verdict)

    output = _provider(client).reason(_evidence())

    assert output.action is Action.WAIT
    assert output.direction is Direction.LONG
    assert output.insufficient_data is False


def test_insufficient_data_output_maps_through() -> None:
    verdict = _verdict(
        action=Action.WAIT,
        insufficient_data=True,
        reason_codes=[ReasonCode.THIN_HISTORY, ReasonCode.INDICATORS_UNAVAILABLE],
        rationale="Not enough bars to compute the indicator set.",
    )
    client = _mock_client()
    client.messages.parse.return_value = _respond(verdict)

    output = _provider(client).reason(_evidence())

    assert output.insufficient_data is True
    assert output.action is Action.WAIT


def test_malformed_model_output_raises_provider_error() -> None:
    """Reproduces the SDK's real failure mode: parse() validates the model's
    text against the schema internally and raises pydantic.ValidationError
    synchronously, rather than returning something falsy."""

    def _raise_malformed(*_args: object, **_kwargs: object) -> None:
        TypeAdapter(ReasoningVerdict).validate_json('{"action": "hold", "bogus": true}')

    client = _mock_client()
    client.messages.parse.side_effect = _raise_malformed

    with pytest.raises(ReasoningProviderError, match="does not satisfy"):
        _provider(client).reason(_evidence())


def test_invalid_confidence_from_model_raises_provider_error() -> None:
    def _raise_invalid_confidence(*_args: object, **_kwargs: object) -> None:
        TypeAdapter(ReasoningVerdict).validate_json(
            '{"action": "wait", "direction": "none", "confidence": 5.0, '
            '"risk_level": "low", "reason_codes": ["no_clear_signal"], '
            '"insufficient_data": false, "rationale": "x"}'
        )

    client = _mock_client()
    client.messages.parse.side_effect = _raise_invalid_confidence

    with pytest.raises(ReasoningProviderError):
        _provider(client).reason(_evidence())


def test_no_parsed_output_raises_provider_error() -> None:
    """parsed_output is silently None on e.g. a refusal — must not leak as None."""
    client = _mock_client()
    client.messages.parse.return_value = _respond(None, stop_reason="refusal")

    with pytest.raises(ReasoningProviderError, match="refusal"):
        _provider(client).reason(_evidence())


@pytest.mark.parametrize(
    ("exc_factory", "match"),
    [
        (
            lambda: anthropic.AuthenticationError(
                "bad key", response=_http_response(401), body=None
            ),
            "API key",
        ),
        (
            lambda: anthropic.PermissionDeniedError(
                "denied", response=_http_response(403), body=None
            ),
            "denied",
        ),
        (
            lambda: anthropic.NotFoundError("no model", response=_http_response(404), body=None),
            "not found",
        ),
        (
            lambda: anthropic.RateLimitError("slow down", response=_http_response(429), body=None),
            "rate limit",
        ),
        (
            lambda: anthropic.BadRequestError(
                "bad request", response=_http_response(400), body=None
            ),
            "rejected",
        ),
    ],
)
def test_anthropic_status_errors_are_wrapped(exc_factory: object, match: str) -> None:
    client = _mock_client()
    client.messages.parse.side_effect = exc_factory()  # type: ignore[operator]

    with pytest.raises(ReasoningProviderError, match=match):
        _provider(client).reason(_evidence())


def test_generic_api_status_error_is_wrapped() -> None:
    client = _mock_client()
    client.messages.parse.side_effect = anthropic.APIStatusError(
        "server exploded", response=_http_response(503), body=None
    )

    with pytest.raises(ReasoningProviderError):
        _provider(client).reason(_evidence())


def test_timeout_is_wrapped() -> None:
    client = _mock_client()
    client.messages.parse.side_effect = anthropic.APITimeoutError(request=_http_request())

    with pytest.raises(ReasoningProviderError, match="timed out"):
        _provider(client).reason(_evidence())


def test_connection_error_is_wrapped() -> None:
    client = _mock_client()
    client.messages.parse.side_effect = anthropic.APIConnectionError(request=_http_request())

    with pytest.raises(ReasoningProviderError, match="Could not reach"):
        _provider(client).reason(_evidence())


def test_model_metadata_is_recorded_from_response_not_config() -> None:
    verdict = _verdict()
    client = _mock_client()
    # The response reports a different (more specific) model string than the
    # one requested — this is the value that must end up on the output.
    client.messages.parse.return_value = _respond(verdict, model="claude-opus-5-20260615")

    output = _provider(client).reason(_evidence())

    assert output.model == "claude-opus-5-20260615"
    assert output.prompt_version == PROMPT_VERSION


def test_reasoning_is_deterministic_for_the_same_response() -> None:
    verdict = _verdict(
        action=Action.BUY, direction=Direction.LONG, stop_loss=2385.0, take_profit=2420.0
    )
    client = _mock_client()
    client.messages.parse.return_value = _respond(verdict)

    provider = _provider(client)
    first = provider.reason(_evidence())
    second = provider.reason(_evidence())

    assert first == second


def test_provider_stamps_the_evidence_fingerprint() -> None:
    """The fingerprint must be computed in Python from the exact Evidence
    object passed to reason() — never asked of or reported by the model.
    Nothing in `_verdict()` / the mocked response supplies it."""
    client = _mock_client()
    client.messages.parse.return_value = _respond(_verdict())
    evidence = _evidence()

    output = _provider(client).reason(evidence)

    assert output.evidence_fingerprint == evidence_fingerprint(evidence)


def test_evidence_fingerprint_changes_with_different_evidence() -> None:
    client = _mock_client()
    client.messages.parse.return_value = _respond(_verdict())
    provider = _provider(client)

    other_evidence = _evidence()
    other_evidence = other_evidence.model_copy(update={"last_price": 9999.0})

    output_a = provider.reason(_evidence())
    output_b = provider.reason(other_evidence)

    assert output_a.evidence_fingerprint != output_b.evidence_fingerprint


def test_evidence_is_passed_through_without_summarization() -> None:
    client = _mock_client()
    client.messages.parse.return_value = _respond(_verdict())

    _provider(client).reason(_evidence())

    call_kwargs = client.messages.parse.call_args.kwargs
    user_content = call_kwargs["messages"][0]["content"]
    assert "XAUUSD" in user_content
    assert "2400.0" in user_content
    assert call_kwargs["model"] == _MODEL
    assert call_kwargs["output_config"] == {"effort": "medium"}
