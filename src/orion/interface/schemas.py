"""Wire shapes for the Mission 10 Phase 2 HTTP boundary — the only things a
client is ever allowed to send in or read back.

DELIBERATELY THIN. `TextTurnRequest.text` carries no length limit, no
character rules and no format validation, because `orion.voice.schemas.
Transcript.text` already owns every one of those rules (1–2000 chars,
`isprintable()`, rejected rather than normalized) and this layer must not
grow a second, silently-drifting copy of them. `orion.interface.service`
feeds the raw string straight into `Transcript` and lets Mission 9's
validators decide — the same "reuse the existing contract, do not restate
it" discipline `orion.voice.audio_output` follows by taking a whole
`SynthesizedSpeech` rather than re-deriving its fields. The only limit this
module does declare is `MAX_REQUEST_BODY_BYTES`, and that is not a business
rule at all: it is a transport-layer ceiling, enforced by
`orion.interface.app` before a single byte reaches the voice runtime.

RESPONSE SURFACE. `VoiceTurnResponse` is an allowlist, not a projection of
whatever the runtime happened to produce. `orion.voice.runtime.
VoiceTurnResult` carries a full `Transcript` (provider name, model
identifier, vendor-reported confidence and duration) and a full
`SynthesizedSpeech` (vendor, voice model, synthesis timestamp). None of
that vendor detail crosses this boundary. A browser needs the words, the
audio, and enough metadata to play it — nothing about which vendor ORION
happens to use this month, which is both an information-disclosure
reduction and the thing that keeps the client from accidentally
depending on a provider we intend to be swappable.

AUDIO TRANSPORT. `audio_base64` is standard base64 of the *unmodified*
WAV bytes Deepgram returned (linear16, 24 kHz, RIFF/WAVE container). The
audio is never transcoded, re-encoded, resampled or rewrapped — only
transport-encoded, and `audio_bytes` reports the true decoded length so a
client can verify the round trip. See `docs/DECISIONS.md` for why base64 in
one JSON response beat the alternatives (a second "fetch the audio"
endpoint would require storing audio between requests, which this phase
forbids outright; metadata-in-headers hits the ~8 KB header ceiling once a
2000-character multilingual transcript is percent-encoded).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orion.voice.audio_input import MAX_AUDIO_INPUT_BYTES

# The transport ceiling for any request body. Deliberately *is*
# `orion.voice.audio_input.MAX_AUDIO_INPUT_BYTES` rather than a second
# hand-picked number: the HTTP boundary must never be the looser of the two
# limits, and the cheapest way to guarantee that forever is to have exactly
# one number. A deployment that wants a tighter ceiling should lower this
# one; it can never accidentally drift above what Mission 9 accepts.
MAX_REQUEST_BODY_BYTES = MAX_AUDIO_INPUT_BYTES

# Content types accepted on the audio turn endpoint. Raw bytes with an
# explicit type, never multipart — no `python-multipart` dependency, no
# parser between the socket and the size check.
ACCEPTED_AUDIO_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/flac",
        "audio/l16",
    }
)


class TextTurnRequest(BaseModel):
    """The text-fallback request body: one field, no local rules.

    This path exists so Phase 3 can drive the entire interface — real
    response text, real Deepgram speech, real audio playback — on a machine
    with no microphone, or in a browser that has not been granted
    microphone permission. It is a genuine product affordance (the brief
    lists "text fallback" as a Mission 9 requirement), not a test hook.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(
        description="What the user said, as text. Validated by "
        "orion.voice.schemas.Transcript, not here — see the module docstring."
    )


class VoiceTurnResponse(BaseModel):
    """One completed voice turn, reduced to what a browser actually needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transcript: str = Field(
        description="What ORION understood the user to have said. For the text-fallback "
        "path this is the client's own text, echoed back only after it has passed "
        "Transcript validation — never before."
    )
    response_text: str = Field(description="What ORION said back, as text.")
    audio_base64: str = Field(
        description="Base64 of the untranscoded synthesized speech. Decode and play."
    )
    audio_format: str = Field(description="Container/encoding of the decoded audio, e.g. 'wav'.")
    audio_media_type: str = Field(
        description="MIME type for the decoded bytes, ready for a Blob/Audio element."
    )
    audio_bytes: int = Field(
        gt=0, description="Length of the decoded audio in bytes, for client-side verification."
    )
    state: str = Field(
        description="The voice runtime's state after the turn, read from Mission 9's "
        "VoiceStateMachine — the one authoritative state machine. This interface owns "
        "no state of its own and never invents a value for this field."
    )


class HealthResponse(BaseModel):
    """Liveness plus one honest capability bit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = Field(description="Always 'ok' if the process is serving requests.")
    voice_available: bool = Field(
        description="Whether a Deepgram credential is configured, so a client can tell "
        "the audio path apart from a misconfiguration before trying it. A boolean only "
        "— never the key, never its length, never a prefix."
    )
    text_fallback_available: bool = Field(
        description="Whether the text-fallback path can run. Requires only text-to-speech, "
        "so it shares the same credential as the audio path today."
    )


class ErrorResponse(BaseModel):
    """The only error shape this service ever returns.

    Both fields are fixed strings chosen at development time. Nothing here
    is ever interpolated from an exception, a provider message, a file path,
    a header or a request body — see `orion.interface.service` for the
    complete, closed mapping and why it is deliberately coarse.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str = Field(description="A stable machine-readable code, safe to branch on.")
    detail: str = Field(description="A calm, generic, human-readable sentence.")
