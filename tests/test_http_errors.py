"""Tests for `orion.http_errors` -- the credential-free httpx error
formatter and chain-detached raise helper. See `docs/DECISIONS.md` for the
audit finding this module exists to close: `httpx.HTTPError.__str__` embeds
the full request URL, including any API-key query parameter, and naively
interpolating it into a provider's own error message leaks the key -- and
even a redacted message alone is not enough, since a bare `raise ... from
None` still leaves the original exception reachable via `__context__`.
"""

from __future__ import annotations

import httpx
import pytest

from orion.http_errors import describe_http_error, raise_detached

FAKE_SECRET = "sk-live-supersecret-1234567890"


def _request_with_secret(url: str = "https://api.example.com/v1/thing") -> httpx.Request:
    return httpx.Request("GET", url, params={"token": FAKE_SECRET})


def test_http_status_error_description_omits_query_string() -> None:
    request = _request_with_secret()
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)

    description = describe_http_error(exc)

    assert FAKE_SECRET not in description
    assert "token" not in description
    assert "500" in description
    assert "api.example.com" in description
    assert "/v1/thing" in description


def test_http_status_error_description_never_contains_raw_str() -> None:
    """The raw httpx-generated str(exc) -- from the real `raise_for_status()`
    code path, not a hand-authored message -- is exactly what leaks the key;
    prove our description is not simply reusing it."""
    request = _request_with_secret()
    response = httpx.Response(429, request=request)
    try:
        response.raise_for_status()
        raise AssertionError("raise_for_status() should have raised")
    except httpx.HTTPStatusError as exc:
        assert FAKE_SECRET in str(exc)  # sanity: confirms httpx itself does leak it
        assert FAKE_SECRET not in describe_http_error(exc)


def test_request_error_description_omits_query_string() -> None:
    request = _request_with_secret()
    exc = httpx.ConnectTimeout("timed out", request=request)

    description = describe_http_error(exc)

    assert FAKE_SECRET not in description
    assert "ConnectTimeout" in description
    assert "api.example.com" in description


def test_request_error_with_no_request_attached_does_not_raise() -> None:
    """A hand-constructed exception with no `.request` set (accessing
    `.request` raises RuntimeError) must still produce a safe description,
    never propagate that RuntimeError."""
    exc = httpx.ConnectTimeout("timed out")

    description = describe_http_error(exc)

    assert FAKE_SECRET not in description
    assert "unknown URL" in description


def test_description_is_deterministic() -> None:
    request = _request_with_secret()
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("boom", request=request, response=response)

    assert describe_http_error(exc) == describe_http_error(exc)


class _Inner(Exception):
    pass


class _Outer(Exception):
    pass


def test_raise_detached_severs_cause_and_context() -> None:
    try:
        try:
            raise _Inner(FAKE_SECRET)
        except _Inner:
            raise_detached(_Outer("safe message"))
    except _Outer as exc:
        assert exc.__cause__ is None
        assert exc.__context__ is None
        assert exc.__suppress_context__ is True


def test_raise_detached_raises_the_given_exception_unchanged() -> None:
    with pytest.raises(_Outer, match="safe message"):
        try:
            raise _Inner("irrelevant")
        except _Inner:
            raise_detached(_Outer("safe message"))


def test_raise_detached_without_an_active_exception_still_works() -> None:
    """Not called from inside an `except` block at all -- there is nothing
    to detach from, but the helper must not require one."""
    with pytest.raises(_Outer):
        raise_detached(_Outer("standalone"))
