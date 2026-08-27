"""Runtime lifecycle: one `VoiceRuntime` per request, shared with nobody.

`VoiceRuntime` documents itself as a single synchronous session object with
no concurrency support, so "a fresh one per request" is not a style
preference — it is the only thing making it safe to serve two requests at
once. These tests assert it structurally (counting constructions) rather
than trusting the call sites.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from orion.interface.service import (
    FAULT_RESPONSES,
    PreparedTranscription,
    VoiceInterfaceError,
    VoiceInterfaceFault,
    VoiceTurnService,
)
from orion.voice.runtime import VoiceRuntime
from orion.voice.schemas import Transcript, VoiceState

from .conftest import UPLOAD_WAV

TEXT_URL = "/v1/voice/turn/text"
AUDIO_URL = "/v1/voice/turn"
WAV_HEADERS = {"Content-Type": "audio/wav"}


@pytest.fixture
def runtimes(monkeypatch: pytest.MonkeyPatch) -> list[VoiceRuntime]:
    """Record every `VoiceRuntime` the service constructs."""
    created: list[VoiceRuntime] = []
    lock = threading.Lock()

    def spy(**kwargs: Any) -> VoiceRuntime:
        runtime = VoiceRuntime(**kwargs)
        with lock:
            created.append(runtime)
        return runtime

    monkeypatch.setattr("orion.interface.service.VoiceRuntime", spy)
    return created


def test_each_request_builds_its_own_runtime(
    client: TestClient, deepgram: respx.MockRouter, runtimes: list[VoiceRuntime]
) -> None:
    for _ in range(4):
        assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 200

    assert len(runtimes) == 4
    assert len({id(runtime) for runtime in runtimes}) == 4


def test_runtime_is_not_retained_on_the_service(
    client: TestClient, deepgram: respx.MockRouter, service: VoiceTurnService
) -> None:
    """Nothing about a turn may survive on the service object."""
    before = dict(vars(service))
    client.post(TEXT_URL, json={"text": "hello"})

    assert vars(service) == before
    assert set(vars(service)) == {"_deepgram_api_key"}


def test_every_runtime_ends_idle_and_none_are_reused(
    client: TestClient, deepgram: respx.MockRouter, runtimes: list[VoiceRuntime]
) -> None:
    client.post(TEXT_URL, json={"text": "hello"})
    client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)

    assert len(runtimes) == 2
    assert all(runtime.state is VoiceState.IDLE for runtime in runtimes)


def test_concurrent_requests_do_not_share_a_runtime(
    client: TestClient, deepgram: respx.MockRouter, runtimes: list[VoiceRuntime]
) -> None:
    """Sixteen overlapping turns, sixteen private state machines.

    A shared runtime would surface as `VoiceRuntimeBusyError` -> HTTP 409
    from whichever request lost the race, so an all-200 result is a real
    signal here, not merely an absence of exceptions.
    """
    request_count = 16

    def one_turn(index: int) -> int:
        response = client.post(TEXT_URL, json={"text": f"turn {index}"})
        return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(one_turn, range(request_count)))

    assert statuses == [200] * request_count
    assert len(runtimes) == request_count
    assert len({id(runtime) for runtime in runtimes}) == request_count


def test_concurrent_requests_return_their_own_transcripts(
    client: TestClient, deepgram: respx.MockRouter
) -> None:
    """Cross-talk would show up as one request receiving another's words."""

    def one_turn(index: int) -> tuple[int, str]:
        response = client.post(TEXT_URL, json={"text": f"utterance {index}"})
        return index, response.json()["transcript"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one_turn, range(16)))

    assert all(transcript == f"utterance {index}" for index, transcript in results)


def test_prepared_transcription_ignores_whatever_audio_it_is_given() -> None:
    """The text path must not smuggle its sentinel byte into a vendor call."""
    transcript = Transcript(
        text="typed by the user",
        provider="text-fallback",
        transcribed_at=datetime.now(UTC),
    )
    provider = PreparedTranscription(transcript)

    assert provider.transcribe(b"") is transcript
    assert provider.transcribe(b"\x00" * 1000) is transcript


def test_service_needs_no_environment_to_construct() -> None:
    """`VoiceTurnService` takes a plain string, like every other provider."""
    assert VoiceTurnService("").voice_available is False
    assert VoiceTurnService("some-key").voice_available is True


def test_every_fault_has_a_response_and_none_are_built_at_runtime() -> None:
    """The mapping is closed and exhaustive over the fault enum."""
    assert set(FAULT_RESPONSES) == set(VoiceInterfaceFault)
    for fault, (status, code, detail) in FAULT_RESPONSES.items():
        assert 400 <= status <= 599
        assert code == fault.value
        assert detail and detail[0].isupper() and detail.endswith(".")


def test_interface_error_cannot_carry_a_message() -> None:
    """There is no parameter through which detail could ever be attached."""
    error = VoiceInterfaceError(VoiceInterfaceFault.TURN_FAILED)

    assert str(error) == "turn_failed"
    with pytest.raises(TypeError):
        VoiceInterfaceError(VoiceInterfaceFault.TURN_FAILED, "extra detail")  # type: ignore[call-arg]
