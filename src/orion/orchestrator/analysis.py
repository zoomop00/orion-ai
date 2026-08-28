"""The composition root ORION has been deferring since Mission 2.

`docs/DECISIONS.md`'s M2-Q9 entry approved shipping *no* orchestrator and no
`pipeline.py`, on the grounds that building one is its own mission rather
than a side effect of another. Every mission since has pointed here: `decide`
and `review` take a caller-supplied `analysis_id`/`now`/`policy`/`portfolio`
specifically because "no orchestrator exists yet to mint them." This module
is that caller.

IT COMPOSES; IT COMPUTES NOTHING. Every step below is an existing function
called with existing arguments. There is no indicator maths here, no rule,
no control, no threshold and no second opinion about anything the engines
already decide. `orion.backtest.engine.run_backtest` is the precedent this
follows deliberately closely — the same chain (validate -> evidence ->
reason -> decide -> review), the same fail-closed posture, the same refusal
to mint an identifier from `uuid4()` — differing only in that it runs once
against live data instead of repeatedly against history, and that it stops
at the verdict instead of executing it.

FAIL-CLOSED, NEVER FAIL-SOFT. `MarketDataProviderError`,
`ReasoningProviderError`, `DecisionEngineError` and `RiskGuardianError` are
never caught here; they propagate and abort the analysis. An analysis that
silently dropped a failed stage would produce a report that *looks* complete
while missing the stage that mattered — the misleading-by-omission failure
this codebase refuses everywhere else. Bad market data is the same: a
snapshot that fails `validate_snapshot` raises `AnalysisDataQualityError`
rather than being analysed anyway with a caveat attached.

THE ONE PLACE ABSENCE IS HANDLED SOFTLY IS RISK, AND ONLY BECAUSE ABSENCE
IS REPORTABLE THERE. A missing or unreadable paper account is not an
analysis failure — market structure, momentum, reasoning and the proposal
are all still real and worth showing. So it becomes an explicit
`RiskAvailability` reason on the report and the Risk Guardian is simply not
consulted. What never happens is inventing an account: a fabricated flat
portfolio at the configured starting balance would make the position-size,
exposure, daily/weekly-loss and drawdown controls evaluate a fiction while
presenting as authoritative, which is precisely what
`orion.paper.store.PortfolioStoreError`'s docstring forbids papering over.

NO CLOCK, NO CONFIGURATION, NO PERSISTENCE. `now` is a required parameter,
never `datetime.now()`; `RiskPolicy` and every provider are injected, so
this module imports neither `orion.config` nor any vendor SDK and is fully
testable with no environment at all (matching `orion.interface.service.
VoiceTurnService`'s identical posture). Nothing is written anywhere: the
portfolio store is only ever `load()`ed, never `save()`d, and no journal is
touched. `AnalysisReport` is returned to the caller and that is the whole
lifetime of the analysis.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from orion.analysis.evidence import Evidence, build_evidence
from orion.analysts.provider_base import MacroAnalystProvider
from orion.data.models import Timeframe
from orion.data.provider_base import MarketDataProvider
from orion.data.validation import validate_snapshot
from orion.decision.engine import decide
from orion.decision.schemas import ProposedDecision
from orion.orchestrator.schemas import (
    AnalysisReport,
    Intent,
    IntentKind,
    MacroAvailability,
    MacroPanel,
    NotConnectedPanel,
    RiskAvailability,
    RiskPanel,
)
from orion.paper.store import PortfolioStore, PortfolioStoreError
from orion.reasoning.provider_base import ReasoningProvider
from orion.research.provider_base import EconomicCalendarProvider
from orion.research.validation import validate_calendar_snapshot
from orion.risk.guardian import review
from orion.risk.policy import RiskPolicy
from orion.risk.portfolio import mark_to_market

ORCHESTRATOR_VERSION = "2026-08-28.1"

# How many bars one live analysis looks at. Comfortably above the 50 the
# default indicator lengths need (`orion.decision.rules.required_bars`),
# without asking a vendor for history nothing reads. Mirrors
# `run_backtest`'s `window_bars` in role; a fixed default rather than a
# guess per call site.
DEFAULT_OUTPUT_SIZE = 200

# Fixed sentences, chosen here at development time and never composed at
# runtime. See `NotConnectedPanel` for why these panels can hold nothing else.
NEWS_NOT_CONNECTED = (
    "No news source is connected in this build, so ORION has nothing to report here."
)
MACRO_NOT_CONNECTED = (
    "No macro source is connected in this build, so ORION has nothing to report here."
)

# The remaining `MacroPanel.detail` sentences. Fixed strings from this module,
# exactly like the two above: nothing here is ever composed at runtime from a
# vendor response, an exception message or an event title.
MACRO_AVAILABLE = "Macro context from the economic calendar, assessed by the Macro Analyst."
MACRO_UNAVAILABLE = (
    "ORION could not read the economic calendar for this analysis, so there is no macro context."
)
MACRO_NO_EVENTS = "The economic calendar returned no events in this window."

# How far either side of `now` the calendar is read. Two days back catches the
# prints a market is still digesting; five forward is the horizon a session is
# actually positioning for. Deliberately constants rather than settings: this
# is what "macro context" means in this build, not a per-deployment knob.
MACRO_LOOKBACK = timedelta(days=2)
MACRO_LOOKAHEAD = timedelta(days=5)


class AnalysisError(RuntimeError):
    """Raised when an analysis cannot be produced at all.

    Distinct from every provider error this module deliberately lets
    through: those describe a stage failing, this describes the request
    itself being unrunnable. Never raised to express "the analysis says
    wait" — a WAIT proposal is a complete, successful analysis.
    """


class AnalysisDataQualityError(AnalysisError):
    """Raised when the fetched market data fails `validate_snapshot`.

    A distinct subclass, matching this codebase's established pattern
    (`EvidenceMismatchError`, `PortfolioLinkageError`), so a caller that
    wants to say "the market data was not good enough" can do so without
    catching every other unrunnable-request case. The message names only
    `DataQualityIssue` enum values — never the finding details, which embed
    timestamps and would vary run to run.
    """


def build_analysis_id(symbol: str, timeframe: Timeframe, as_of: datetime) -> str:
    """A deterministic identifier for one analysis.

    Derived from what the analysis *is* rather than minted with `uuid4()`,
    for exactly the reason `run_backtest` derives its own the same way: two
    runs over the same bar must be recognisably the same analysis, and an
    identifier that changes every call makes a journaled record
    unreproducible from its own inputs. `as_of` is the evidence's latest bar
    timestamp, not wall-clock time, so re-asking the same question before a
    new bar closes yields the same id.
    """
    return f"analysis-{symbol}-{timeframe.value}-{as_of.isoformat()}"


def _risk_panel(
    *,
    portfolio_store: PortfolioStore | None,
    decision: ProposedDecision,
    evidence: Evidence,
    policy: RiskPolicy,
    now: datetime,
) -> RiskPanel:
    """Consult the Risk Guardian, or say exactly why it was not consulted."""
    if portfolio_store is None:
        return RiskPanel(availability=RiskAvailability.NO_PORTFOLIO_STORE)

    try:
        record = portfolio_store.load()
    except PortfolioStoreError:
        # The account could not be read. The exception's text is discarded:
        # it can name a database path, and nothing here needs it to decide
        # that the honest answer is "no risk review".
        return RiskPanel(availability=RiskAvailability.PORTFOLIO_UNAVAILABLE)

    stored = record.state
    if stored.symbol != evidence.symbol:
        # A paper account for a different instrument cannot review this
        # proposal, and `review()` would raise `PortfolioLinkageError` if
        # handed one. Reported rather than raised: the rest of the analysis
        # is still perfectly real.
        return RiskPanel(
            availability=RiskAvailability.PORTFOLIO_SYMBOL_MISMATCH,
            portfolio_as_of=stored.as_of,
        )

    try:
        # Required, not optional: `review()` independently re-verifies that
        # `equity` equals cash plus the open position's unrealised P&L at
        # `evidence.last_price`, and raises `PortfolioIntegrityError`
        # otherwise. Marking forward is a pure projection — the result is
        # never saved, so this reads the account without touching it.
        marked = mark_to_market(stored, now=now, last_price=evidence.last_price)
    except ValueError:
        # A stored state timestamped after `now` (clock skew, or a record
        # written by a future run). Refused rather than moved backwards.
        return RiskPanel(
            availability=RiskAvailability.PORTFOLIO_UNAVAILABLE,
            portfolio_as_of=stored.as_of,
        )

    verdict = review(decision, evidence, policy=policy, portfolio=marked, now=now)
    return RiskPanel(
        availability=RiskAvailability.AVAILABLE,
        verdict=verdict,
        # The *stored* record's own timestamp, not the marked projection's.
        # See `RiskPanel`'s docstring.
        portfolio_as_of=stored.as_of,
    )


def _macro_panel(
    *,
    calendar_provider: EconomicCalendarProvider | None,
    macro_analyst: MacroAnalystProvider | None,
    now: datetime,
) -> MacroPanel:
    """Read the economic calendar and assess it, or say honestly why not.

    FAIL-SOFT, AND DELIBERATELY UNLIKE EVERY OTHER STAGE HERE. The module
    docstring's fail-closed rule exists because market data, reasoning, the
    decision engine and the Risk Guardian each produce something the
    *proposal* depends on -- a partial one of those is a wrong one. Macro is
    context for the person reading the report and nothing downstream consumes
    it, so a calendar outage must not destroy an otherwise complete analysis.
    It must not invent one either, which is what `MACRO_UNAVAILABLE` is for:
    the failure is reported as a state, never smoothed into silence and never
    filled in with a neutral-looking reading.

    The exception is caught broadly and discarded without being read.
    `EconomicCalendarProviderError` and `MacroAnalystProviderError` carry
    messages that may name a vendor or a URL -- `orion.http_errors` exists
    because one of them once embedded an API key in one -- and none of that
    may reach a field this report will serialise.
    """
    if calendar_provider is None or macro_analyst is None:
        return MacroPanel(availability=MacroAvailability.NOT_CONNECTED, detail=MACRO_NOT_CONNECTED)

    try:
        snapshot = calendar_provider.fetch_calendar(now - MACRO_LOOKBACK, now + MACRO_LOOKAHEAD)
        # The same quality gate Mission 7 built. A snapshot with a blocking
        # issue is not handed to the analyst: assessing data ORION already
        # knows is bad would produce a confident opinion about nothing.
        if not validate_calendar_snapshot(snapshot, now=now).is_valid:
            return MacroPanel(
                availability=MacroAvailability.MACRO_UNAVAILABLE, detail=MACRO_UNAVAILABLE
            )
        if not snapshot.events:
            return MacroPanel(availability=MacroAvailability.NO_EVENTS, detail=MACRO_NO_EVENTS)
        opinion = macro_analyst.analyze(snapshot)
    except Exception:
        return MacroPanel(
            availability=MacroAvailability.MACRO_UNAVAILABLE, detail=MACRO_UNAVAILABLE
        )

    return MacroPanel(
        availability=MacroAvailability.AVAILABLE, detail=MACRO_AVAILABLE, opinion=opinion
    )


def run_analysis(
    *,
    intent: Intent,
    market_data: MarketDataProvider,
    reasoning_provider: ReasoningProvider,
    policy: RiskPolicy,
    portfolio_store: PortfolioStore | None,
    now: datetime,
    output_size: int = DEFAULT_OUTPUT_SIZE,
    calendar_provider: EconomicCalendarProvider | None = None,
    macro_analyst: MacroAnalystProvider | None = None,
) -> AnalysisReport:
    """Run one complete live analysis for `intent` and return what it found.

    Raises `AnalysisError` if `intent` is not an analyze intent, if `now` is
    naive, or if `output_size` is not positive; `AnalysisDataQualityError`
    if the fetched snapshot fails validation. Propagates (never catches)
    `MarketDataProviderError`, `ReasoningProviderError`, `DecisionEngineError`
    and `RiskGuardianError` — see the module docstring's fail-closed note.

    Otherwise always returns a complete `AnalysisReport`, including when the
    proposal is WAIT and when the Risk Guardian vetoes: both are real,
    fully auditable answers, not failures.
    """
    if intent.kind is not IntentKind.ANALYZE:
        raise AnalysisError(
            f"intent.kind is {intent.kind.value!r}; run_analysis requires an ANALYZE intent"
        )
    # Guaranteed by `Intent`'s own validator; restated so mypy sees it and so
    # a future relaxation of that validator cannot silently reach the network
    # with `None`.
    if intent.symbol is None or intent.timeframe is None:
        raise AnalysisError("an ANALYZE intent must carry both a symbol and a timeframe")
    if now.tzinfo is None:
        raise AnalysisError("now must be a timezone-aware datetime")
    if output_size < 1:
        raise AnalysisError(f"output_size must be at least 1, got {output_size}")

    symbol, timeframe = intent.symbol, intent.timeframe

    snapshot = market_data.fetch_ohlcv(symbol, timeframe, output_size)

    validation = validate_snapshot(
        snapshot,
        now=now,
        # The Risk Guardian's own staleness threshold, not a second
        # independently-chosen number — `orion.risk.controls.evidence_is_fresh`
        # re-checks the same figure after reasoning, and the two must not
        # disagree about what "stale" means.
        max_staleness_bars=policy.max_staleness_bars,
    )
    if not validation.is_valid:
        issues = ", ".join(sorted({finding.issue.value for finding in validation.findings}))
        raise AnalysisDataQualityError(
            f"market data for {symbol}/{timeframe.value} is not usable: {issues}"
        )

    evidence = build_evidence(snapshot)
    reasoning = reasoning_provider.reason(evidence)

    analysis_id = build_analysis_id(symbol, timeframe, evidence.as_of)
    decision = decide(evidence, reasoning, analysis_id=analysis_id, now=now)

    risk = _risk_panel(
        portfolio_store=portfolio_store,
        decision=decision,
        evidence=evidence,
        policy=policy,
        now=now,
    )

    # Last, and after the decision exists: macro is context attached to a
    # finished analysis, never an input to one. Running it here rather than
    # earlier makes that ordering structural instead of a convention.
    macro = _macro_panel(calendar_provider=calendar_provider, macro_analyst=macro_analyst, now=now)

    return AnalysisReport(
        analysis_id=analysis_id,
        symbol=symbol,
        timeframe=timeframe,
        analyzed_at=now,
        orchestrator_version=ORCHESTRATOR_VERSION,
        evidence=evidence,
        reasoning=reasoning,
        decision=decision,
        risk=risk,
        news=NotConnectedPanel(detail=NEWS_NOT_CONNECTED),
        macro=macro,
    )


class AnalysisService:
    """`run_analysis` with its dependencies already attached.

    Exists so a caller that runs many analyses — the response provider next
    door, and the HTTP layer after it — states its wiring once instead of
    repeating six keyword arguments at every call site. It holds no mutable
    state, no cache and no clock: `run` still requires `now`, and every call
    is independent. Mirrors `orion.interface.service.VoiceTurnService`'s
    shape exactly, including taking its dependencies as plain injected
    objects so it is constructible with no environment.
    """

    def __init__(
        self,
        *,
        market_data: MarketDataProvider,
        reasoning_provider: ReasoningProvider,
        policy: RiskPolicy,
        portfolio_store: PortfolioStore | None = None,
        output_size: int = DEFAULT_OUTPUT_SIZE,
        calendar_provider: EconomicCalendarProvider | None = None,
        macro_analyst: MacroAnalystProvider | None = None,
    ) -> None:
        self._market_data = market_data
        self._reasoning_provider = reasoning_provider
        self._policy = policy
        self._portfolio_store = portfolio_store
        self._output_size = output_size
        # Both default to None, so every existing caller keeps the exact
        # behaviour it had: macro reports NOT_CONNECTED and no second vendor
        # is contacted.
        self._calendar_provider = calendar_provider
        self._macro_analyst = macro_analyst

    def run(self, intent: Intent, *, now: datetime) -> AnalysisReport:
        return run_analysis(
            intent=intent,
            market_data=self._market_data,
            reasoning_provider=self._reasoning_provider,
            policy=self._policy,
            portfolio_store=self._portfolio_store,
            now=now,
            output_size=self._output_size,
            calendar_provider=self._calendar_provider,
            macro_analyst=self._macro_analyst,
        )
