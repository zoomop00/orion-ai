"""Twelve Data implementation of MarketDataProvider.

Free-tier reference: https://twelvedata.com/docs#time-series
No fabricated fallback data — any failure raises MarketDataProviderError.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.data.provider_base import MarketDataProvider, MarketDataProviderError

# ORION uses plain symbols like "XAUUSD"; Twelve Data expects "XAU/USD".
_SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
}


class _TwelveDataValue(BaseModel):
    datetime: str
    open: str
    high: str
    low: str
    close: str
    volume: str | None = None


class _TwelveDataResponse(BaseModel):
    status: str | None = None
    code: int | None = None
    message: str | None = None
    values: list[_TwelveDataValue] | None = None


class TwelveDataProvider(MarketDataProvider):
    """Fetches real OHLCV data from the Twelve Data REST API."""

    def __init__(self, api_key: str, base_url: str, *, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise MarketDataProviderError("TWELVE_DATA_API_KEY is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def fetch_ohlcv(self, symbol: str, timeframe: Timeframe, output_size: int) -> MarketSnapshot:
        provider_symbol = _SYMBOL_MAP.get(symbol, symbol)
        try:
            response = self._client.get(
                f"{self._base_url}/time_series",
                params={
                    "symbol": provider_symbol,
                    "interval": timeframe.value,
                    "outputsize": output_size,
                    "timezone": "UTC",
                    "apikey": self._api_key,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketDataProviderError(f"Twelve Data request failed: {exc}") from exc

        try:
            parsed = _TwelveDataResponse.model_validate(response.json())
        except ValueError as exc:
            raise MarketDataProviderError(
                f"Twelve Data returned an unparseable response: {exc}"
            ) from exc

        if parsed.status == "error" or parsed.values is None:
            raise MarketDataProviderError(
                f"Twelve Data error (code={parsed.code}): {parsed.message or 'no values returned'}"
            )

        bars = [self._to_bar(v) for v in reversed(parsed.values)]  # API returns newest-first

        return MarketSnapshot(
            source=DataSourceMeta(
                provider="twelvedata",
                symbol=symbol,
                timeframe=timeframe,
                retrieved_at=datetime.now(UTC),
            ),
            bars=bars,
        )

    @staticmethod
    def _to_bar(value: _TwelveDataValue) -> OHLCVBar:
        try:
            return OHLCVBar(
                timestamp=datetime.fromisoformat(value.datetime).replace(tzinfo=UTC),
                open=float(value.open),
                high=float(value.high),
                low=float(value.low),
                close=float(value.close),
                volume=float(value.volume) if value.volume not in (None, "") else None,
            )
        except (ValueError, TypeError) as exc:
            raise MarketDataProviderError(
                f"Twelve Data returned an invalid bar: {value!r} ({exc})"
            ) from exc
