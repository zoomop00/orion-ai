"""`TwelveDataHistoricalProvider`: a strict range primitive.

The interesting behaviour is what it refuses. A response that cannot be shown
to answer the range asked for is rejected outright rather than returned as
though it were complete -- there is no partial dataset, and no interpolation
anywhere in the acquisition path.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from orion.data.historical import (
    MAX_BARS_PER_REQUEST,
    TwelveDataHistoricalProvider,
)
from orion.data.models import Timeframe
from orion.data.provider_base import MarketDataProviderError

BASE_URL = "https://api.twelvedata.com"
FAKE_SECRET = "sk-live-supersecret-1234567890"
SYMBOL = "XAUUSD"
START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
END = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)


def _provider(api_key: str = "test-key") -> TwelveDataHistoricalProvider:
    return TwelveDataHistoricalProvider(api_key=api_key, base_url=BASE_URL)


def _value(hours: int, price: float = 2400.0) -> dict[str, str]:
    stamp = (START + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "datetime": stamp,
        "open": str(price),
        "high": str(price + 1.0),
        "low": str(price - 1.0),
        "close": str(price),
        "volume": "1000",
    }


def _ok(values: list[dict[str, Any]]) -> httpx.Response:
    # Twelve Data returns newest-first.
    return httpx.Response(200, json={"status": "ok", "values": list(reversed(values))})


# --- the happy path ---


@respx.mock
def test_fetch_range_returns_bars_oldest_first() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(return_value=_ok([_value(h) for h in range(4)]))
    snapshot = _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)
    assert [b.timestamp for b in snapshot.bars] == [START + timedelta(hours=h) for h in range(4)]
    assert snapshot.source.provider == "twelvedata"
    assert snapshot.source.symbol == SYMBOL


@respx.mock
def test_the_request_carries_an_explicit_start_and_end() -> None:
    route = respx.get(f"{BASE_URL}/time_series").mock(return_value=_ok([_value(0)]))
    _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)
    params = route.calls[0].request.url.params
    assert params["start_date"] == "2026-01-05 00:00:00"
    assert params["end_date"] == "2026-01-05 06:00:00"
    assert params["interval"] == "1h"
    assert params["timezone"] == "UTC"
    # ORION's plain symbol is mapped to the vendor's slashed form.
    assert params["symbol"] == "XAU/USD"


@respx.mock
def test_a_well_formed_range_with_no_bars_is_empty_not_an_error() -> None:
    # Emptiness is validate_snapshot's to classify, exactly as for live data.
    respx.get(f"{BASE_URL}/time_series").mock(return_value=_ok([]))
    assert _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END).bars == []


@respx.mock
def test_a_non_utc_range_is_normalised_not_rejected() -> None:
    route = respx.get(f"{BASE_URL}/time_series").mock(return_value=_ok([_value(0)]))
    offset = timezone_plus_five()
    _provider().fetch_range(
        SYMBOL, Timeframe.H1, start=START.astimezone(offset), end=END.astimezone(offset)
    )
    assert route.calls[0].request.url.params["start_date"] == "2026-01-05 00:00:00"


def timezone_plus_five() -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=5))


# --- fail closed ---


@respx.mock
def test_a_response_at_the_request_ceiling_is_refused_as_truncated() -> None:
    values = [_value(h) for h in range(MAX_BARS_PER_REQUEST)]
    respx.get(f"{BASE_URL}/time_series").mock(return_value=_ok(values))
    with pytest.raises(MarketDataProviderError, match="cannot be shown to be complete"):
        _provider().fetch_range(
            SYMBOL, Timeframe.H1, start=START, end=START + timedelta(hours=MAX_BARS_PER_REQUEST)
        )


@respx.mock
def test_a_bar_outside_the_requested_range_is_refused() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(
        return_value=_ok([_value(0), _value(99)])  # far past END
    )
    with pytest.raises(MarketDataProviderError, match="outside the requested range"):
        _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)


@respx.mock
def test_a_vendor_error_body_is_refused() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(
        return_value=httpx.Response(200, json={"status": "error", "code": 400, "message": "bad"})
    )
    with pytest.raises(MarketDataProviderError, match="Twelve Data error"):
        _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)


@respx.mock
def test_an_unparseable_body_is_refused() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(MarketDataProviderError):
        _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)


@respx.mock
def test_an_incoherent_bar_is_refused_rather_than_repaired() -> None:
    bad = _value(0)
    bad["high"] = "2000.0"  # below low
    respx.get(f"{BASE_URL}/time_series").mock(return_value=_ok([bad]))
    with pytest.raises(MarketDataProviderError, match="invalid bar"):
        _provider().fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(MarketDataProviderError):
        TwelveDataHistoricalProvider(api_key="", base_url=BASE_URL)


def test_naive_or_reversed_ranges_are_refused() -> None:
    provider = _provider()
    with pytest.raises(MarketDataProviderError, match="timezone-aware"):
        provider.fetch_range(SYMBOL, Timeframe.H1, start=START.replace(tzinfo=None), end=END)
    with pytest.raises(MarketDataProviderError, match="is before"):
        provider.fetch_range(SYMBOL, Timeframe.H1, start=END, end=START)


# --- the credential never reaches an error ---


@respx.mock
def test_an_http_failure_never_leaks_the_api_key() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(return_value=httpx.Response(429))
    provider = TwelveDataHistoricalProvider(api_key=FAKE_SECRET, base_url=BASE_URL)
    with pytest.raises(MarketDataProviderError) as caught:
        provider.fetch_range(SYMBOL, Timeframe.H1, start=START, end=END)

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert FAKE_SECRET not in str(caught.value)
    assert FAKE_SECRET not in rendered
    assert caught.value.__context__ is None


# --- it agrees with the live provider about vendor trivia ---


def test_the_symbol_map_agrees_with_the_live_provider() -> None:
    # Two modules hold the same two lines of vendor mapping; this is what stops
    # them becoming two different opinions about what a symbol is called.
    from orion.data import historical, twelvedata_provider

    assert historical._SYMBOL_MAP == twelvedata_provider._SYMBOL_MAP
