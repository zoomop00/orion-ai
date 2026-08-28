"""The `ResponseProvider` Mission 9 built a seam for and deliberately left
empty.

`orion.voice.response`'s module docstring promised that "swapping in a real
reasoning/orchestrator-backed provider later means writing a new class here,
matching every other provider boundary in this codebase." This is that
class. It lives in `orion.orchestrator` rather than in `orion.voice` for a
reason that is enforced, not merely intended: `tests/voice/test_import_
hygiene.py` forbids `orion.voice` from importing `orion.data`,
`orion.analysis`, `orion.decision`, `orion.risk` or any other engine, so an
analysis-backed provider *cannot* be written inside that package. The
dependency points the only way it is allowed to.

WHY THE REPORT IS CAPTURED RATHER THAN RETURNED. `ResponseProvider.respond`
returns `str`, because Mission 9 only ever needed something to say. Widening
that ABC to carry structure would be a Mission 9 change made for a Mission
10 convenience. Instead the completed `AnalysisReport` is held on this
instance and read back through `last_report` after the turn. That is safe
precisely because of how `orion.interface.service.VoiceTurnService._run`
already works: it constructs every provider fresh, per request, sharing
nothing between calls — so a provider instance is scoped to exactly one turn
and `last_report` cannot bleed across users, requests or threads. A caller
that reuses one instance across turns gets the report of the most recent
one, and `last_report` is reset at the *start* of every `respond` so a
failed or unrecognised turn can never leave the previous turn's analysis
sitting there looking current.

`transcribed_at` IS THE CLOCK. Nothing in this module calls
`datetime.now()`. The analysis runs `as of` the moment the transcript was
produced, which is a real, provenance-stamped timestamp that arrived with
the input — so `respond` stays a pure function of its argument plus the
injected service, exactly like every engine underneath it. Staleness checks
downstream then measure the market against when the user actually asked.

FAILURE IS NOT A VERDICT. Every way an analysis can fail — bad market data,
an unreachable vendor, a refused model call, a Risk Guardian linkage error —
becomes a `ResponseProviderError` with a fixed message and no report. It
never becomes a spoken guess, a neutral "conditions look mixed", or a report
with empty fields. `VoiceTurnService` already maps `ResponseProviderError`
onto its calm, credential-free `TURN_FAILED` fault, so the failure reaches
the user as one honest sentence with no vendor name, path or exception text
in it. Softening any of this into a spoken apology is a UX policy decision
for the HTTP layer that owns the fault vocabulary, not something this module
decides on its own.

THE SPOKEN SUMMARY IS ASSEMBLED FROM CLOSED VOCABULARIES. Trend, volatility,
action, direction and risk outcome are all enums; the numbers are formatted
here to a fixed number of places. The single piece of model-authored text
that reaches speech is `reasoning.rationale`, which is already bounded to
400 characters by `ReasoningVerdict` and is spoken, never executed — and it
is omitted entirely rather than truncated if including it would push the
utterance past what the speech synthesiser accepts.
"""

from __future__ import annotations

from orion.analysis.structure import TrendDirection, VolatilityLevel
from orion.data.models import Timeframe
from orion.orchestrator.analysis import AnalysisService
from orion.orchestrator.intent import parse_intent
from orion.orchestrator.schemas import AnalysisReport, IntentKind, RiskAvailability
from orion.reasoning.schemas import Action, Direction
from orion.risk.schemas import RiskOutcome
from orion.voice.response import ResponseProvider, ResponseProviderError, StaticResponseProvider
from orion.voice.schemas import Transcript

# `orion.voice.deepgram_tts.MAX_TEXT_CHARS` is 2000. Restated rather than
# imported for the same reason Mission 9 restates Deepgram's own limits: this
# module must never be the looser of the two, and it has no business
# depending on which vendor happens to synthesise the speech. The margin
# below 2000 is deliberate — a future synthesiser with a slightly tighter cap
# should not silently start failing turns.
MAX_SPOKEN_CHARS = 1800

# What ORION calls each symbol out loud. A closed table, like every other
# vocabulary here: the spoken form is chosen at development time, never
# derived by transforming the internal symbol.
_SPOKEN_SYMBOL: dict[str, str] = {"XAUUSD": "Gold"}

_SPOKEN_TIMEFRAME: dict[Timeframe, str] = {
    Timeframe.M15: "on the fifteen minute chart",
    Timeframe.H1: "on the hourly chart",
    Timeframe.H4: "on the four hour chart",
    Timeframe.D1: "on the daily chart",
}

_SPOKEN_TREND: dict[TrendDirection, str] = {
    TrendDirection.BULLISH: "the trend is bullish",
    TrendDirection.BEARISH: "the trend is bearish",
    TrendDirection.SIDEWAYS: "the trend is sideways",
    TrendDirection.UNKNOWN: "the trend is unclear",
}

_SPOKEN_VOLATILITY: dict[VolatilityLevel, str] = {
    VolatilityLevel.LOW: "volatility is low",
    VolatilityLevel.MEDIUM: "volatility is moderate",
    VolatilityLevel.HIGH: "volatility is high",
    VolatilityLevel.UNKNOWN: "volatility is unclear",
}

_SPOKEN_CALL: dict[tuple[Action, Direction], str] = {
    (Action.BUY, Direction.LONG): "my call is to buy",
    (Action.SELL, Direction.SHORT): "my call is to sell",
    (Action.WAIT, Direction.LONG): "my call is to wait, with a long bias",
    (Action.WAIT, Direction.SHORT): "my call is to wait, with a short bias",
    (Action.WAIT, Direction.NONE): "my call is to wait",
}

_SPOKEN_RISK: dict[RiskOutcome, str] = {
    RiskOutcome.APPROVED: "The risk guardian approved it for paper trading.",
    RiskOutcome.VETOED: "The risk guardian vetoed it.",
    RiskOutcome.NO_TRADE_PROPOSED: "There was no trade for the risk guardian to review.",
}

_SPOKEN_RISK_UNAVAILABLE: dict[RiskAvailability, str] = {
    RiskAvailability.NO_PORTFOLIO_STORE: (
        "I could not run a risk review, because no paper account is connected."
    ),
    RiskAvailability.PORTFOLIO_UNAVAILABLE: (
        "I could not run a risk review, because the paper account could not be read."
    ),
    RiskAvailability.PORTFOLIO_SYMBOL_MISMATCH: (
        "I could not run a risk review, because the paper account is for a different market."
    ),
}

ANALYSIS_FAILED_MESSAGE = "the analysis could not be completed"


def _round(value: float, places: int) -> str:
    """Format a float deterministically, with no locale and no exponent."""
    return f"{value:.{places}f}"


def summarize(report: AnalysisReport) -> str:
    """One short spoken paragraph describing `report`.

    Deterministic: identical reports produce byte-identical text. Bounded:
    the result is never longer than `MAX_SPOKEN_CHARS`, achieved by omitting
    the model's rationale rather than by cutting a sentence in half.
    """
    name = _SPOKEN_SYMBOL.get(report.symbol, report.symbol)
    chart = _SPOKEN_TIMEFRAME[report.timeframe]
    structure = report.evidence.structure
    reasoning = report.reasoning

    parts: list[str] = [
        f"{name} {chart}: {_SPOKEN_TREND[structure.trend]}, "
        f"and {_SPOKEN_VOLATILITY[structure.volatility]}."
    ]

    rsi = report.evidence.indicators.rsi
    if rsi is not None:
        parts.append(f"Momentum, by R S I, is at {_round(rsi, 1)}.")
    else:
        # Never a substituted number. `IndicatorSnapshot` uses None for "not
        # enough data", and that is said out loud rather than smoothed over.
        parts.append("There is not enough history yet for a momentum reading.")

    call = _SPOKEN_CALL[(report.decision.action, report.decision.direction)]
    confidence = _round(reasoning.confidence * 100, 0)
    parts.append(f"On the evidence, {call}, with {confidence} percent confidence.")

    if report.risk.availability is RiskAvailability.AVAILABLE:
        # Guaranteed non-None by `RiskPanel`'s own validator.
        assert report.risk.verdict is not None
        parts.append(_SPOKEN_RISK[report.risk.verdict.outcome])
    else:
        parts.append(_SPOKEN_RISK_UNAVAILABLE[report.risk.availability])

    without_rationale = " ".join(parts)
    with_rationale = f"{without_rationale} {reasoning.rationale}"
    return with_rationale if len(with_rationale) <= MAX_SPOKEN_CHARS else without_rationale


class AnalysisResponseProvider(ResponseProvider):
    """Answers an analyse request with a real analysis, and anything else
    with the existing Mission 9 static response.

    The fallback is an injected `ResponseProvider`, defaulting to
    `StaticResponseProvider` — so "ORION did not understand that" keeps
    going through the one boundary that already owns that answer, rather
    than this class growing a second copy of it.
    """

    def __init__(
        self,
        service: AnalysisService,
        *,
        fallback: ResponseProvider | None = None,
    ) -> None:
        self._service = service
        self._fallback: ResponseProvider = fallback or StaticResponseProvider()
        self._last_report: AnalysisReport | None = None

    @property
    def last_report(self) -> AnalysisReport | None:
        """The report from the most recent `respond`, or None if that turn
        produced no analysis (an unrecognised request, or a failure).

        Cleared at the start of every `respond`, so a stale report can never
        be read back as if it described the current turn.
        """
        return self._last_report

    def respond(self, transcript: Transcript) -> str:
        # Cleared first, unconditionally: whatever happens below, the caller
        # must never be able to read the previous turn's analysis.
        self._last_report = None

        intent = parse_intent(transcript.text)
        if intent.kind is not IntentKind.ANALYZE:
            return self._fallback.respond(transcript)

        try:
            report = self._service.run(intent, now=transcript.transcribed_at)
        except ResponseProviderError:
            # Already this boundary's own vocabulary — re-raising it
            # unchanged avoids wrapping a fixed message inside another one.
            raise
        except Exception as exc:
            # Deliberately broad, and deliberately message-free. Every stage
            # underneath raises a different, vendor-specific error type
            # (`MarketDataProviderError`, `ReasoningProviderError`,
            # `DecisionEngineError`, `RiskGuardianError`,
            # `AnalysisDataQualityError`), and any of their messages may name
            # a vendor, a URL, a database path or a raw provider response.
            # None of that may reach a string this interface will speak, so
            # the cause is chained for a debugger and discarded for the user.
            raise ResponseProviderError(ANALYSIS_FAILED_MESSAGE) from exc

        self._last_report = report
        return summarize(report)
