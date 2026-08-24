from __future__ import annotations

import traceback

import httpx
import pytest
import respx

from orion.voice.deepgram_stt import MAX_AUDIO_BYTES, DeepgramTranscriptionProvider
from orion.voice.provider_base import TranscriptionProviderError

LISTEN_URL = "https://api.deepgram.com/v1/listen"
FAKE_SECRET = "dg-live-supersecret-1234567890"
AUDIO = b"\x00\x01\x02\x03fake-audio-bytes"


def _provider(api_key: str = "test-key") -> DeepgramTranscriptionProvider:
    return DeepgramTranscriptionProvider(api_key=api_key)


def _success_payload(
    transcript: str = "hello world",
    confidence: float | None = 0.98,
    duration: float | None = 2.5,
    model_name: str | None = "nova-3",
) -> dict[str, object]:
    return {
        "metadata": {
            "duration": duration,
            "model_info": ({"abc-uuid": {"name": model_name}} if model_name is not None else None),
        },
        "results": {
            "channels": [{"alternatives": [{"transcript": transcript, "confidence": confidence}]}]
        },
    }


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(TranscriptionProviderError, match="DEEPGRAM_API_KEY"):
        DeepgramTranscriptionProvider(api_key="")


def test_empty_audio_raises_without_a_request() -> None:
    with pytest.raises(TranscriptionProviderError, match="empty"):
        _provider().transcribe(b"")


def test_oversized_audio_raises_without_a_request() -> None:
    oversized = b"\x00" * (MAX_AUDIO_BYTES + 1)
    with pytest.raises(TranscriptionProviderError, match="exceeding"):
        _provider().transcribe(oversized)


@respx.mock
def test_transcribe_parses_a_successful_response() -> None:
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json=_success_payload()))

    transcript = _provider().transcribe(AUDIO)

    assert transcript.text == "hello world"
    assert transcript.provider == "deepgram"
    assert transcript.model == "nova-3"
    assert transcript.confidence == 0.98
    assert transcript.duration_seconds == 2.5
    assert transcript.transcribed_at.tzinfo is not None


@respx.mock
def test_transcribe_sends_model_and_language_query_params() -> None:
    route = respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json=_success_payload()))

    _provider().transcribe(AUDIO)

    request = route.calls.last.request
    assert request.url.params["model"] == "nova-3"
    assert request.url.params["language"] == "multi"


@respx.mock
def test_transcribe_sends_header_auth_never_query_param() -> None:
    route = respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json=_success_payload()))

    _provider(api_key=FAKE_SECRET).transcribe(AUDIO)

    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Token {FAKE_SECRET}"
    assert FAKE_SECRET not in str(request.url)
    assert all(FAKE_SECRET not in value for _, value in request.url.params.multi_items())


@respx.mock
def test_transcribe_sends_raw_audio_body() -> None:
    route = respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json=_success_payload()))

    _provider().transcribe(AUDIO)

    assert route.calls.last.request.content == AUDIO


@respx.mock
def test_transcribe_raises_on_no_speech_detected() -> None:
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_success_payload(transcript=""))
    )

    with pytest.raises(TranscriptionProviderError, match="no speech"):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_missing_results_field() -> None:
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, json={"metadata": {}}))

    with pytest.raises(TranscriptionProviderError, match="results"):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_empty_channels() -> None:
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json={"metadata": {}, "results": {"channels": []}})
    )

    with pytest.raises(TranscriptionProviderError, match="results.channels"):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_empty_alternatives() -> None:
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(
            200, json={"metadata": {}, "results": {"channels": [{"alternatives": []}]}}
        )
    )

    with pytest.raises(TranscriptionProviderError, match="alternatives"):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_unparseable_json() -> None:
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(200, content=b"not json"))

    with pytest.raises(TranscriptionProviderError):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_http_error() -> None:
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(TranscriptionProviderError):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_invalid_duration_metadata() -> None:
    # A negative duration is a malformed provider response -- Transcript's
    # own `gt=0` field constraint (orion.voice.schemas) must independently
    # reject it, never silently coerced or dropped.
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_success_payload(duration=-1.0))
    )

    with pytest.raises(TranscriptionProviderError, match="failed validation"):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_raises_on_out_of_range_confidence() -> None:
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_success_payload(confidence=1.5))
    )

    with pytest.raises(TranscriptionProviderError, match="failed validation"):
        _provider().transcribe(AUDIO)


@respx.mock
def test_transcribe_does_not_fabricate_model_when_absent() -> None:
    respx.post(LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_success_payload(model_name=None))
    )

    transcript = _provider().transcribe(AUDIO)

    assert transcript.model is None


# --- Credential-leakage regression (mirrors tests/research/test_finnhub_provider.py) ---


@respx.mock
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500, 502, 503])
def test_api_key_never_leaks_on_http_error_response(status_code: int) -> None:
    respx.post(LISTEN_URL).mock(return_value=httpx.Response(status_code))

    try:
        _provider(api_key=FAKE_SECRET).transcribe(AUDIO)
        pytest.fail("expected TranscriptionProviderError")
    except TranscriptionProviderError as exc:
        assert FAKE_SECRET not in str(exc)
        assert FAKE_SECRET not in repr(exc)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert FAKE_SECRET not in rendered
        assert exc.__cause__ is None
        assert exc.__suppress_context__ is True
        assert exc.__context__ is None


@respx.mock
def test_api_key_never_leaks_on_connection_error() -> None:
    respx.post(LISTEN_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    try:
        _provider(api_key=FAKE_SECRET).transcribe(AUDIO)
        pytest.fail("expected TranscriptionProviderError")
    except TranscriptionProviderError as exc:
        assert FAKE_SECRET not in str(exc)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert FAKE_SECRET not in rendered
        assert exc.__context__ is None


@respx.mock
@pytest.mark.parametrize("status_code", [401, 500])
def test_api_key_never_in_request_url_on_error(status_code: int) -> None:
    """Independent of the wrapped-exception checks above: prove the raw
    outgoing request itself never carried the key in the URL, regardless
    of how the response came back."""
    route = respx.post(LISTEN_URL).mock(return_value=httpx.Response(status_code))

    with pytest.raises(TranscriptionProviderError):
        _provider(api_key=FAKE_SECRET).transcribe(AUDIO)

    request = route.calls.last.request
    assert FAKE_SECRET not in str(request.url)
    assert request.headers["Authorization"] == f"Token {FAKE_SECRET}"
