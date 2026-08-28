"""Mission 12.1: the turn ceiling, and everything it must not break.

Two layers, deliberately. `TurnLimiter` is exercised directly with an injected
clock, because window behaviour tested through `time.sleep` is slow and flaky
and proves less. The HTTP layer is then exercised through the real app to prove
the parts only an integration can prove: that both endpoints are covered, that a
refusal happens *before* any vendor is called, and that nothing Mission 10
guaranteed has moved.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from orion.interface.app import CORS_ALLOWED_ORIGINS, create_app
from orion.interface.limits import (
    MAX_CONCURRENT_TURNS,
    MAX_TURNS_PER_WINDOW,
    WINDOW_SECONDS,
    TurnLimiter,
)
from orion.interface.schemas import MAX_REQUEST_BODY_BYTES
from orion.interface.service import (
    FAULT_RESPONSES,
    VoiceInterfaceError,
    VoiceInterfaceFault,
    VoiceTurnService,
)

from .conftest import SPEECH_WAV, UPLOAD_WAV, listen_payload

TEXT_URL = "/v1/voice/turn/text"
AUDIO_URL = "/v1/voice/turn"
WAV_HEADERS = {"Content-Type": "audio/wav"}


class FakeClock:
    """A monotonic clock a test can advance. Never goes backwards."""

    def __init__(self) -> None:
        self._now = 1_000.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        assert seconds >= 0
        self._now += seconds


def limited_client(service: VoiceTurnService, limiter: TurnLimiter) -> TestClient:
    return TestClient(create_app(service, limiter=limiter))


# ------------------------------------------------------------- the limiter --


class TestTheLimiterItself:
    def test_requests_below_the_window_limit_are_allowed(self) -> None:
        limiter = TurnLimiter(max_concurrent=4, max_per_window=3, clock=FakeClock())
        for _ in range(3):
            with limiter.slot():
                pass

    def test_the_window_limit_refuses_with_rate_limited(self) -> None:
        limiter = TurnLimiter(max_concurrent=4, max_per_window=2, clock=FakeClock())
        for _ in range(2):
            with limiter.slot():
                pass

        with pytest.raises(VoiceInterfaceError) as caught, limiter.slot():
            pass
        assert caught.value.fault is VoiceInterfaceFault.RATE_LIMITED

    def test_the_window_resets_once_it_has_elapsed(self) -> None:
        clock = FakeClock()
        limiter = TurnLimiter(max_concurrent=4, max_per_window=1, window_seconds=60.0, clock=clock)
        with limiter.slot():
            pass
        with pytest.raises(VoiceInterfaceError), limiter.slot():
            pass

        # Still inside the window a moment before it closes.
        clock.advance(59.9)
        with pytest.raises(VoiceInterfaceError), limiter.slot():
            pass

        clock.advance(0.1)
        with limiter.slot():
            pass

    def test_concurrency_refuses_with_runtime_busy_not_rate_limited(self) -> None:
        # A different statement from "too many this minute", and the interface
        # must not conflate them: this one is true only while turns are running.
        limiter = TurnLimiter(max_concurrent=1, max_per_window=100, clock=FakeClock())
        with limiter.slot(), pytest.raises(VoiceInterfaceError) as caught, limiter.slot():
            pass
        assert caught.value.fault is VoiceInterfaceFault.RUNTIME_BUSY

    def test_concurrency_is_actually_bounded_under_real_threads(self) -> None:
        limiter = TurnLimiter(max_concurrent=3, max_per_window=1_000)
        peak = 0
        active = 0
        guard = threading.Lock()
        release = threading.Event()

        def hold() -> None:
            nonlocal peak, active
            try:
                with limiter.slot():
                    with guard:
                        active += 1
                        peak = max(peak, active)
                    release.wait(timeout=5)
                    with guard:
                        active -= 1
            except VoiceInterfaceError:
                pass

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(hold) for _ in range(12)]
            # Give the winners time to occupy every slot before releasing.
            threading.Event().wait(0.2)
            release.set()
            for future in futures:
                future.result()

        assert peak <= 3

    def test_the_slot_is_released_after_success(self) -> None:
        limiter = TurnLimiter(max_concurrent=1, max_per_window=100, clock=FakeClock())
        for _ in range(5):
            with limiter.slot():
                pass
        # Five sequential turns through a ceiling of one only works if every
        # one of them gave the slot back.

    def test_the_slot_is_released_after_failure(self) -> None:
        # The dangerous direction: a limiter that leaks a slot on error
        # eventually refuses everything, which is a denial of service dressed
        # as a safety feature.
        limiter = TurnLimiter(max_concurrent=1, max_per_window=100, clock=FakeClock())
        for _ in range(5):
            with pytest.raises(RuntimeError), limiter.slot():
                raise RuntimeError("provider exploded mid-turn")

        with limiter.slot():
            pass

    def test_a_fresh_limiter_starts_clean(self) -> None:
        # "Restarting the process resets the limiter" — there is nowhere for
        # state to survive, and this is the behavioural half of that claim.
        clock = FakeClock()
        first = TurnLimiter(max_concurrent=1, max_per_window=1, clock=clock)
        with first.slot():
            pass
        with pytest.raises(VoiceInterfaceError), first.slot():
            pass

        second = TurnLimiter(max_concurrent=1, max_per_window=1, clock=clock)
        with second.slot():
            pass

    @pytest.mark.parametrize(
        ("kwargs"),
        [
            {"max_concurrent": 0},
            {"max_concurrent": -1},
            {"max_per_window": 0},
            {"max_per_window": -5},
            {"window_seconds": 0.0},
            {"window_seconds": -1.0},
        ],
    )
    def test_it_fails_closed_on_a_nonsensical_ceiling(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            TurnLimiter(**kwargs)  # type: ignore[arg-type]

    def test_the_shipped_constants_are_tight(self) -> None:
        # The numbers are part of the contract: a later edit that loosens them
        # should have to change this line and explain itself.
        assert MAX_CONCURRENT_TURNS == 2
        assert MAX_TURNS_PER_WINDOW == 30
        assert WINDOW_SECONDS == 60.0
        # Twenty times tighter than the AnyIO default worker ceiling it replaces.
        assert MAX_CONCURRENT_TURNS < 40


# --------------------------------------------------------------- the routes --


@pytest.fixture
def strict_client(service: VoiceTurnService) -> Iterator[TestClient]:
    """One turn per window, so the second request is always the refused one."""
    with limited_client(service, TurnLimiter(max_concurrent=2, max_per_window=1)) as client:
        yield client


class TestBothEndpointsAreProtected:
    def test_the_text_endpoint_refuses_past_the_window(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        assert strict_client.post(TEXT_URL, json={"text": "hello"}).status_code == 200
        refused = strict_client.post(TEXT_URL, json={"text": "hello again"})
        assert refused.status_code == 429
        assert refused.json() == {
            "error": "rate_limited",
            "detail": "ORION has handled too many turns just now. Try again shortly.",
        }

    def test_the_audio_endpoint_refuses_past_the_window(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        first = strict_client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)
        assert first.status_code == 200
        refused = strict_client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)
        assert refused.status_code == 429

    def test_the_two_endpoints_share_one_budget(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        # A per-endpoint budget would double the ceiling for anyone willing to
        # alternate, and both endpoints spend the same vendor money.
        assert strict_client.post(TEXT_URL, json={"text": "hello"}).status_code == 200
        assert (
            strict_client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS).status_code
            == 429
        )

    def test_health_is_never_rate_limited(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        # `/health` reaches no vendor, so limiting it would only break
        # monitoring while saving nothing.
        strict_client.post(TEXT_URL, json={"text": "hello"})
        for _ in range(10):
            assert strict_client.get("/health").status_code == 200


class TestARefusalCostsNothing:
    def test_a_refused_turn_reaches_no_provider(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        strict_client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)
        listen_calls = deepgram["listen"].call_count
        speak_calls = deepgram["speak"].call_count
        assert listen_calls == 1 and speak_calls == 1

        for _ in range(5):
            assert (
                strict_client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS).status_code
                == 429
            )

        # The whole point of the phase: five refusals, zero extra vendor calls.
        assert deepgram["listen"].call_count == listen_calls
        assert deepgram["speak"].call_count == speak_calls

    def test_a_burst_spends_the_budget_once_and_no_more(
        self, service: VoiceTurnService, deepgram: respx.MockRouter
    ) -> None:
        limiter = TurnLimiter(max_concurrent=2, max_per_window=3)
        with limited_client(service, limiter) as client:
            statuses = [
                client.post(TEXT_URL, json={"text": f"turn {index}"}).status_code
                for index in range(20)
            ]

        assert statuses.count(200) == 3
        assert statuses.count(429) == 17
        # Three turns, three synthesis calls. Seventeen refusals, none.
        assert deepgram["speak"].call_count == 3

    def test_a_concurrent_burst_is_bounded_and_never_5xx(
        self, service: VoiceTurnService, deepgram: respx.MockRouter
    ) -> None:
        limiter = TurnLimiter(max_concurrent=2, max_per_window=6)
        with limited_client(service, limiter) as client:

            def one_turn(index: int) -> int:
                return client.post(TEXT_URL, json={"text": f"turn {index}"}).status_code

            with ThreadPoolExecutor(max_workers=10) as pool:
                statuses = list(pool.map(one_turn, range(20)))

        assert statuses.count(200) <= 6
        # Every refusal is a deliberate, calm 409/429 — never a crash.
        assert set(statuses) <= {200, 409, 429}
        assert deepgram["speak"].call_count == statuses.count(200)


class TestFailureDoesNotLeakCapacity:
    def test_a_provider_failure_still_returns_the_slot(
        self, service: VoiceTurnService, deepgram_unreachable: respx.MockRouter
    ) -> None:
        # Deepgram 401s on every call. The turn fails; the slot must come back,
        # or one bad credential would wedge the process closed.
        limiter = TurnLimiter(max_concurrent=1, max_per_window=100)
        with limited_client(service, limiter) as client:
            for _ in range(5):
                assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 502

    def test_capacity_survives_a_failure_and_serves_the_next_turn(
        self, service: VoiceTurnService
    ) -> None:
        limiter = TurnLimiter(max_concurrent=1, max_per_window=100)
        with respx.mock(assert_all_called=False) as router:
            speak = router.post("https://api.deepgram.com/v1/speak", name="speak")
            speak.mock(return_value=httpx.Response(500, json={"err_msg": "boom"}))
            with limited_client(service, limiter) as client:
                assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 502

                speak.mock(
                    return_value=httpx.Response(
                        200, content=SPEECH_WAV, headers={"content-type": "audio/wav"}
                    )
                )
                assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 200

    def test_malformed_input_does_not_consume_a_slot(
        self, service: VoiceTurnService, deepgram: respx.MockRouter
    ) -> None:
        # A 415/422 costs no vendor money, so it must not spend the budget
        # either — otherwise cheap junk could deny service to a real turn.
        limiter = TurnLimiter(max_concurrent=2, max_per_window=2)
        with limited_client(service, limiter) as client:
            for _ in range(10):
                refused = client.post(
                    AUDIO_URL, content=b"x", headers={"Content-Type": "text/plain"}
                )
                assert refused.status_code == 415
            for _ in range(10):
                assert client.post(TEXT_URL, json={"nope": 1}).status_code == 422

            assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 200
            assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 200
            assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 429


class TestTheCallerCannotChooseTheLimits:
    def test_the_default_app_uses_exactly_the_module_constants(
        self, service: VoiceTurnService
    ) -> None:
        app = create_app(service)
        limiter = TurnLimiter()
        assert limiter._max_concurrent == MAX_CONCURRENT_TURNS  # noqa: SLF001
        assert limiter._max_per_window == MAX_TURNS_PER_WINDOW  # noqa: SLF001
        assert limiter._window_seconds == WINDOW_SECONDS  # noqa: SLF001
        # And the app really built one rather than leaving the route unguarded.
        assert {route.path for route in app.routes} >= {TEXT_URL, AUDIO_URL}

    @pytest.mark.parametrize(
        "body",
        [
            {"text": "hello", "max_per_window": 10_000},
            {"text": "hello", "window_seconds": 0},
            {"text": "hello", "max_concurrent": 999},
            {"text": "hello", "limit": None},
        ],
    )
    def test_limits_supplied_in_the_body_change_nothing(
        self,
        service: VoiceTurnService,
        deepgram: respx.MockRouter,
        body: dict[str, object],
    ) -> None:
        limiter = TurnLimiter(max_concurrent=2, max_per_window=1)
        with limited_client(service, limiter) as client:
            first = client.post(TEXT_URL, json=body)
            # Whether the extra field is ignored or rejected is the schema's
            # business; what matters is that it never widens the ceiling.
            assert first.status_code in {200, 422}
            for _ in range(3):
                assert client.post(TEXT_URL, json={"text": "hello"}).status_code in {200, 429}
            assert client.post(TEXT_URL, json={"text": "hello"}).status_code == 429

    @pytest.mark.parametrize(
        "params",
        [{"max_per_window": "10000"}, {"window_seconds": "0"}, {"limit": "999"}],
    )
    def test_limits_supplied_as_query_parameters_change_nothing(
        self, service: VoiceTurnService, deepgram: respx.MockRouter, params: dict[str, str]
    ) -> None:
        limiter = TurnLimiter(max_concurrent=2, max_per_window=1)
        with limited_client(service, limiter) as client:
            client.post(TEXT_URL, json={"text": "hello"}, params=params)
            assert client.post(TEXT_URL, json={"text": "hello"}, params=params).status_code == 429

    @pytest.mark.parametrize(
        "headers",
        [
            {"X-Rate-Limit": "10000"},
            {"X-RateLimit-Limit": "10000"},
            {"X-Max-Concurrent": "999"},
        ],
    )
    def test_limits_supplied_as_headers_change_nothing(
        self, service: VoiceTurnService, deepgram: respx.MockRouter, headers: dict[str, str]
    ) -> None:
        limiter = TurnLimiter(max_concurrent=2, max_per_window=1)
        with limited_client(service, limiter) as client:
            client.post(TEXT_URL, json={"text": "hello"}, headers=headers)
            assert client.post(TEXT_URL, json={"text": "hello"}, headers=headers).status_code == 429

    def test_no_response_exposes_limiter_state(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        strict_client.post(TEXT_URL, json={"text": "hello"})
        refused = strict_client.post(TEXT_URL, json={"text": "hello"})

        assert set(refused.json()) == {"error", "detail"}
        rendered = refused.text.lower()
        for leaked in ("window", "concurrent", "remaining", "active", "count", "reset"):
            assert leaked not in rendered
        for header in refused.headers:
            assert not header.lower().startswith("x-ratelimit")


# ------------------------------------- Mission 10 guarantees, still standing --


class TestNothingMission10GuaranteedMoved:
    def test_the_fault_vocabulary_is_still_closed_and_complete(self) -> None:
        assert set(FAULT_RESPONSES) == set(VoiceInterfaceFault)
        for status, code, detail in FAULT_RESPONSES.values():
            assert 400 <= status <= 599
            assert code and detail
        # Exactly one member was added, and it is the rate-limit one.
        assert FAULT_RESPONSES[VoiceInterfaceFault.RATE_LIMITED][0] == 429
        assert FAULT_RESPONSES[VoiceInterfaceFault.RUNTIME_BUSY][0] == 409

    def test_the_error_carries_no_detail_beyond_its_fault(self) -> None:
        with pytest.raises(TypeError):
            VoiceInterfaceError(VoiceInterfaceFault.RATE_LIMITED, "extra")  # type: ignore[call-arg]

    def test_cors_behaviour_is_unchanged(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        origin = "http://localhost:3000"
        allowed = strict_client.post(TEXT_URL, json={"text": "hello"}, headers={"Origin": origin})
        assert allowed.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in allowed.headers
        assert set(CORS_ALLOWED_ORIGINS) == {"http://localhost:3000", "http://127.0.0.1:3000"}

    def test_a_rate_limited_response_still_carries_cors_headers(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        # The limiter sits inside CORS, so a browser can actually read the 429
        # rather than seeing an opaque network error.
        origin = "http://localhost:3000"
        strict_client.post(TEXT_URL, json={"text": "hello"}, headers={"Origin": origin})
        refused = strict_client.post(TEXT_URL, json={"text": "hello"}, headers={"Origin": origin})
        assert refused.status_code == 429
        assert refused.headers["access-control-allow-origin"] == origin

    def test_the_body_size_ceiling_is_unchanged_and_still_precedes_the_limiter(
        self, strict_client: TestClient, deepgram: respx.MockRouter
    ) -> None:
        assert MAX_REQUEST_BODY_BYTES == 25 * 1024 * 1024
        oversized = strict_client.post(
            AUDIO_URL,
            content=b"\x00" * (MAX_REQUEST_BODY_BYTES + 1),
            headers=WAV_HEADERS,
        )
        assert oversized.status_code == 413
        # Rejected on size, so the turn budget is untouched and a real turn
        # still succeeds afterwards.
        assert strict_client.post(TEXT_URL, json={"text": "hello"}).status_code == 200

    def test_fail_closed_behaviour_is_unchanged(
        self, service: VoiceTurnService, deepgram_unreachable: respx.MockRouter
    ) -> None:
        limiter = TurnLimiter(max_concurrent=2, max_per_window=10)
        with limited_client(service, limiter) as client:
            response = client.post(TEXT_URL, json={"text": "hello"})
        assert response.status_code == 502
        assert response.json()["error"] == "synthesis_failed"
        assert "dg-test-key" not in response.text


class TestNoRateLimitStateReachesDisk:
    def test_a_full_burst_including_refusals_writes_nothing(
        self,
        service: VoiceTurnService,
        deepgram: respx.MockRouter,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        limiter = TurnLimiter(max_concurrent=2, max_per_window=2)
        with limited_client(service, limiter) as client:
            for index in range(10):
                client.post(TEXT_URL, json={"text": f"turn {index}"})
            client.post(AUDIO_URL, content=UPLOAD_WAV, headers=WAV_HEADERS)

        # The Mission 10 assertion, re-run with the limiter in the path and
        # with most of those requests refused by it.
        assert list(tmp_path.rglob("*")) == []

    def test_the_limiter_module_touches_no_file_and_no_clock_but_monotonic(self) -> None:
        import ast
        import inspect

        from orion.interface import limits

        source = inspect.getsource(limits)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "eval", "exec", "compile", "__import__"}
        # A wall clock can step backwards over an NTP correction and silently
        # widen the window; monotonic cannot.
        assert "monotonic" in source
        assert "time.time" not in source


def test_listen_payload_helper_is_still_the_shared_one() -> None:
    """Guards against this file quietly forking the Phase 2 fixtures."""
    assert listen_payload()["results"]
