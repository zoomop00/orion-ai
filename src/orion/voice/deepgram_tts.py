"""Deepgram implementation of `SpeechSynthesisProvider` (text-to-speech),
using the Aura-2 model on Deepgram's REST `/v1/speak` endpoint.

Only this file (and `orion.voice.deepgram_stt`) may import `httpx` inside
`orion.voice` -- see `deepgram_stt`'s module docstring for the shared
import-boundary/auth/credential-leakage rationale, which applies here
unchanged (header-based `Authorization: Token <key>` auth, `orion.
http_errors` reuse, empirical leakage verification in
`tests/voice/test_deepgram_tts.py`).

MODEL/ENCODING: fixed constants (`aura-2-thalia-en`, `mp3`), not
user-configurable -- Settings carries only `DEEPGRAM_API_KEY` per the
approved Phase 2 scope. Both are sent as explicit request query parameters
rather than relying on Deepgram's own defaults, so `SynthesizedSpeech.model`/
`.format` reflect the actual configured/used values by construction: a
success response (`raise_for_status()` passing) is Deepgram's REST contract
that the request as specified -- this exact model, this exact encoding --
was honored, not a guess about what happened.

`text` is ORION-authored output text (never externally-sourced), per
`SpeechSynthesisProvider.synthesize`'s documented contract -- it does not
carry `Transcript.text`'s untrusted-data concerns. `MAX_TEXT_CHARS` is still
enforced as an input-size backstop before the vendor request is made
(never silently truncated), mirroring `Transcript.text`'s identical
"conventional starting point, not vendor-calibrated" 2000-char cap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from orion.http_errors import describe_http_error, raise_detached
from orion.voice.provider_base import SpeechSynthesisProvider, SpeechSynthesisProviderError
from orion.voice.schemas import SynthesizedSpeech

_SPEAK_URL = "https://api.deepgram.com/v1/speak"
_MODEL = "aura-2-thalia-en"
_ENCODING = "mp3"
_PROVIDER_NAME = "deepgram"
_DEFAULT_TIMEOUT_SECONDS = 30.0

MAX_TEXT_CHARS = 2000


class DeepgramSpeechSynthesisProvider(SpeechSynthesisProvider):
    """Text-to-speech via Deepgram Aura-2's REST endpoint."""

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise SpeechSynthesisProviderError("DEEPGRAM_API_KEY is not configured")
        # The key is handed to per-request headers and never retained
        # anywhere it could reach a repr/log -- only on self, matching
        # DeepgramTranscriptionProvider's identical discipline.
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)

    def synthesize(self, text: str) -> SynthesizedSpeech:
        if not text:
            raise SpeechSynthesisProviderError("text is empty; nothing to synthesize")
        if len(text) > MAX_TEXT_CHARS:
            raise SpeechSynthesisProviderError(
                f"text is {len(text)} chars, exceeding the {MAX_TEXT_CHARS}-char limit"
            )

        try:
            response = self._client.post(
                _SPEAK_URL,
                params={"model": _MODEL, "encoding": _ENCODING},
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"text": text},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise_detached(
                SpeechSynthesisProviderError(f"Deepgram request failed: {describe_http_error(exc)}")
            )

        audio = response.content
        if not audio:
            raise SpeechSynthesisProviderError("Deepgram returned empty audio")

        try:
            return SynthesizedSpeech(
                audio=audio,
                format=_ENCODING,
                provider=_PROVIDER_NAME,
                model=_MODEL,
                synthesized_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise SpeechSynthesisProviderError(
                f"Deepgram returned audio that failed validation: {exc}"
            ) from exc
