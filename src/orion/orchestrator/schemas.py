"""Wire shapes for the Mission 10 Phase 5.1 orchestrator: what ORION was
asked, and what one analysis actually produced.

REUSES DOMAIN MODELS, DOES NOT RE-DECLARE THEM. `AnalysisReport` embeds
`Evidence`, `ReasoningOutput` and `ProposedDecision` whole, exactly as
`orion.voice.runtime.VoiceTurnResult` embeds a full `Transcript` and
`SynthesizedSpeech`. A parallel set of flattened "panel" models restating
`trend`/`rsi`/`confidence` would be a second copy of every field in the
system, free to drift the moment either side is edited. Reducing this to a
client-visible allowlist is the HTTP boundary's job when one exists (see
`orion.interface.schemas.VoiceTurnResponse`, which already does exactly that
reduction for a `VoiceTurnResult`) — not this module's.

The brief's seven requested fields map onto that structure directly:

    market structure -> report.evidence.structure
    momentum         -> report.evidence.indicators
    news             -> report.news        (NOT CONNECTED — see below)
    macro            -> report.macro       (NOT CONNECTED — see below)
    risk             -> report.risk
    confidence       -> report.reasoning.confidence / .risk_level
    conclusion       -> report.decision.action / .direction, .reasoning.rationale

ABSENCE IS A FIRST-CLASS, STRUCTURALLY UNFAKEABLE VALUE. Two of those seven
have no source in this build. No news provider and no News Analyst exist
anywhere in this codebase (`docs/DECISIONS.md`, Mission 7 v1 and Mission 8
v1 both record the deferral and why), and the Macro Analyst path is
deliberately out of Phase 5.1's approved scope. `NotConnectedPanel` is how
they are reported, and it carries **no list, no mapping, no number and no
free-text content field** — only a single-member status enum and a fixed
sentence chosen at development time. There is therefore nowhere in this
schema to put a fabricated headline or an invented macro reading, which is
a stronger guarantee than a convention that nobody does.

The same discipline applies to risk. `RiskPanel` can only carry a real
`RiskVerdict` produced by `orion.risk.guardian.review()` from a real stored
`PortfolioState`; every other case is an explicit `RiskAvailability` reason
and `verdict=None`, enforced by a model validator. A "flat account at the
configured starting balance" would make the position-size, exposure and
loss-limit controls evaluate a fiction while looking authoritative, which is
precisely the failure `orion.paper.store.PortfolioStoreError`'s own
docstring forbids papering over.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.analysis.evidence import Evidence
from orion.analysts.schemas import MacroAnalystOutput
from orion.data.models import Timeframe
from orion.decision.schemas import ProposedDecision
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.schemas import RiskVerdict


class IntentKind(StrEnum):
    """What the user asked for, from the closed set this build understands.

    Not a growing catalogue: `orion.orchestrator.intent` maps an utterance
    onto exactly one thing ORION can do today, and everything else onto
    `UNKNOWN`. A new capability adds a member here *and* a real
    implementation behind it, in the same change — never a member on its own.

    `GREETING` and `CAPABILITIES` are answered entirely locally by
    `orion.orchestrator.response.LocalResponseProvider`: deterministic
    sentences about what ORION is and what this machine can currently do.
    They reach no provider, cost nothing, and work with no credential
    configured at all — which is the point of them. Only `ANALYZE` needs
    anything external.
    """

    ANALYZE = "analyze"
    GREETING = "greeting"
    CAPABILITIES = "capabilities"
    UNKNOWN = "unknown"


class Intent(BaseModel):
    """One parsed utterance.

    `symbol` is never the user's own words: it is a value resolved through
    `orion.orchestrator.intent`'s closed alias table, so the string that
    reaches `MarketDataProvider.fetch_ohlcv` can only ever be one this
    codebase chose. See that module's docstring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: IntentKind
    symbol: str | None = Field(default=None, min_length=1)
    timeframe: Timeframe | None = None

    @model_validator(mode="after")
    def _check_intent_invariants(self) -> Intent:
        resolved = self.symbol is not None and self.timeframe is not None
        if self.kind is IntentKind.ANALYZE and not resolved:
            raise ValueError(
                f"kind is ANALYZE but symbol/timeframe are {self.symbol!r}/"
                f"{self.timeframe!r}; an analyze intent must name both"
            )
        resolved_anything = self.symbol is not None or self.timeframe is not None
        if self.kind is not IntentKind.ANALYZE and resolved_anything:
            raise ValueError(
                f"kind is {self.kind.value.upper()} but symbol/timeframe are {self.symbol!r}/"
                f"{self.timeframe!r}; only an analyze intent resolves either"
            )
        return self


class CapabilityStatus(StrEnum):
    """Deliberately one member.

    A two-member enum would imply a `CONNECTED` case this build can reach,
    and would leave a reviewer wondering where the data for it comes from.
    When news or macro is genuinely wired up, the panel that reports it will
    be a new model carrying real, provenance-stamped values — not this one
    with its status flipped.
    """

    NOT_CONNECTED = "not_connected"


class NotConnectedPanel(BaseModel):
    """A capability the brief asks for that this build does not have.

    Carries no field capable of holding data: no list, no mapping, no
    number, no timestamp. `detail` is a fixed sentence supplied by
    `orion.orchestrator.analysis` from a module constant, never composed at
    runtime from anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapabilityStatus = CapabilityStatus.NOT_CONNECTED
    detail: str = Field(min_length=1, max_length=200)


class MacroAvailability(StrEnum):
    """Why a `MacroPanel` does or does not carry an opinion.

    Every member is a real, reachable condition. `NOT_CONNECTED` is this
    deployment having no economic-calendar credential -- the state every build
    before this one was permanently in. The other two are genuine runtime
    outcomes, and neither is ever answered by inventing macro context.
    """

    AVAILABLE = "available"
    NOT_CONNECTED = "not_connected"
    MACRO_UNAVAILABLE = "macro_unavailable"
    NO_EVENTS = "no_events"


class MacroPanel(BaseModel):
    """The Macro Analyst's opinion, or an explicit reason there isn't one.

    Shaped exactly like `RiskPanel`, and for the same reason: a caller can
    tell "ORION looked and found nothing" apart from "ORION could not look"
    apart from "this build cannot look at all", and no state is answered with
    a fabricated read.

    `detail` stays a fixed sentence from an `orion.orchestrator.analysis`
    module constant, never composed at runtime -- the property
    `NotConnectedPanel` had and this type inherits rather than drops, so a
    client that only ever read `status`/`detail` sees exactly what it always
    did while this deployment has no calendar credential.

    ADVISORY ONLY, AND STRUCTURALLY SO. `opinion` is a `MacroAnalystOutput`:
    a policy-pressure reading, a relevance, a confidence and a rationale,
    stamped with the model, prompt version and the fingerprint of the exact
    calendar snapshot it was computed from. Nothing downstream reads it.
    `orion.decision` never sees it, the Risk Guardian never sees it, and it
    cannot reach `ProposedDecision` -- macro context informs the person
    reading the report, never the machine proposing the trade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    availability: MacroAvailability
    detail: str = Field(min_length=1, max_length=200)
    opinion: MacroAnalystOutput | None = None

    @model_validator(mode="after")
    def _check_macro_invariants(self) -> MacroPanel:
        available = self.availability is MacroAvailability.AVAILABLE
        if available and self.opinion is None:
            raise ValueError("availability is AVAILABLE but no opinion is present")
        if not available and self.opinion is not None:
            raise ValueError(
                f"availability is {self.availability.value!r} but an opinion is present; "
                "an opinion may only accompany AVAILABLE"
            )
        return self


class RiskAvailability(StrEnum):
    """Why a `RiskPanel` does or does not carry a verdict.

    Every non-`AVAILABLE` member is a real, reachable condition, not a
    catch-all: no store was supplied, the store could not be read, or the
    stored account is for a different symbol than the one being analysed.
    None of them is ever answered by inventing an account.
    """

    AVAILABLE = "available"
    NO_PORTFOLIO_STORE = "no_portfolio_store"
    PORTFOLIO_UNAVAILABLE = "portfolio_unavailable"
    PORTFOLIO_SYMBOL_MISMATCH = "portfolio_symbol_mismatch"


class RiskPanel(BaseModel):
    """The Risk Guardian's answer, or an explicit reason there isn't one.

    `portfolio_as_of` is the *stored* snapshot's own timestamp, kept
    separately from the verdict on purpose. `review()` requires a portfolio
    whose `equity` is consistent with the evidence price, so
    `orion.orchestrator.analysis` marks the loaded state to market before
    reviewing it — a pure, unsaved projection. That projection sets
    `as_of` to now, which would otherwise hide how old the underlying record
    actually was. This field is what stops that from being laundered away.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    availability: RiskAvailability
    verdict: RiskVerdict | None = None
    portfolio_as_of: datetime | None = None

    @model_validator(mode="after")
    def _check_risk_invariants(self) -> RiskPanel:
        available = self.availability is RiskAvailability.AVAILABLE
        if available and self.verdict is None:
            raise ValueError("availability is AVAILABLE but no verdict is present")
        if not available and self.verdict is not None:
            raise ValueError(
                f"availability is {self.availability.value!r} but a verdict is present; "
                "a verdict may only accompany AVAILABLE"
            )
        if available and self.portfolio_as_of is None:
            raise ValueError("availability is AVAILABLE but portfolio_as_of is absent")
        if self.portfolio_as_of is not None and self.portfolio_as_of.tzinfo is None:
            raise ValueError("portfolio_as_of must be a timezone-aware datetime")
        return self


class AnalysisReport(BaseModel):
    """Everything one completed analysis produced, returned together.

    Frozen and closed like every schema in this codebase. Nothing here is
    persisted by Phase 5.1 — this object is returned to the caller and is
    unreferenced the moment the caller drops it, matching Mission 7's and
    Mission 8's identical "don't build storage ahead of a proven consumer"
    precedent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: Timeframe
    analyzed_at: datetime
    orchestrator_version: str = Field(min_length=1)

    evidence: Evidence
    reasoning: ReasoningOutput
    decision: ProposedDecision

    risk: RiskPanel
    news: NotConnectedPanel
    macro: MacroPanel

    @model_validator(mode="after")
    def _check_report_invariants(self) -> AnalysisReport:
        if self.analyzed_at.tzinfo is None:
            raise ValueError("analyzed_at must be a timezone-aware datetime")
        if self.evidence.symbol != self.symbol:
            raise ValueError(
                f"report symbol ({self.symbol!r}) does not match evidence.symbol "
                f"({self.evidence.symbol!r})"
            )
        if self.evidence.timeframe is not self.timeframe:
            raise ValueError(
                f"report timeframe ({self.timeframe.value!r}) does not match "
                f"evidence.timeframe ({self.evidence.timeframe.value!r})"
            )
        if self.decision.analysis_id != self.analysis_id:
            raise ValueError(
                f"report analysis_id ({self.analysis_id!r}) does not match "
                f"decision.analysis_id ({self.decision.analysis_id!r})"
            )
        if self.risk.verdict is not None and self.risk.verdict.analysis_id != self.analysis_id:
            raise ValueError(
                f"risk verdict analysis_id ({self.risk.verdict.analysis_id!r}) does not "
                f"match report analysis_id ({self.analysis_id!r})"
            )
        return self
