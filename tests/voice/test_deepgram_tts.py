from __future__ import annotations

import traceback

import httpx
import pytest
import respx

from orion.voice.deepgram_tts import MAX_TEXT_CHARS, DeepgramSpeechSynthesisProvider
from orion.voice.provider_base import SpeechSynthesisProviderError

SPEAK_URL = "https://api.deepgram.com/v1/speak"
FAKE_SECRET = "dg-live-supersecret-1234567890"
FAKE_AUDIO = b"RIFF....WAVEfake-pcm-bytes"


def _provider(api_key: str = "test-key") -> DeepgramSpeechSynthesisProvider:
    return DeepgramSpeechSynthesisProvider(api_key=api_key)


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(SpeechSynthesisProviderError, match="DEEPGRAM_API_KEY"):
        DeepgramSpeechSynthesisProvider(api_key="")


def test_empty_text_raises_without_a_request() -> None:
    with pytest.raises(SpeechSynthesisProviderError, match="empty"):
        _provider().synthesize("")


def test_oversized_text_raises_without_a_request() -> None:
    with pytest.raises(SpeechSynthesisProviderError, match="exceeding"):
        _provider().synthesize("x" * (MAX_TEXT_CHARS + 1))


@respx.mock
def test_synthesize_parses_a_successful_response() -> None:
    respx.post(SPEAK_URL).mock(
        return_value=httpx.Response(200, content=FAKE_AUDIO, headers={"Content-Type": "audio/wav"})
    )

    speech = _provider().synthesize("hello there")

    assert speech.audio == FAKE_AUDIO
    assert speech.provider == "deepgram"
    assert speech.model == "aura-2-thalia-en"
    assert speech.format == "wav"
    assert speech.synthesized_at.tzinfo is not None


@respx.mock
def test_synthesize_sends_model_encoding_container_and_sample_rate_query_params() -> None:
    route = respx.post(SPEAK_URL).mock(return_value=httpx.Response(200, content=FAKE_AUDIO))

    _provider().synthesize("hello there")

    request = route.calls.last.request
    assert request.url.params["model"] == "aura-2-thalia-en"
    assert request.url.params["encoding"] == "linear16"
    assert request.url.params["container"] == "wav"
    assert request.url.params["sample_rate"] == "24000"


@respx.mock
def test_synthesize_sends_header_auth_never_query_param() -> None:
    route = respx.post(SPEAK_URL).mock(return_value=httpx.Response(200, content=FAKE_AUDIO))

    _provider(api_key=FAKE_SECRET).synthesize("hello there")

    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Token {FAKE_SECRET}"
    assert FAKE_SECRET not in str(request.url)
    assert all(FAKE_SECRET not in value for _, value in request.url.params.multi_items())


@respx.mock
def test_synthesize_sends_json_body_with_text() -> None:
    route = respx.post(SPEAK_URL).mock(return_value=httpx.Response(200, content=FAKE_AUDIO))

    _provider().synthesize("hello there")

    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == b'{"text":"hello there"}'


@respx.mock
def test_synthesize_raises_on_empty_audio_response() -> None:
    respx.post(SPEAK_URL).mock(return_value=httpx.Response(200, content=b""))

    with pytest.raises(SpeechSynthesisProviderError, match="empty audio"):
        _provider().synthesize("hello there")


@respx.mock
def test_synthesize_raises_on_http_error() -> None:
    respx.post(SPEAK_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(SpeechSynthesisProviderError):
        _provider().synthesize("hello there")


# --- Credential-leakage regression (mirrors tests/research/test_finnhub_provider.py) ---


@respx.mock
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500, 502, 503])
def test_api_key_never_leaks_on_http_error_response(status_code: int) -> None:
    respx.post(SPEAK_URL).mock(return_value=httpx.Response(status_code))

    try:
        _provider(api_key=FAKE_SECRET).synthesize("hello there")
        pytest.fail("expected SpeechSynthesisProviderError")
    except SpeechSynthesisProviderError as exc:
        assert FAKE_SECRET not in str(exc)
        assert FAKE_SECRET not in repr(exc)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert FAKE_SECRET not in rendered
        assert exc.__cause__ is None
        assert exc.__suppress_context__ is True
        assert exc.__context__ is None


@respx.mock
def test_api_key_never_leaks_on_connection_error() -> None:
    respx.post(SPEAK_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    try:
        _provider(api_key=FAKE_SECRET).synthesize("hello there")
        pytest.fail("expected SpeechSynthesisProviderError")
    except SpeechSynthesisProviderError as exc:
        assert FAKE_SECRET not in str(exc)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert FAKE_SECRET not in rendered
        assert exc.__context__ is None


@respx.mock
@pytest.mark.parametrize("status_code", [401, 500])
def test_api_key_never_in_request_url_on_error(status_code: int) -> None:
    route = respx.post(SPEAK_URL).mock(return_value=httpx.Response(status_code))

    with pytest.raises(SpeechSynthesisProviderError):
        _provider(api_key=FAKE_SECRET).synthesize("hello there")

    request = route.calls.last.request
    assert FAKE_SECRET not in str(request.url)
    assert request.headers["Authorization"] == f"Token {FAKE_SECRET}"
