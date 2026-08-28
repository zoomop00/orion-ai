"""Composition root for one voice turn over HTTP.

This module is an ADAPTER, not the ORION orchestrator. It knows how to
assemble Mission 9's five injected dependencies into a `VoiceRuntime`, run
exactly one turn, and reduce the result to a wire shape. It still knows
nothing about market data, evidence, reasoning, risk or portfolios, and it
must stay that way — `tests/interface/test_import_hygiene.py` forbids this
package from importing `orion.decision`/`orion.risk`/`orion.paper` outright,
because "the moment it can reach them, an HTTP handler is one edit away from
making trading decisions."

WHAT CHANGED IN PHASE 5.2 IS WHO ANSWERS, NOT WHAT THIS FILE KNOWS. The
orchestrator built in Phase 5.1 now exists, so the thing standing between
"user said something" and "ORION replies" can be
`orion.orchestrator.response.AnalysisResponseProvider` instead of
`StaticResponseProvider`. This module hands it a transcript and receives a
sentence plus, sometimes, an `AnalysisReport`; it never calls `decide()` or
`review()` and could not if it wanted to. When no `AnalysisService` is
supplied — no Twelve Data key, no Anthropic key, or a caller that simply did
not wire one — behaviour is byte-identical to Phase 2/3: `StaticResponseProvider`,
no analysis, `analysis: null`.

THE ANALYSIS PROVIDER IS BUILT PER REQUEST, LIKE EVERYTHING ELSE HERE.
`AnalysisResponseProvider` holds the completed report on itself so it can be
read back after the turn (`ResponseProvider.respond` returns `str`, and
widening Mission 9's ABC for a Mission 10 convenience was not on the table).
That is only safe because a fresh one is constructed inside `_run` for every
call and dropped when it returns — one request's analysis is unreachable
from another's, structurally, for the same reason one request's
`VoiceStateMachine` is. The `AnalysisService` itself is built per request too,
through an injected factory: `orion.risk.policy.RiskPolicy.from_settings`
documents that a policy must be built fresh each cycle from an uncached
`get_settings()`, because that is what lets an operator engage the kill
switch by editing `.env` without restarting the process.

ONE RUNTIME PER REQUEST, ALWAYS. `run_audio_turn`/`run_text_turn` each
construct a brand-new `VoiceRuntime` — and brand-new providers, and a
brand-new `httpx.Client` — and drop all of them when the call returns.
Nothing is cached on `self`, at module scope, or in any registry.
`VoiceRuntime` documents itself as "a single synchronous session object
with no concurrency support"; the only safe way to serve concurrent
requests from it is therefore to give every request its own, which also
means one request's `VoiceStateMachine` can never be observed, advanced or
corrupted by another's. `VoiceRuntimeBusyError` should consequently be
unreachable through this interface — it is still mapped below, because
"unreachable" is a claim that should degrade into a clean 409 rather than a
traceback if it ever stops being true.

TEXT FALLBACK BYPASSES STT, NOT VALIDATION. The text path builds a real
`orion.voice.schemas.Transcript` from the client's string *before* any
runtime exists, so Mission 9's own rules (1–2000 characters, `isprintable()`,
rejected rather than normalized) decide whether the request is acceptable.
The transcript is then handed to `PreparedTranscription`, a
`TranscriptionProvider` that returns it verbatim. No Deepgram STT call is
made, no audio is invented to justify one, and not one validation rule is
restated in this package.

ERRORS NEVER CARRY DETAIL. `VoiceInterfaceError` takes a `VoiceInterfaceFault`
and nothing else — there is no message parameter, so it is structurally
impossible for an exception string, a provider message, a URL, a file path
or a request body to be attached to one. `FAULT_RESPONSES` below is the
complete, closed mapping from fault to (status, code, sentence), and every
sentence is a literal written here at development time. This is coarser
than it could be on purpose: distinguishing "Deepgram heard no speech" from
"Deepgram rejected the request" would mean inspecting a provider's error
text and reflecting a decision about it back to the client, which is
exactly the coupling that leaks vendor internals one careless refactor
later. Mission 9 already guarantees its own error messages are
credential-free (`orion.http_errors.describe_http_error`/`raise_detached`);
this layer does not rely on that guarantee, it simply never forwards the
message.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

import httpx

from orion.interface.schemas import (
    MAX_REQUEST_BODY_BYTES,
    AnalysisSummary,
    CapabilitySummary,
    ConclusionSummary,
    MomentumSummary,
    RiskSummary,
    StructureSummary,
    VoiceTurnResponse,
)
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.response import AnalysisResponseProvider
from orion.orchestrator.schemas import AnalysisReport
from orion.voice.audio_input import AudioInputError, InMemoryAudioInput
from orion.voice.audio_output import AudioOutputError, InMemoryAudioOutput
from orion.voice.deepgram_stt import DeepgramTranscriptionProvider
from orion.voice.deepgram_tts import DeepgramSpeechSynthesisProvider
from orion.voice.provider_base import (
    SpeechSynthesisProviderError,
    TranscriptionProvider,
    TranscriptionProviderError,
)
from orion.voice.response import ResponseProviderError, StaticResponseProvider
from orion.voice.runtime import VoiceRuntime, VoiceRuntimeBusyError, VoiceTurnResult
from orion.voice.schemas import Transcript

_TEXT_FALLBACK_PROVIDER = "text-fallback"
_REQUEST_TIMEOUT_SECONDS = 30.0

# The text path has no audio, but `VoiceRuntime` always calls
# `AudioInput.capture()` and `InMemoryAudioInput` rejects empty bytes. This
# single byte exists only to satisfy that shape; `PreparedTranscription`
# discards whatever it is handed, so it is never transcribed, never sent
# anywhere, and never mistaken for a recording. Naming it explicitly is
# preferable to quietly passing `b"\x00"` at the call site.
_NO_AUDIO_SENTINEL = b"\x00"

# `SynthesizedSpeech.format` is a free string by design (vendors differ), so
# it is translated to a browser media type through a closed table rather
# than string-formatted into one. An unknown format degrades to a generic
# type instead of asserting something untrue about the bytes.
_MEDIA_TYPE_BY_FORMAT = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}
_FALLBACK_MEDIA_TYPE = "application/octet-stream"

# Builds one request-scoped `AnalysisService`, or returns `None` when this
# deployment cannot analyse. A callable rather than an instance so the
# `RiskPolicy` inside it is rebuilt per request — see the module docstring.
AnalysisServiceFactory = Callable[[], AnalysisService | None]

# `orion.risk.schemas.ControlStatus.FAILED.value`, restated as a literal
# because `tests/interface/test_import_hygiene.py` forbids this package from
# importing `orion.risk` at all — the rule that keeps an HTTP handler from
# ever being one edit away from a trade decision. A test asserts this string
# still equals the enum member it mirrors, so the two cannot drift apart
# silently.
_FAILED_CONTROL_STATUS = "failed"


class VoiceInterfaceFault(StrEnum):
    """Every way a turn can fail, from the client's point of view."""

    EMPTY_AUDIO = "empty_audio"
    AUDIO_TOO_LARGE = "audio_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    MALFORMED_REQUEST = "malformed_request"
    INVALID_TEXT = "invalid_text"
    VOICE_UNAVAILABLE = "voice_unavailable"
    TRANSCRIPTION_FAILED = "transcription_failed"
    SYNTHESIS_FAILED = "synthesis_failed"
    RUNTIME_BUSY = "runtime_busy"
    # Mission 12.1. Distinct from RUNTIME_BUSY on purpose: "ORION is already
    # handling a turn" is a statement about right now, and would be untrue when
    # the per-window budget is spent while nothing at all is running.
    RATE_LIMITED = "rate_limited"
    TURN_FAILED = "turn_failed"


# fault -> (HTTP status, stable code, fixed sentence). Nothing in the third
# column is ever built at runtime. See the module docstring.
FAULT_RESPONSES: dict[VoiceInterfaceFault, tuple[int, str, str]] = {
    VoiceInterfaceFault.EMPTY_AUDIO: (
        400,
        "empty_audio",
        "The request contained no audio.",
    ),
    VoiceInterfaceFault.AUDIO_TOO_LARGE: (
        413,
        "audio_too_large",
        "The request body is larger than this service accepts.",
    ),
    VoiceInterfaceFault.UNSUPPORTED_MEDIA_TYPE: (
        415,
        "unsupported_media_type",
        "Send raw audio bytes with an audio Content-Type.",
    ),
    VoiceInterfaceFault.MALFORMED_REQUEST: (
        422,
        "malformed_request",
        "The request body was not in the expected form.",
    ),
    VoiceInterfaceFault.INVALID_TEXT: (
        422,
        "invalid_text",
        "The supplied text was empty, too long, or contained unsupported characters.",
    ),
    VoiceInterfaceFault.VOICE_UNAVAILABLE: (
        503,
        "voice_unavailable",
        "ORION's voice is not configured on this server.",
    ),
    VoiceInterfaceFault.TRANSCRIPTION_FAILED: (
        502,
        "transcription_failed",
        "ORION could not make out what was said.",
    ),
    VoiceInterfaceFault.SYNTHESIS_FAILED: (
        502,
        "synthesis_failed",
        "ORION could not produce a spoken reply.",
    ),
    VoiceInterfaceFault.RUNTIME_BUSY: (
        409,
        "runtime_busy",
        "ORION is already handling a turn.",
    ),
    VoiceInterfaceFault.RATE_LIMITED: (
        429,
        "rate_limited",
        "ORION has handled too many turns just now. Try again shortly.",
    ),
    VoiceInterfaceFault.TURN_FAILED: (
        500,
        "turn_failed",
        "ORION could not complete the turn.",
    ),
}


class VoiceInterfaceError(RuntimeError):
    """A turn failure already reduced to a client-safe fault.

    Takes no message. `str(self)` is the fault's own value and nothing
    else — there is no parameter through which caller detail, exception
    text or request content could ever be attached.
    """

    def __init__(self, fault: VoiceInterfaceFault) -> None:
        super().__init__(fault.value)
        self.fault = fault


class PreparedTranscription(TranscriptionProvider):
    """Returns one already-validated `Transcript`, ignoring the audio.

    The text-fallback path's `TranscriptionProvider`. Implementing Mission
    9's existing ABC — rather than teaching `VoiceRuntime` about a second
    kind of turn — is the whole reason no Mission 9 file changes for this
    phase: the runtime's documented extension mechanism is "construct it
    with different arguments", and this is one of those arguments.
    """

    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript

    def transcribe(self, audio: bytes) -> Transcript:
        # Deliberately unread: on this path the bytes are `_NO_AUDIO_SENTINEL`,
        # not a recording. Accepting them keeps this class's signature
        # identical to every other TranscriptionProvider.
        del audio
        return self._transcript


class VoiceTurnService:
    """Runs one voice turn per call, sharing nothing between calls.

    Holds exactly one piece of state: the Deepgram credential, taken as a
    plain string exactly like `DeepgramTranscriptionProvider`/
    `ClaudeReasoningProvider`/`FinnhubEconomicCalendarProvider` already do.
    Reading it from `Settings` is `orion.interface.app`'s job as the
    composition root, which is what keeps this module importable and fully
    testable with no environment at all.
    """

    def __init__(
        self,
        deepgram_api_key: str,
        analysis_factory: AnalysisServiceFactory | None = None,
    ) -> None:
        self._deepgram_api_key = deepgram_api_key
        self._analysis_factory = analysis_factory

    @property
    def voice_available(self) -> bool:
        """Whether a credential is present. A boolean — never the key."""
        return bool(self._deepgram_api_key)

    @property
    def analysis_available(self) -> bool:
        """Whether this service was wired to analyse at all.

        A boolean about wiring, not about credentials — the factory itself
        decides whether the keys it needs are present, and returns `None` if
        they are not. Never a key, never its length, never a prefix.
        """
        return self._analysis_factory is not None

    def run_audio_turn(self, audio: bytes) -> VoiceTurnResponse:
        """Transcribe `audio`, answer it, and speak the answer.

        `audio` is untrusted bytes straight off the socket. The size and
        emptiness checks here are transport-level request validity, not a
        restatement of Mission 9's rules: `MAX_REQUEST_BODY_BYTES` *is*
        `orion.voice.audio_input.MAX_AUDIO_INPUT_BYTES`, so this boundary
        cannot drift looser than the runtime's, and `InMemoryAudioInput`
        still independently re-checks both below.
        """
        if not audio:
            raise VoiceInterfaceError(VoiceInterfaceFault.EMPTY_AUDIO)
        if len(audio) > MAX_REQUEST_BODY_BYTES:
            raise VoiceInterfaceError(VoiceInterfaceFault.AUDIO_TOO_LARGE)

        self._require_voice()
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            try:
                audio_input = InMemoryAudioInput(audio)
            except AudioInputError as exc:
                raise VoiceInterfaceError(VoiceInterfaceFault.EMPTY_AUDIO) from exc
            transcription = DeepgramTranscriptionProvider(self._deepgram_api_key, client=client)
            return self._run(audio_input=audio_input, transcription=transcription, client=client)

    def run_text_turn(self, text: str) -> VoiceTurnResponse:
        """Answer `text` as if it had been spoken, skipping speech-to-text.

        The `Transcript` is built (and therefore validated by Mission 9)
        before any runtime, provider or HTTP client exists, so unusable
        text costs nothing and fails as a 422 rather than as an upstream
        provider error.
        """
        try:
            transcript = Transcript(
                text=text,
                provider=_TEXT_FALLBACK_PROVIDER,
                transcribed_at=datetime.now(UTC),
            )
        except ValueError as exc:
            raise VoiceInterfaceError(VoiceInterfaceFault.INVALID_TEXT) from exc

        self._require_voice()
        with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            return self._run(
                audio_input=InMemoryAudioInput(_NO_AUDIO_SENTINEL),
                transcription=PreparedTranscription(transcript),
                client=client,
            )

    def _require_voice(self) -> None:
        if not self._deepgram_api_key:
            raise VoiceInterfaceError(VoiceInterfaceFault.VOICE_UNAVAILABLE)

    def _run(
        self,
        *,
        audio_input: InMemoryAudioInput,
        transcription: TranscriptionProvider,
        client: httpx.Client,
    ) -> VoiceTurnResponse:
        """Build a fresh runtime, run exactly one turn, translate the result.

        Every `except` clause below discards the caught exception's message
        and raises a `VoiceInterfaceError` carrying only a fault — see the
        module docstring. The bare `except Exception` at the end is not
        laziness: an unexpected exception type is precisely the case where
        a raw message is most likely to contain something that should not
        cross this boundary, so it is the case that most needs flattening.
        """
        # Fresh for this request and unreachable from any other. When no
        # factory was supplied, or it declines for want of credentials, this
        # is exactly the Phase 2/3 provider and nothing below behaves
        # differently.
        analysis_service = self._analysis_factory() if self._analysis_factory else None
        response_provider = (
            AnalysisResponseProvider(analysis_service)
            if analysis_service is not None
            else StaticResponseProvider()
        )

        runtime = VoiceRuntime(
            audio_input=audio_input,
            transcription=transcription,
            response_provider=response_provider,
            synthesis=DeepgramSpeechSynthesisProvider(self._deepgram_api_key, client=client),
            audio_output=InMemoryAudioOutput(),
        )

        try:
            result = runtime.run_turn()
        except VoiceRuntimeBusyError as exc:
            raise VoiceInterfaceError(VoiceInterfaceFault.RUNTIME_BUSY) from exc
        except TranscriptionProviderError as exc:
            raise VoiceInterfaceError(VoiceInterfaceFault.TRANSCRIPTION_FAILED) from exc
        except SpeechSynthesisProviderError as exc:
            raise VoiceInterfaceError(VoiceInterfaceFault.SYNTHESIS_FAILED) from exc
        except (AudioInputError, AudioOutputError, ResponseProviderError) as exc:
            raise VoiceInterfaceError(VoiceInterfaceFault.TURN_FAILED) from exc
        except Exception as exc:
            raise VoiceInterfaceError(VoiceInterfaceFault.TURN_FAILED) from exc

        report = (
            response_provider.last_report
            if isinstance(response_provider, AnalysisResponseProvider)
            else None
        )
        try:
            analysis = _to_analysis_summary(report) if report is not None else None
        except Exception as exc:
            # The reduction below re-validates every bound independently of
            # the engines that produced the values. A report that cannot
            # satisfy those bounds is one this boundary refuses to serialise
            # at all — a failed turn, never a response with a field quietly
            # dropped or clamped into range.
            raise VoiceInterfaceError(VoiceInterfaceFault.TURN_FAILED) from exc

        return _to_response(result, state=runtime.state.value, analysis=analysis)


def _to_analysis_summary(report: AnalysisReport) -> AnalysisSummary:
    """Reduce a full `AnalysisReport` to the client-visible allowlist.

    Every field is named explicitly. There is no `model_dump()` here and no
    loop over `report`'s attributes, which is the point: a field added to
    `AnalysisReport` — a new provenance ref, a vendor identifier, a debug
    payload — reaches a browser only if someone writes a line here to put it
    there. The model identifier, the prompt version, the evidence
    fingerprint, the engine and guardian versions and the risk-policy content
    hash all exist on `report` and all stop at this function, exactly as
    `_to_response` already drops the STT/TTS vendor detail from a
    `VoiceTurnResult`.

    Enum members become `.value` strings, and `AnalysisSummary`'s own bounds
    re-check every one of them. If any value could not have come from an
    engine, construction raises and the caller turns the whole turn into a
    clean failure rather than serialising it.

    Nothing is filled in. `IndicatorSnapshot`'s `None`s stay `None`, a risk
    panel with no verdict stays a risk panel with no verdict, and the two
    `NotConnectedPanel`s become `CapabilitySummary`s whose only possible
    status is `"not_connected"`.
    """
    evidence = report.evidence
    indicators = evidence.indicators
    structure = evidence.structure
    reasoning = report.reasoning
    decision = report.decision
    verdict = report.risk.verdict

    return AnalysisSummary(
        analysis_id=report.analysis_id,
        symbol=report.symbol,
        timeframe=report.timeframe.value,
        as_of=evidence.as_of,
        analyzed_at=report.analyzed_at,
        last_price=evidence.last_price,
        bar_count=evidence.bar_count,
        structure=StructureSummary(
            trend=structure.trend.value,
            volatility=structure.volatility.value,
            nearest_support=structure.nearest_support,
            nearest_resistance=structure.nearest_resistance,
            swing_high_count=structure.swing_high_count,
            swing_low_count=structure.swing_low_count,
        ),
        momentum=MomentumSummary(
            rsi=indicators.rsi,
            ema_fast=indicators.ema_fast,
            ema_slow=indicators.ema_slow,
            sma_slow=indicators.sma_slow,
            atr=indicators.atr,
            rsi_length=indicators.rsi_length,
            ema_fast_length=indicators.ema_fast_length,
            ema_slow_length=indicators.ema_slow_length,
        ),
        news=CapabilitySummary(detail=report.news.detail),
        macro=CapabilitySummary(detail=report.macro.detail),
        risk=RiskSummary(
            availability=report.risk.availability.value,
            outcome=verdict.outcome.value if verdict is not None else None,
            reason_codes=(
                tuple(code.value for code in verdict.reason_codes) if verdict is not None else ()
            ),
            # Only the controls that actually failed. A passing control is
            # not news, and shipping all fourteen every time would make the
            # one that matters harder to find, not easier.
            failed_controls=(
                tuple(
                    outcome.control.value
                    for outcome in verdict.control_outcomes
                    if outcome.status.value == _FAILED_CONTROL_STATUS
                )
                if verdict is not None
                else ()
            ),
            portfolio_as_of=report.risk.portfolio_as_of,
        ),
        confidence=reasoning.confidence,
        risk_level=reasoning.risk_level.value,
        conclusion=ConclusionSummary(
            action=decision.action.value,
            direction=decision.direction.value,
            rationale=reasoning.rationale,
            reason_codes=tuple(code.value for code in decision.reason_codes),
        ),
    )


def _to_response(
    result: VoiceTurnResult,
    *,
    state: str,
    analysis: AnalysisSummary | None = None,
) -> VoiceTurnResponse:
    """Reduce a full `VoiceTurnResult` to the client-visible allowlist.

    `result.transcript` carries the STT vendor's name, model identifier,
    confidence and duration, and `result.speech` carries the TTS vendor and
    voice model. None of it is copied out. `base64.b64encode` is the only
    thing done to the audio — no transcode, no resample, no rewrap.
    """
    audio = result.speech.audio
    return VoiceTurnResponse(
        transcript=result.transcript.text,
        response_text=result.response_text,
        audio_base64=base64.b64encode(audio).decode("ascii"),
        audio_format=result.speech.format,
        audio_media_type=_MEDIA_TYPE_BY_FORMAT.get(result.speech.format, _FALLBACK_MEDIA_TYPE),
        audio_bytes=len(audio),
        state=state,
        analysis=analysis,
    )
