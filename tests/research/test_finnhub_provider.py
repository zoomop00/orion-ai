from __future__ import annotations

import traceback
from datetime import UTC, datetime

import httpx
import pytest
import respx

from orion.research.finnhub_provider import FinnhubEconomicCalendarProvider
from orion.research.provider_base import EconomicCalendarProviderError

BASE_URL = "https://finnhub.io/api/v1"
START = datetime(2026, 8, 24, 0, 0, 0, tzinfo=UTC)
END = datetime(2026, 8, 24, 23, 59, 59, tzinfo=UTC)
FAKE_SECRET = "sk-live-supersecret-1234567890"


def _provider(api_key: str = "test-key") -> FinnhubEconomicCalendarProvider:
    return FinnhubEconomicCalendarProvider(api_key=api_key, base_url=BASE_URL)


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(EconomicCalendarProviderError):
        FinnhubEconomicCalendarProvider(api_key="", base_url=BASE_URL)


def test_fetch_calendar_end_before_start_raises_without_a_request() -> None:
    with pytest.raises(EconomicCalendarProviderError, match="before start"):
        _provider().fetch_calendar(END, START)


def test_fetch_calendar_requires_timezone_aware_bounds() -> None:
    with pytest.raises(EconomicCalendarProviderError, match="timezone"):
        _provider().fetch_calendar(datetime(2026, 8, 24), END)


@respx.mock
def test_fetch_calendar_parses_a_successful_response() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(
        return_value=httpx.Response(
            200,
            json={
                "economicCalendar": [
                    {
                        "time": "2026-08-24 10:00:00",
                        "country": "US",
                        "event": "CPI YoY",
                        "impact": "high",
                        "actual": 3.1,
                        "estimate": 3.0,
                        "prev": 2.9,
                        "unit": "%",
                    },
                    {
                        "time": "2026-08-24 14:30:00",
                        "country": "US",
                        "event": "Unclassified release",
                        "impact": "",
                        "actual": None,
                        "estimate": None,
                        "prev": None,
                        "unit": None,
                    },
                ]
            },
        )
    )

    snapshot = _provider().fetch_calendar(START, END)

    assert snapshot.source.provider == "finnhub"
    assert snapshot.source.query_start == START
    assert snapshot.source.query_end == END
    assert len(snapshot.events) == 2
    assert snapshot.events[0].event_name == "CPI YoY"
    assert snapshot.events[0].impact.value == "high"
    assert snapshot.events[1].impact.value == "unknown"


@respx.mock
def test_fetch_calendar_raises_on_provider_error_payload() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(
        return_value=httpx.Response(200, json={"error": "invalid token"})
    )

    with pytest.raises(EconomicCalendarProviderError, match="invalid token"):
        _provider().fetch_calendar(START, END)


@respx.mock
def test_fetch_calendar_raises_on_missing_calendar_field() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(EconomicCalendarProviderError, match="economicCalendar"):
        _provider().fetch_calendar(START, END)


@respx.mock
def test_fetch_calendar_raises_on_http_error() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(return_value=httpx.Response(500))

    with pytest.raises(EconomicCalendarProviderError):
        _provider().fetch_calendar(START, END)


@respx.mock
def test_fetch_calendar_raises_on_invalid_impact_value() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(
        return_value=httpx.Response(
            200,
            json={
                "economicCalendar": [
                    {
                        "time": "2026-08-24 10:00:00",
                        "country": "US",
                        "event": "CPI YoY",
                        "impact": "extreme",
                    },
                ]
            },
        )
    )

    with pytest.raises(EconomicCalendarProviderError):
        _provider().fetch_calendar(START, END)


@respx.mock
def test_fetch_calendar_raises_on_missing_event_time() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(
        return_value=httpx.Response(
            200,
            json={
                "economicCalendar": [
                    {"country": "US", "event": "CPI YoY", "impact": "high"},
                ]
            },
        )
    )

    with pytest.raises(EconomicCalendarProviderError):
        _provider().fetch_calendar(START, END)


@respx.mock
def test_fetch_calendar_raises_on_unparseable_json() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(
        return_value=httpx.Response(200, content=b"not json")
    )

    with pytest.raises(EconomicCalendarProviderError):
        _provider().fetch_calendar(START, END)


# --- Credential-leakage regression (HIGH finding from the post-commit audit) ---


@respx.mock
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500, 502, 503])
def test_api_key_never_leaks_on_http_error_response(status_code: int) -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(return_value=httpx.Response(status_code))

    try:
        _provider(api_key=FAKE_SECRET).fetch_calendar(START, END)
        pytest.fail("expected EconomicCalendarProviderError")
    except EconomicCalendarProviderError as exc:
        assert FAKE_SECRET not in str(exc)
        assert FAKE_SECRET not in repr(exc)
        # The full chained/traceback rendering is what a real logging setup
        # (logging.exception, an unhandled-exception printout, Sentry, etc.)
        # actually shows -- proving the raw httpx.HTTPStatusError (whose
        # str() *does* embed the key) is not reachable through __cause__/
        # __context__ in the default-rendered chain.
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert FAKE_SECRET not in rendered
        assert exc.__cause__ is None
        assert exc.__suppress_context__ is True
        assert exc.__context__ is None


@respx.mock
def test_api_key_never_leaks_on_connection_error() -> None:
    respx.get(f"{BASE_URL}/calendar/economic").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    try:
        _provider(api_key=FAKE_SECRET).fetch_calendar(START, END)
        pytest.fail("expected EconomicCalendarProviderError")
    except EconomicCalendarProviderError as exc:
        assert FAKE_SECRET not in str(exc)
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert FAKE_SECRET not in rendered
        assert exc.__context__ is None
