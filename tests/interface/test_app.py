"""The HTTP contract: both turn endpoints, health, and every rejection path.

These are integration tests by intent — the route, the middleware stack,
the service, `VoiceRuntime` and both Deepgram providers all really run;
only the socket is replaced.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from orion.interface.app import create_app
from orion.interface.schemas import MAX_REQUEST_BODY_BYTES
from orion.interface.service import VoiceTurnService
from orion.orchestrator.response import ANALYSIS_UNAVAILABLE_TEXT, GREETING_TEXT

from .conftest import (
    DEEPGRAM_LISTEN_URL,
    SPEECH_WAV,
    UPLOAD_WAV,
    listen_payload,
)

AUDIO_URL = "/v1/voice/turn"
TEXT_URL = "/v1/voice/turn/text"
WAV_HEADERS = {"Content-Type": "audio/wav"}


def test_app_module_imports_and_exposes_a_default_app() -> None:
    """`uvicorn orion.interface.app:app` must resolve without any setup."""
    from orion.interface import app as app_module

    assert app_module.app is not None
    assert app_module.create_app() is not app_module.app


def test_health_reports_ok_and_capability_without_revealing_the_key(
    client: TestClient,
) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "voice_available": True,
        "text_fallback_available": True,
    }


def test_health_reports_voice_unavailable_when_no_credential_is_configured() -> None:
    with TestClient(create_app(VoiceTurnService(""))) as client:
        body = client.get("/health").json()

    assert body["voice_available"] is False
    # True even with no speech credential: a typed turn is answered in
    # writing rather than refused. That is the whole point of the field.
    assert body["text_fallback_available"] is True


# --------------------------------------------------------------- happy paths


def test_text_fallback_returns_a_complete_turn(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    response = client.post(TEXT_URL, json={"text": "hello orion"})

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "hello orion"
    assert body["response_text"] == GREETING_TEXT
    assert body["audio_format"] == "wav"
    assert body["audio_media_type"] == "audio/wav"
    assert body["state"] == "idle"
    assert body["spoken"] is True
    # Phase 5.2 added `analysis`, null on an ordinary turn; this phase adds
    # `spoken`. Kept as an exact set rather than a subset check: the point of
    # this assertion has always been that the response surface cannot grow
    # without someone noticing.
    assert set(body) == {
        "transcript",
        "response_text",
        "spoken",
        "audio_base64",
        "audio_format",
        "audio_media_type",
        "audio_bytes",
        "state",
        "analysis",
    }
    assert body["analysis"] is None


def test_text_fallback_never_calls_speech_to_text(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    """Bypassing STT is the entire point of this path -- prove it is bypassed."""
    client.post(TEXT_URL, json={"text": "hello orion"})

    assert deepgram["listen"].call_count == 0
    assert deepgram["speak"].call_count == 1


def test_audio_turn_returns_the_transcribed_text(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    response = client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "orion analyze gold"
    assert body["response_text"] == ANALYSIS_UNAVAILABLE_TEXT
    assert deepgram["listen"].call_count == 1
    assert deepgram["speak"].call_count == 1


def test_returned_audio_is_the_untranscoded_wav_deepgram_produced(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    """Byte-for-byte identity, not merely 'plays back'.

    Mission 9 pinned Deepgram to linear16/wav/24kHz specifically so local
    playback needs no decoder; silently re-encoding here would undo that
    decision and degrade the audio for no reason.
    """
    body = client.post(TEXT_URL, json={"text": "hello orion"}).json()
    decoded = base64.b64decode(body["audio_base64"])

    assert decoded == SPEECH_WAV
    assert decoded[:4] == b"RIFF"
    assert decoded[8:12] == b"WAVE"
    assert body["audio_bytes"] == len(SPEECH_WAV)


@pytest.mark.parametrize(
    "content_type",
    ["audio/wav", "audio/webm", "audio/ogg", "application/octet-stream", "audio/wav; codecs=1"],
)
def test_accepted_audio_content_types(
    client: TestClient, deepgram: respx.MockRouter, content_type: str
) -> None:
    response = client.post(AUDIO_URL, content=UPLOAD_WAV, headers={"Content-Type": content_type})

    assert response.status_code == 200


# ----------------------------------------------------------- rejection paths


def test_empty_audio_is_rejected_without_contacting_the_vendor(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    response = client.post(AUDIO_URL, content=b"", headers=WAV_HEADERS)

    assert response.status_code == 400
    assert response.json() == {
        "error": "empty_audio",
        "detail": "The request contained no audio.",
    }
    assert deepgram.calls.call_count == 0


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/json", "", "multipart/form-data"]
)
def test_invalid_content_type_is_rejected(
    client: TestClient, deepgram: respx.MockRouter, content_type: str
) -> None:
    response = client.post(AUDIO_URL, content=UPLOAD_WAV, headers={"Content-Type": content_type})

    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"
    assert deepgram.calls.call_count == 0


def test_oversized_body_is_rejected_before_any_voice_work_happens(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    """The ceiling is enforced at the ASGI layer, not by the runtime.

    Nothing downstream should observe the request at all -- no Deepgram
    call, and a 413 rather than the 400/500 a downstream check would give.
    """
    oversized = b"\x00" * (MAX_REQUEST_BODY_BYTES + 1)

    response = client.post(AUDIO_URL, content=oversized, headers=WAV_HEADERS)

    assert response.status_code == 413
    assert response.json()["error"] == "audio_too_large"
    assert deepgram.calls.call_count == 0


def test_request_body_limit_matches_the_mission_9_audio_limit() -> None:
    """One number, not two that can drift apart."""
    from orion.voice.audio_input import MAX_AUDIO_INPUT_BYTES

    assert MAX_REQUEST_BODY_BYTES == MAX_AUDIO_INPUT_BYTES


@pytest.mark.parametrize(
    "payload",
    [
        {"nope": "hello"},
        {"text": 123},
        {"text": "hello", "extra": "forbidden"},
        {},
        [],
        "bare string",
    ],
)
def test_malformed_text_payloads_are_rejected(
    client: TestClient, deepgram: respx.MockRouter, payload: object
) -> None:
    response = client.post(TEXT_URL, json=payload)

    assert response.status_code == 422
    assert response.json()["error"] == "malformed_request"
    assert deepgram.calls.call_count == 0


def test_non_json_body_on_the_text_endpoint_is_rejected(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    response = client.post(TEXT_URL, content=b"\xff\xfe not json", headers=WAV_HEADERS)

    assert response.status_code == 422
    assert response.json()["error"] == "malformed_request"
    assert deepgram.calls.call_count == 0


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("", "empty"),
        ("x" * 2001, "over the Transcript 2000-char limit"),
        ("hello\x00orion", "NUL control character"),
        ("hello\norion", "raw newline"),
        ("\t", "control character only"),
    ],
)
def test_text_rejected_by_mission_9_transcript_rules(
    client: TestClient, deepgram: respx.MockRouter, text: str, why: str
) -> None:
    """The interface declares none of these rules -- Transcript owns them."""
    response = client.post(TEXT_URL, json={"text": text})

    assert response.status_code == 422, why
    assert response.json()["error"] == "invalid_text"
    assert deepgram.calls.call_count == 0


def test_text_at_exactly_the_transcript_limit_is_accepted(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    """The boundary is inclusive, and this layer does not shave it."""
    response = client.post(TEXT_URL, json={"text": "a" * 2000})

    assert response.status_code == 200
    assert response.json()["transcript"] == "a" * 2000


def test_a_spoken_turn_still_needs_a_speech_credential(
    deepgram: respx.MockRouter,
) -> None:
    """The audio endpoint is unchanged: without Deepgram there is no way to
    transcribe what was said, so it still refuses rather than guessing."""
    with TestClient(create_app(VoiceTurnService(""))) as client:
        response = client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)

    assert response.status_code == 503
    assert response.json()["error"] == "voice_unavailable"
    assert deepgram.calls.call_count == 0


def test_a_typed_turn_is_answered_in_writing_without_a_speech_credential(
    deepgram: respx.MockRouter,
) -> None:
    """The change this phase exists for.

    Before it, a deployment with no TTS vendor could not use ORION at all --
    not the analysis, not risk, not macro -- because answering a typed
    question insisted on speaking the answer.
    """
    with TestClient(create_app(VoiceTurnService(""))) as client:
        response = client.post(TEXT_URL, json={"text": "hello orion"})

    assert response.status_code == 200
    body = response.json()
    assert body["spoken"] is False
    assert len(body["response_text"]) > 0
    assert body["transcript"] == "hello orion"
    # Absent, not empty: there is no zero-length base64 to mistake for audio.
    for field in ("audio_base64", "audio_format", "audio_media_type", "audio_bytes"):
        assert body[field] is None
    # The turn ends in IDLE rather than SPEAKING, which is a path Mission 9's
    # state graph already permitted.
    assert body["state"] == "idle"
    assert deepgram.calls.call_count == 0


def test_upstream_failure_becomes_a_clean_gateway_error(
    client: TestClient, deepgram_unreachable: respx.MockRouter
) -> None:
    response = client.post(TEXT_URL, json={"text": "hello orion"})

    assert response.status_code == 502
    assert response.json() == {
        "error": "synthesis_failed",
        "detail": "ORION could not produce a spoken reply.",
    }


def test_speech_to_text_failure_is_reported_separately_from_synthesis(
    client: TestClient,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(DEEPGRAM_LISTEN_URL).mock(
            return_value=httpx.Response(200, json={"results": {"channels": []}})
        )
        response = client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)

    assert response.status_code == 502
    assert response.json()["error"] == "transcription_failed"


def test_no_speech_detected_is_a_failure_not_an_empty_transcript(
    client: TestClient,
) -> None:
    """Mission 9 refuses to fabricate an empty transcript; so does the API."""
    with respx.mock(assert_all_called=False) as router:
        router.post(DEEPGRAM_LISTEN_URL).mock(
            return_value=httpx.Response(200, json=listen_payload(""))
        )
        response = client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)

    assert response.status_code == 502
    assert response.json()["error"] == "transcription_failed"


@pytest.mark.parametrize(("method", "url"), [("GET", AUDIO_URL), ("PUT", TEXT_URL)])
def test_wrong_method_returns_the_standard_error_shape(
    client: TestClient, method: str, url: str
) -> None:
    response = client.request(method, url)

    assert response.status_code == 405
    assert set(response.json()) == {"error", "detail"}


def test_unknown_route_returns_the_standard_error_shape(client: TestClient) -> None:
    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
    assert set(response.json()) == {"error", "detail"}
