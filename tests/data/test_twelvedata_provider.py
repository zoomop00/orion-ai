from __future__ import annotations

import httpx
import pytest
import respx

from orion.data.models import Timeframe
from orion.data.provider_base import MarketDataProviderError
from orion.data.twelvedata_provider import TwelveDataProvider

BASE_URL = "https://api.twelvedata.com"


def _provider() -> TwelveDataProvider:
    return TwelveDataProvider(api_key="test-key", base_url=BASE_URL)


def test_missing_api_key_raises_immediately() -> None:
    with pytest.raises(MarketDataProviderError):
        TwelveDataProvider(api_key="", base_url=BASE_URL)


@respx.mock
def test_fetch_ohlcv_parses_a_successful_response() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "values": [
                    # Twelve Data returns newest-first.
                    {
                        "datetime": "2026-08-23 11:00:00",
                        "open": "2401.0",
                        "high": "2405.0",
                        "low": "2399.0",
                        "close": "2403.0",
                        "volume": "1200",
                    },
                    {
                        "datetime": "2026-08-23 10:00:00",
                        "open": "2400.0",
                        "high": "2404.0",
                        "low": "2398.0",
                        "close": "2401.0",
                        "volume": "1000",
                    },
                ],
            },
        )
    )

    snapshot = _provider().fetch_ohlcv("XAUUSD", Timeframe.H1, output_size=2)

    assert snapshot.source.provider == "twelvedata"
    assert snapshot.source.symbol == "XAUUSD"
    assert len(snapshot.bars) == 2
    # Oldest-first after parsing.
    assert snapshot.bars[0].timestamp < snapshot.bars[1].timestamp
    assert snapshot.bars[-1].close == 2403.0


@respx.mock
def test_fetch_ohlcv_raises_on_provider_error_payload() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(
        return_value=httpx.Response(
            200,
            json={"status": "error", "code": 400, "message": "invalid symbol"},
        )
    )

    with pytest.raises(MarketDataProviderError, match="invalid symbol"):
        _provider().fetch_ohlcv("XAUUSD", Timeframe.H1, output_size=2)


@respx.mock
def test_fetch_ohlcv_raises_on_http_error() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(return_value=httpx.Response(500))

    with pytest.raises(MarketDataProviderError):
        _provider().fetch_ohlcv("XAUUSD", Timeframe.H1, output_size=2)


@respx.mock
def test_fetch_ohlcv_raises_on_invalid_bar_values() -> None:
    respx.get(f"{BASE_URL}/time_series").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "values": [
                    {
                        "datetime": "2026-08-23 10:00:00",
                        "open": "not-a-number",
                        "high": "2404.0",
                        "low": "2398.0",
                        "close": "2401.0",
                    },
                ],
            },
        )
    )

    with pytest.raises(MarketDataProviderError):
        _provider().fetch_ohlcv("XAUUSD", Timeframe.H1, output_size=1)
