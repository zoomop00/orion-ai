"""Vendor acquisition of historical OHLCV bars by explicit date range.

WHY THIS IS NOT `MarketDataProvider`. `orion.data.provider_base.
MarketDataProvider.fetch_ohlcv(symbol, timeframe, output_size)` answers "give
me the most recent N bars" — the live-fetch access pattern three callers now
depend on (`orion.orchestrator.analysis`, `orion.monitor.iteration`, and the
CLI through them). Acquiring history is a structurally different query, "give
me every bar between two dates", and widening the live interface to carry it
would complicate an audited contract for a use case it never has. That contract
is untouched by this module; this is a sibling beside it, not a replacement.

WHY IT IS ALSO NOT `orion.backtest.data.HistoricalBarSource`. That ABC has the
same method name and shape, deliberately, but it describes a source of bars
*already held* — in memory, or in a file on disk. This one describes a network
fetch. Keeping them as two interfaces is what lets `orion.backtest` stay free
of every vendor, HTTP client and credential: a hygiene test asserts that
package imports no `httpx` and no vendor SDK, and it does not import this
module either. The two halves meet at a CSV file on disk, never in a process —
see `orion.datasets`. That file boundary is also what makes a backtest
reproducible: it replays a fixed artifact, not a live call whose answer can
change between runs.

FAIL CLOSED, AND ONLY ON WHAT CAN BE PROVEN. A vendor response is rejected when
it cannot be shown to answer the question asked: a transport or HTTP failure, a
vendor-reported error, an unparseable body, a bar outside the requested range,
or a response that hit the request's own size ceiling and therefore cannot be
shown to be complete. It is never repaired, back-filled, interpolated or
truncated into something that looks whole — the same prohibition
`orion.data.provider_base.MarketDataProviderError` already carries for live
data, and the reason this module raises that same error rather than inventing a
parallel one: the failure is identical in kind (a provider could not return
real market data), and only the access pattern differs.

WHAT IT DELIBERATELY DOES NOT CHECK. Whether the returned bars are ordered,
unique, or contiguous is `orion.data.validation.validate_snapshot`'s
classification job at consumption time, exactly as it is for live data — and
contiguity in particular cannot be judged here at all, because a legitimate
market closure is indistinguishable from missing bars without the declared
`orion.data.sessions.TradingSession` the *consumer* supplies. This module
records what the vendor gave; it does not grade it.

The API key is sent as a query parameter and never reaches an error message:
failures are described through `orion.http_errors.describe_http_error` and
raised via `raise_detached`, for the reasons that module documents in full.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

from orion.data.models import DataSourceMeta, MarketSnapshot, OHLCVBar, Timeframe
from orion.data.provider_base import MarketDataProviderError
from orion.http_errors import describe_http_error, raise_detached

#: Twelve Data's documented ceiling for `outputsize` on `/time_series`. Used as
#: the request size *and* as the truncation test: a response holding exactly
#: this many bars may have been cut off at the ceiling, and a range that cannot
#: be shown to be complete is refused rather than returned as if it were.
MAX_BARS_PER_REQUEST = 5000

#: ORION uses plain symbols like "XAUUSD"; Twelve Data expects "XAU/USD". The
#: same mapping `orion.data.twelvedata_provider` applies, restated here rather
#: than imported across that module's boundary: it is two lines of vendor
#: trivia, and a test asserts the two stay in agreement so this cannot drift
#: into a second, different opinion about what a symbol is called.
_SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
}

_TWELVE_DATA_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class HistoricalMarketDataProvider(ABC):
    """A vendor source of historical OHLCV bars, queryable by date range."""

    @abstractmethod
    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        """Fetch every bar the vendor holds with `start <= timestamp <= end`.

        Must raise `MarketDataProviderError` — never return partial,
        fabricated or interpolated bars — if the request fails, if the vendor
        reports an error, or if the response cannot be shown to cover the
        whole requested range. A well-formed range the vendor genuinely has no
        bars for is not an error: it returns an empty `MarketSnapshot`, and
        classifying "empty" as unusable is `orion.data.validation.
        validate_snapshot`'s job, exactly as it is for live data.
        """
        raise NotImplementedError


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


class TwelveDataHistoricalProvider(HistoricalMarketDataProvider):
    """Fetches a historical range from Twelve Data's `/time_series` endpoint.

    One request per call, deliberately: this is a strict primitive, not a
    paging engine. A range wider than `MAX_BARS_PER_REQUEST` bars is refused
    rather than silently stitched, because stitching is where a gap gets
    quietly introduced and a caller cannot see it happen. Acquiring a longer
    history is done by asking for consecutive sub-ranges and is the caller's
    explicit, visible decision.
    """

    def __init__(self, api_key: str, base_url: str, *, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise MarketDataProviderError("TWELVE_DATA_API_KEY is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)

    def fetch_range(
        self, symbol: str, timeframe: Timeframe, *, start: datetime, end: datetime
    ) -> MarketSnapshot:
        if start.tzinfo is None or end.tzinfo is None:
            raise MarketDataProviderError("start/end must be timezone-aware datetimes")
        if end < start:
            raise MarketDataProviderError(
                f"end ({end.isoformat()}) is before start ({start.isoformat()})"
            )

        provider_symbol = _SYMBOL_MAP.get(symbol, symbol)
        start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
        try:
            response = self._client.get(
                f"{self._base_url}/time_series",
                params={
                    "symbol": provider_symbol,
                    "interval": timeframe.value,
                    "start_date": start_utc.strftime(_TWELVE_DATA_TIME_FORMAT),
                    "end_date": end_utc.strftime(_TWELVE_DATA_TIME_FORMAT),
                    "outputsize": MAX_BARS_PER_REQUEST,
                    "timezone": "UTC",
                    "apikey": self._api_key,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise_detached(
                MarketDataProviderError(
                    f"Twelve Data range request failed: {describe_http_error(exc)}"
                )
            )

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

        # The API returns newest-first; reversing yields oldest-first, matching
        # every other MarketSnapshot in ORION. Nothing is sorted beyond that
        # reversal -- if the vendor's own ordering is wrong, the snapshot will
        # faithfully show it and validate_snapshot will classify it.
        bars = [_to_bar(value) for value in reversed(parsed.values)]

        if len(bars) >= MAX_BARS_PER_REQUEST:
            raise MarketDataProviderError(
                f"Twelve Data returned {len(bars)} bars for {symbol}/{timeframe.value} between "
                f"{start_utc.isoformat()} and {end_utc.isoformat()}, at or above the "
                f"{MAX_BARS_PER_REQUEST}-bar request ceiling; the range may be truncated and "
                "cannot be shown to be complete. Request narrower sub-ranges instead -- a "
                "possibly-truncated range is never returned as if it were whole"
            )

        outside = [bar for bar in bars if not (start_utc <= bar.timestamp <= end_utc)]
        if outside:
            raise MarketDataProviderError(
                f"Twelve Data returned {len(outside)} bar(s) outside the requested range "
                f"[{start_utc.isoformat()}, {end_utc.isoformat()}], first at "
                f"{outside[0].timestamp.isoformat()}; the response does not answer the "
                "question asked and is refused rather than filtered down to the part that does"
            )

        return MarketSnapshot(
            source=DataSourceMeta(
                provider="twelvedata",
                symbol=symbol,
                timeframe=timeframe,
                retrieved_at=datetime.now(UTC),
            ),
            bars=bars,
        )


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
