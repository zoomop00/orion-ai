"""Shared fixtures for the Mission 10 Phase 2 interface tests.

Every test here drives the *real* stack — the real FastAPI app, the real
`VoiceTurnService`, the real `VoiceRuntime`, the real
`DeepgramTranscriptionProvider`/`DeepgramSpeechSynthesisProvider` — with
only the outermost HTTP transport replaced by `respx`, exactly as
`tests/voice/test_deepgram_stt.py` already does. Nothing between the route
handler and the vendor boundary is mocked, so these tests exercise the
integration this phase exists to prove rather than a stand-in for it.

`FAKE_DEEPGRAM_KEY` is deliberately a long, distinctive, greppable string:
several tests assert it appears nowhere in a response body, a header, an
exception rendering, or a traceback, and that assertion is only meaningful
if an accidental leak would actually be visible.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from orion.interface.app import create_app
from orion.interface.limits import TurnLimiter
from orion.interface.service import VoiceTurnService

FAKE_DEEPGRAM_KEY = "dg-test-key-ZZQQ7788-not-a-real-credential"

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"


def build_wav(*, frames: int = 2400, sample_rate: int = 24_000) -> bytes:
    """A real, well-formed RIFF/WAVE file built with the stdlib.

    Used both as the audio a client uploads and as the audio Deepgram
    returns, so the "not transcoded" assertions compare against genuine
    WAV bytes rather than an arbitrary blob.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x01" * frames)
    return buffer.getvalue()


SPEECH_WAV = build_wav()
UPLOAD_WAV = build_wav(frames=240)


def listen_payload(text: str = "orion analyze gold") -> dict[str, object]:
    """A minimal well-formed Deepgram `/v1/listen` response body."""
    return {
        "metadata": {"duration": 1.5, "model_info": {"a": {"name": "nova-3"}}},
        "results": {"channels": [{"alternatives": [{"transcript": text, "confidence": 0.97}]}]},
    }


@pytest.fixture
def service() -> VoiceTurnService:
    return VoiceTurnService(FAKE_DEEPGRAM_KEY)


@pytest.fixture
def client(service: VoiceTurnService) -> Iterator[TestClient]:
    """A client over an app built with an injected, known credential.

    Never `orion.interface.app.app`, which would read the developer's real
    `.env`: these tests must behave identically on a machine that has a
    Deepgram key and one that does not.
    """
    with TestClient(create_app(service)) as test_client:
        yield test_client


@pytest.fixture
def isolation_client(service: VoiceTurnService) -> Iterator[TestClient]:
    """A client whose turn ceiling is raised well above the scenario.

    Two Phase 2 tests prove *runtime isolation* — sixteen overlapping turns
    must produce sixteen private `VoiceRuntime`s and sixteen uncrossed
    transcripts. That property is about the service, not about how many turns
    ORION permits, and it predates Mission 12.1's ceiling by several missions.
    Running them against the default `TurnLimiter` would make them assert the
    limiter instead, so they get one sized for the scenario. Every assertion
    in those tests is unchanged; only the ceiling they run beneath is.

    The default ceiling itself is proven separately, and proven to be exactly
    the module constants, in `tests/interface/test_rate_limit.py`.
    """
    limiter = TurnLimiter(max_concurrent=32, max_per_window=1_000)
    with TestClient(create_app(service, limiter=limiter)) as test_client:
        yield test_client


@pytest.fixture
def deepgram() -> Iterator[respx.MockRouter]:
    """Deepgram answering successfully for both STT and TTS."""
    with respx.mock(assert_all_called=False) as router:
        router.post(DEEPGRAM_LISTEN_URL, name="listen").mock(
            return_value=httpx.Response(200, json=listen_payload())
        )
        router.post(DEEPGRAM_SPEAK_URL, name="speak").mock(
            return_value=httpx.Response(
                200, content=SPEECH_WAV, headers={"content-type": "audio/wav"}
            )
        )
        yield router


@pytest.fixture
def deepgram_unreachable() -> Iterator[respx.MockRouter]:
    """Deepgram refusing every call, the way a bad credential would."""
    with respx.mock(assert_all_called=False) as router:
        router.post(DEEPGRAM_LISTEN_URL, name="listen").mock(
            return_value=httpx.Response(401, json={"err_msg": "Invalid credentials."})
        )
        router.post(DEEPGRAM_SPEAK_URL, name="speak").mock(
            return_value=httpx.Response(401, json={"err_msg": "Invalid credentials."})
        )
        yield router
