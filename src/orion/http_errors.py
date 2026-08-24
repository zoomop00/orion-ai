"""Turn an `httpx.HTTPError` into a message safe to log — no credentials.

Every REST provider in this codebase authenticates via an API-key query
parameter (Twelve Data's `apikey`, Finnhub's `token`). `httpx.HTTPError.
__str__` (and `httpx.HTTPStatusError.__str__` specifically, raised by
`response.raise_for_status()`) embeds the *full* request URL, query string
included — so naively interpolating `{exc}` into a provider's own error
message leaks the raw API key in plaintext into anything that logs, reports,
or displays that error. Confirmed as a real, reproduced HIGH-severity finding
against both `orion.data.twelvedata_provider.TwelveDataProvider` and
`orion.research.finnhub_provider.FinnhubEconomicCalendarProvider` during a
post-commit audit; see `docs/DECISIONS.md`.

`describe_http_error` is the one place this formatting happens, so a future
third HTTP-based provider does not have to reinvent — or forget — the
redaction. It never echoes the query string, regardless of which parameter
name a given provider happens to use for its key, so this stays correct even
for a provider whose auth parameter isn't `apikey`/`token`.

Callers must raise their wrapping error through `raise_detached` below, not a
bare `raise ... from exc` (or even `raise ... from None` alone) — chaining
the original exception would leave the credential-bearing `httpx.HTTPError`
(and its leaking `str()`) reachable, which default traceback formatting and
most logging setups (e.g. `logging.exception`) print in full.
"""

from __future__ import annotations

from typing import NoReturn

import httpx


def describe_http_error(exc: httpx.HTTPError) -> str:
    """A human-readable, credential-free description of `exc`.

    Only ever built from known-safe fields (exception type, HTTP status
    code/reason phrase, and the request's scheme/host/path with the query
    string dropped entirely) — never from `exc`'s own `str()`/`repr()`,
    which is exactly what leaks the query string in the first place.
    """
    try:
        url = exc.request.url
        location = f"{url.scheme}://{url.host}{url.path}"
    except RuntimeError:
        # `.request` was never attached (e.g. a hand-constructed exception
        # in a test) — still safe, just less specific.
        location = "unknown URL"

    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} {exc.response.reason_phrase} from {location}"
    return f"{type(exc).__name__} while contacting {location}"


def raise_detached(error: Exception) -> NoReturn:
    """Raise `error` with the exception chain fully severed — not merely
    hidden from default formatting.

    `raise error from None` alone sets `error.__cause__ = None` and
    `error.__suppress_context__ = True`, which stops `str`/`repr`/
    `traceback.format_exception`/`logging.exception` from ever *displaying*
    the original exception. It does **not** stop `error.__context__` from
    being set: Python's `raise` statement attaches whatever exception is
    currently being handled to `__context__` unconditionally, regardless of
    a `from` clause — so the original, credential-bearing exception (and its
    own leaking `str()`) stays reachable via `error.__context__` for any
    caller that inspects it directly, even though no standard rendering path
    shows it.

    Clearing `__context__` inside a `finally` block — which runs during
    exception propagation, after the `raise` statement below has already set
    it but before `error` leaves this function — removes that reference
    entirely, verified to survive up to whichever frame ultimately catches
    `error` (it does; exceptions are mutated in place, never copied).
    """
    try:
        raise error from None
    finally:
        error.__context__ = None
