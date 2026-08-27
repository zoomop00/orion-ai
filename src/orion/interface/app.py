"""The HTTP surface: three endpoints, two middlewares, four exception
handlers, and a `run()` that can only ever bind to loopback.

    HTTP request
        -> _BodySizeLimitMiddleware   (reject oversized bodies at the socket)
        -> CORSMiddleware             (explicit localhost allowlist, no "*")
        -> route                      (content-type / shape validation)
        -> VoiceTurnService           (fresh VoiceRuntime, per request)
        -> Mission 9 voice pipeline
        -> HTTP response

LOCALHOST ONLY, AND NOT MERELY BY DOCUMENTATION. `run()` hardcodes
`_BIND_HOST = "127.0.0.1"` and exposes no host parameter, so the supported
way to start this server cannot be pointed at `0.0.0.0` by passing an
argument. **This service has no authentication, no authorization, no rate
limiting and no TLS. Exposing it beyond loopback — a public bind address, a
port forward, a reverse proxy, a tunnel, a container port mapping — is NOT
supported and would hand anyone who can reach it unlimited use of ORION's
paid Deepgram credential.** Authentication and production hardening are
explicitly deferred (see `docs/DECISIONS.md`); until they exist, loopback is
the security boundary.

SIZE BEFORE SUBSTANCE. `_BodySizeLimitMiddleware` is the innermost of the
two middlewares but still runs before any route, any Pydantic model and any
voice code. It rejects on a declared `Content-Length` without reading a
byte, and independently counts what actually arrives — a request that lies
about its length, or omits it entirely and streams chunked, is cut off at
the same ceiling. Only then can bytes reach `VoiceTurnService`.

ZERO PERSISTENCE. Nothing in this package opens a file, a database, a
cache or a session. Audio exists as a local variable for the duration of
one request and is unreachable the moment the response is sent. No
transcript, no audio and no request body is ever written to a log: this
module imports no logging framework and calls no logger, and uvicorn's
access log records method, path and status only — which is also why no
endpoint accepts a query parameter, so there is no channel through which
payload could reach that log line even accidentally.

NO SECOND STATE MACHINE. `VoiceTurnResponse.state` is read from Mission 9's
`VoiceStateMachine` after the turn. This package declares no states,
validates no transitions and remembers nothing between requests. Live state
streaming (and therefore WebSockets/SSE) is deliberately absent: a
request/response turn is the smallest thing that proves the path end to
end, and Phase 3 can render progress without the server pushing it.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from orion.config import get_settings
from orion.interface.schemas import (
    ACCEPTED_AUDIO_CONTENT_TYPES,
    MAX_REQUEST_BODY_BYTES,
    ErrorResponse,
    HealthResponse,
    TextTurnRequest,
    VoiceTurnResponse,
)
from orion.interface.service import (
    FAULT_RESPONSES,
    VoiceInterfaceError,
    VoiceInterfaceFault,
    VoiceTurnService,
)

# The only address `run()` will ever bind. Not a parameter, not a setting.
_BIND_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000

# Exactly the origins a local Next.js dev server uses, both spellings of
# loopback. Never "*": a wildcard here would let any page the founder
# happens to have open drive ORION's paid credential, and it is also the
# setting that silently becomes unsafe the moment authentication is added.
CORS_ALLOWED_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def _fault_response(fault: VoiceInterfaceFault) -> JSONResponse:
    """Render a fault through the one closed mapping in `service`."""
    status_code, code, detail = FAULT_RESPONSES[fault]
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=code, detail=detail).model_dump(),
    )


class _BodyTooLargeError(Exception):
    """Internal signal raised from the wrapped `receive`. Never escapes."""


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class _BodySizeLimitMiddleware:
    """Enforce a request-body ceiling before the application sees the body.

    Pure ASGI rather than `BaseHTTPMiddleware` on purpose: only at this
    level can `receive` be wrapped, which is what makes the byte count
    authoritative even when `Content-Length` is absent or untrue.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        declared = _declared_content_length(scope)
        if declared is not None and declared > self._max_bytes:
            # Refused without reading the body at all.
            await _fault_response(VoiceInterfaceFault.AUDIO_TOO_LARGE)(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, guarded_send)
        except _BodyTooLargeError:
            if not response_started:
                await _fault_response(VoiceInterfaceFault.AUDIO_TOO_LARGE)(scope, receive, send)


async def _handle_interface_error(request: Request, exc: Exception) -> Response:
    del request
    if isinstance(exc, VoiceInterfaceError):
        return _fault_response(exc.fault)
    return _fault_response(VoiceInterfaceFault.TURN_FAILED)


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    """Replace FastAPI's default 422 body, which echoes the client's input.

    The default `RequestValidationError` response includes an `input` field
    containing the offending value and a `loc` path into the payload.
    Neither is dangerous by itself, but reflecting request content back is
    a habit this boundary does not want, and the client learns nothing
    useful from it that `malformed_request` does not already say.
    """
    del request, exc
    return _fault_response(VoiceInterfaceFault.MALFORMED_REQUEST)


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    """Give framework-raised 404/405/etc. the same shape as everything else."""
    del request
    status_code = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error="request_failed", detail="This request could not be served."
        ).model_dump(),
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Last resort: no type, no message, no traceback, ever.

    Starlette would otherwise re-raise, and an unhandled exception is
    exactly the situation where whatever string it carries is least
    predictable. `exc` is deliberately consumed without being read.
    """
    del request, exc
    return _fault_response(VoiceInterfaceFault.TURN_FAILED)


def create_app(service: VoiceTurnService | None = None) -> FastAPI:
    """Build the application.

    `service` is injectable so tests can supply a known credential and
    never touch `.env`; in normal use the credential comes from `Settings`,
    which is the one place in this codebase allowed to read the
    environment (`orion.config`). This function is the composition root —
    the only reason `orion.interface` may import `orion.config` at all,
    while `orion.voice` still may not.
    """
    turn_service = service or VoiceTurnService(get_settings().deepgram_api_key)

    app = FastAPI(
        title="ORION voice interface",
        version="0.1.0",
        summary="Localhost-only HTTP adapter over the Mission 9 voice runtime.",
        debug=False,
    )

    # Added first, so it sits *inside* CORS: a 413 still comes back with the
    # CORS headers a browser needs in order to read it (Starlette applies
    # the last-added middleware outermost).
    app.add_middleware(_BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(CORS_ALLOWED_ORIGINS),
        # No cookies, no Authorization header, nothing to send credentials
        # with — and keeping this False is what stops a future auth change
        # from quietly turning the allowlist into a credentialed one.
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )

    app.add_exception_handler(VoiceInterfaceError, _handle_interface_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            voice_available=turn_service.voice_available,
            text_fallback_available=turn_service.voice_available,
        )

    @app.post("/v1/voice/turn", response_model=VoiceTurnResponse)
    async def voice_turn(request: Request) -> VoiceTurnResponse:
        """One spoken turn. Body is raw audio bytes, not multipart.

        Raw bytes with an explicit `Content-Type` means no
        `python-multipart` dependency and no parser standing between the
        socket and the size ceiling — the body is either within the limit
        or it never becomes a Python object.
        """
        media_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if media_type not in ACCEPTED_AUDIO_CONTENT_TYPES:
            raise VoiceInterfaceError(VoiceInterfaceFault.UNSUPPORTED_MEDIA_TYPE)

        audio = await request.body()
        # `run_turn` is synchronous and blocks on two network round trips.
        # Running it in a worker thread keeps the event loop free, and
        # gives concurrent requests genuinely separate execution — each
        # with its own VoiceRuntime, which is the point.
        return await run_in_threadpool(turn_service.run_audio_turn, audio)

    @app.post("/v1/voice/turn/text", response_model=VoiceTurnResponse)
    async def voice_turn_text(payload: TextTurnRequest) -> VoiceTurnResponse:
        """One typed turn: skips speech-to-text, still really speaks."""
        return await run_in_threadpool(turn_service.run_text_turn, payload.text)

    return app


app = create_app()


def run(*, port: int = _DEFAULT_PORT) -> None:
    """Serve on loopback. There is deliberately no `host` parameter.

    `uvicorn` is imported here rather than at module scope so that
    importing this module — which the test suite does constantly — never
    requires the server package to be present or importable, matching
    `orion.voice.audio_input`'s identical deferred-import discipline for
    `sounddevice`.
    """
    import uvicorn

    uvicorn.run(app, host=_BIND_HOST, port=port, log_level="info")
