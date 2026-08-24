"""Deepgram implementation of `TranscriptionProvider` (speech-to-text),
using the Nova-3 model on Deepgram's pre-recorded (batch) REST endpoint.

Only this file (and `orion.voice.deepgram_tts`) may import `httpx` inside
`orion.voice` -- `orion.voice.provider_base`/`orion.voice.schemas` remain
vendor-independent, enforced mechanically by `tests/voice/test_import_
hygiene.py`.

AUTHENTICATION: header-based only (`Authorization: Token <key>`), per
Deepgram's own documented auth scheme -- the key is never placed in the
URL/query string, unlike `TwelveDataProvider`/`FinnhubEconomicCalendarProvider`
(both of which use a query-parameter API key because that is what *those*
vendors require). `orion.http_errors.describe_http_error`/`raise_detached`
are reused regardless of that difference (see their module docstring: they
never echo the query string "regardless of which parameter name a given
provider happens to use for its key", written generically enough to already
cover a header-auth vendor). This is not merely assumed safe because
Deepgram uses headers: `tests/voice/test_deepgram_stt.py` empirically
verifies (via a fake secret run through a live `httpx.HTTPStatusError`/
`ConnectError` and `repr()`/`str()`/`traceback.format_exception`/`response.
headers`) that no credential-bearing surface leaks the key, mirroring
`tests/research/test_finnhub_provider.py`'s identical regression-test
pattern.

LANGUAGE: `language=multi` is Deepgram's documented Nova-3 multilingual
code-switching mode -- the concrete mechanism behind the Hindi/Hinglish
capability that was the stated reason for selecting Deepgram (see
`docs/DECISIONS.md`). Fixed here, not user-configurable: Settings carries
only `DEEPGRAM_API_KEY` per the approved Phase 2 scope ("do not add
unnecessary configuration").

INPUT: `audio` is opaque, vendor-agnostic bytes per `TranscriptionProvider.
transcribe`'s contract -- this provider does not know (and does not assume)
a specific container/codec. `Content-Type: application/octet-stream` is
sent accordingly; Deepgram's own docs state format detection from the
audio's own bytes is the normal path ("Generally you don't have to specify
the audio format in your API request"), so an unlabeled generic
Content-Type does not prevent transcription.

SIZE LIMIT: `MAX_AUDIO_BYTES` is a conservative, not-vendor-calibrated
starting point for "one complete utterance" (Deepgram's own documented
ceiling for this endpoint is 2 GB, meant for long-form files) -- mirrors
`Transcript.text`'s identical "generously long, not vendor-calibrated"
2000-char cap. Enforced before the request is ever made; oversized/empty
audio is rejected outright, never silently truncated.

OUTPUT: every externally-returned field is validated through Pydantic
response models before touching `Transcript` -- a malformed or
unexpected-shape response fails closed with `TranscriptionProviderError`,
never a partially-fabricated transcript. No speech detected (Deepgram
returns an empty `transcript` string, not an error) is treated as a hard
failure too, matching `TranscriptionProvider.transcribe`'s documented
contract that an empty/placeholder `Transcript` must never be returned.
`model` is populated only from `metadata.model_info`, when the provider
actually reports it -- never guessed. `duration_seconds`/`confidence` are
likewise provider-observed only; `Transcript`'s own validators (`orion.
voice.schemas`) independently re-enforce range/finiteness.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from orion.http_errors import describe_http_error, raise_detached
from orion.voice.provider_base import TranscriptionProvider, TranscriptionProviderError
from orion.voice.schemas import Transcript

_LISTEN_URL = "https://api.deepgram.com/v1/listen"
_MODEL = "nova-3"
_LANGUAGE = "multi"
_PROVIDER_NAME = "deepgram"
_DEFAULT_TIMEOUT_SECONDS = 30.0

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class _DeepgramAlternative(BaseModel):
    transcript: str | None = None
    confidence: float | None = None


class _DeepgramChannel(BaseModel):
    alternatives: list[_DeepgramAlternative] = Field(default_factory=list)


class _DeepgramResults(BaseModel):
    channels: list[_DeepgramChannel] = Field(default_factory=list)


class _DeepgramModelInfo(BaseModel):
    name: str | None = None


class _DeepgramMetadata(BaseModel):
    duration: float | None = None
    model_info: dict[str, _DeepgramModelInfo] | None = None


class _DeepgramListenResponse(BaseModel):
    metadata: _DeepgramMetadata | None = None
    results: _DeepgramResults | None = None


class DeepgramTranscriptionProvider(TranscriptionProvider):
    """Speech-to-text via Deepgram Nova-3's pre-recorded REST endpoint."""

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise TranscriptionProviderError("DEEPGRAM_API_KEY is not configured")
        # The key is handed to per-request headers and never retained
        # anywhere it could reach a repr/log -- only on self, exactly like
        # every other provider's identical discipline in this codebase.
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)

    def transcribe(self, audio: bytes) -> Transcript:
        if not audio:
            raise TranscriptionProviderError("audio is empty; nothing to transcribe")
        if len(audio) > MAX_AUDIO_BYTES:
            raise TranscriptionProviderError(
                f"audio is {len(audio)} bytes, exceeding the {MAX_AUDIO_BYTES}-byte limit "
                "for one utterance"
            )

        try:
            response = self._client.post(
                _LISTEN_URL,
                params={"model": _MODEL, "language": _LANGUAGE},
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Content-Type": "application/octet-stream",
                },
                content=audio,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise_detached(
                TranscriptionProviderError(f"Deepgram request failed: {describe_http_error(exc)}")
            )

        try:
            parsed = _DeepgramListenResponse.model_validate(response.json())
        except ValueError as exc:
            raise TranscriptionProviderError(
                f"Deepgram returned an unparseable response: {exc}"
            ) from exc

        if parsed.results is None or not parsed.results.channels:
            raise TranscriptionProviderError("Deepgram response has no results.channels")
        alternatives = parsed.results.channels[0].alternatives
        if not alternatives:
            raise TranscriptionProviderError("Deepgram response channel has no alternatives")

        text = alternatives[0].transcript
        if not text:
            raise TranscriptionProviderError("Deepgram detected no speech in the supplied audio")

        model_name: str | None = None
        if parsed.metadata is not None and parsed.metadata.model_info:
            model_name = next(iter(parsed.metadata.model_info.values())).name

        try:
            return Transcript(
                text=text,
                provider=_PROVIDER_NAME,
                model=model_name,
                transcribed_at=datetime.now(UTC),
                duration_seconds=parsed.metadata.duration if parsed.metadata else None,
                confidence=alternatives[0].confidence,
            )
        except ValueError as exc:
            raise TranscriptionProviderError(
                f"Deepgram returned a transcript that failed validation: {exc}"
            ) from exc
