"""The Macro Analyst, wired into an analysis at last.

Missions 7 and 8 built the economic-calendar data layer and the Macro Analyst
and left both unconnected: `FINNHUB_API_KEY` had no consumer anywhere in
`src/`, and every analysis ORION produced said "No macro source is connected
in this build". This suite covers the connection.

TWO PROPERTIES MATTER MORE THAN THE REST, and both have their own class
below. Macro is context, so a calendar outage must not destroy an otherwise
complete analysis -- and macro is *advisory*, so nothing it says may reach the
proposal or the Risk Guardian. The second is asserted by comparing whole
decisions across macro states, not by reading the implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.analysts.provider_base import MacroAnalystProvider, MacroAnalystProviderError
from orion.analysts.schemas import (
    MacroAnalystOutput,
    MacroEventAssessment,
    MacroPolicyPressure,
    MarketRelevance,
    SurpriseDirection,
)
from orion.config import Settings
from orion.orchestrator.analysis import (
    MACRO_AVAILABLE,
    MACRO_NO_EVENTS,
    MACRO_NOT_CONNECTED,
    MACRO_UNAVAILABLE,
    run_analysis,
)
from orion.orchestrator.intent import parse_intent
from orion.orchestrator.schemas import AnalysisReport, MacroAvailability
from orion.orchestrator.wiring import (
    analysis_available,
    build_analysis_service,
    macro_available,
)
from orion.research.models import (
    EconomicCalendarEvent,
    EconomicCalendarSnapshot,
    EconomicCalendarSourceMeta,
    ImpactLevel,
)
from orion.research.provider_base import (
    EconomicCalendarProvider,
    EconomicCalendarProviderError,
)
from orion.risk.policy import RiskPolicy

from .conftest import NOW, FakeMarketDataProvider, FakeReasoningProvider

ANALYZE_GOLD = parse_intent("analyze gold")

# Planted inside vendor error messages, to prove none of them reaches the
# report. Fake, and never a credential for anything.
FAKE_SECRET = "fh-live-supersecret-1234567890"

EVENT_ID = "a" * 64


def make_calendar(*, events: int = 2, retrieved_at: datetime = NOW) -> EconomicCalendarSnapshot:
    return EconomicCalendarSnapshot(
        source=EconomicCalendarSourceMeta(
            provider="fake",
            query_start=retrieved_at - timedelta(days=2),
            query_end=retrieved_at + timedelta(days=5),
            retrieved_at=retrieved_at,
        ),
        events=[
            EconomicCalendarEvent(
                event_time=retrieved_at + timedelta(hours=index + 1),
                country="US",
                event_name=f"CPI print {index}",
                impact=ImpactLevel.HIGH,
                actual=3.2,
                estimate=3.0,
                previous=3.1,
            )
            for index in range(events)
        ],
    )


def make_opinion(*, retrieved_at: datetime = NOW) -> MacroAnalystOutput:
    return MacroAnalystOutput(
        policy_pressure=MacroPolicyPressure.TIGHTENING_PRESSURE,
        market_relevance=MarketRelevance.HIGH,
        confidence=0.72,
        key_event_assessments=(
            MacroEventAssessment(
                event_id=EVENT_ID,
                surprise=SurpriseDirection.ABOVE_ESTIMATE,
                note="hotter than expected",
            ),
        ),
        rationale="US prints came in above estimate across the batch.",
        model="fake-model",
        prompt_version="fake-v1",
        input_fingerprint="b" * 64,
        source_provider="fake",
        query_start=retrieved_at - timedelta(days=2),
        query_end=retrieved_at + timedelta(days=5),
        retrieved_at=retrieved_at,
        analyzed_at=retrieved_at,
    )


class FakeCalendarProvider(EconomicCalendarProvider):
    """Returns a fixed snapshot, or raises a caller-chosen error."""

    def __init__(
        self,
        snapshot: EconomicCalendarSnapshot | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._snapshot = snapshot if snapshot is not None else make_calendar()
        self._error = error
        self.windows: list[tuple[datetime, datetime]] = []

    def fetch_calendar(self, start: datetime, end: datetime) -> EconomicCalendarSnapshot:
        self.windows.append((start, end))
        if self._error is not None:
            raise self._error
        return self._snapshot


class FakeMacroAnalyst(MacroAnalystProvider):
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[EconomicCalendarSnapshot] = []

    def analyze(self, snapshot: EconomicCalendarSnapshot) -> MacroAnalystOutput:
        self.calls.append(snapshot)
        if self._error is not None:
            raise self._error
        return make_opinion()


def analyse(
    *,
    policy: RiskPolicy,
    calendar_provider: EconomicCalendarProvider | None = None,
    macro_analyst: MacroAnalystProvider | None = None,
) -> AnalysisReport:
    return run_analysis(
        intent=ANALYZE_GOLD,
        market_data=FakeMarketDataProvider(),
        reasoning_provider=FakeReasoningProvider(),
        policy=policy,
        portfolio_store=None,
        now=NOW,
        calendar_provider=calendar_provider,
        macro_analyst=macro_analyst,
    )


# ------------------------------------------------------------ the four states


class TestTheFourMacroStates:
    def test_not_connected_when_neither_provider_is_wired(
        self, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(policy=permissive_policy)

        assert report.macro.availability is MacroAvailability.NOT_CONNECTED
        assert report.macro.detail == MACRO_NOT_CONNECTED
        assert report.macro.opinion is None

    @pytest.mark.parametrize("half", ["calendar", "analyst"])
    def test_a_half_wired_pair_is_not_connected_and_calls_nobody(
        self, half: str, permissive_policy: RiskPolicy
    ) -> None:
        """One without the other is a capability that cannot answer, so it
        must not half-run and must not contact a vendor."""
        calendar = FakeCalendarProvider()
        analyst = FakeMacroAnalyst()

        report = analyse(
            policy=permissive_policy,
            calendar_provider=calendar if half == "calendar" else None,
            macro_analyst=analyst if half == "analyst" else None,
        )

        assert report.macro.availability is MacroAvailability.NOT_CONNECTED
        assert calendar.windows == []
        assert analyst.calls == []

    def test_available_when_both_are_wired_and_the_calendar_has_events(
        self, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(
            policy=permissive_policy,
            calendar_provider=FakeCalendarProvider(),
            macro_analyst=FakeMacroAnalyst(),
        )

        assert report.macro.availability is MacroAvailability.AVAILABLE
        assert report.macro.detail == MACRO_AVAILABLE
        assert report.macro.opinion is not None
        assert report.macro.opinion.policy_pressure is MacroPolicyPressure.TIGHTENING_PRESSURE
        assert report.macro.opinion.confidence == pytest.approx(0.72)

    def test_no_events_is_its_own_state_and_never_reaches_the_analyst(
        self, permissive_policy: RiskPolicy
    ) -> None:
        analyst = FakeMacroAnalyst()

        report = analyse(
            policy=permissive_policy,
            calendar_provider=FakeCalendarProvider(make_calendar(events=0)),
            macro_analyst=analyst,
        )

        assert report.macro.availability is MacroAvailability.NO_EVENTS
        assert report.macro.detail == MACRO_NO_EVENTS
        assert report.macro.opinion is None
        # An empty batch is not something to ask a paid model about.
        assert analyst.calls == []

    def test_a_stale_snapshot_is_refused_before_the_analyst_sees_it(
        self, permissive_policy: RiskPolicy
    ) -> None:
        """Mission 7's quality gate still runs. Assessing data ORION already
        knows is bad would produce a confident opinion about nothing."""
        analyst = FakeMacroAnalyst()
        stale = make_calendar(retrieved_at=NOW - timedelta(days=30))

        report = analyse(
            policy=permissive_policy,
            calendar_provider=FakeCalendarProvider(stale),
            macro_analyst=analyst,
        )

        assert report.macro.availability is MacroAvailability.MACRO_UNAVAILABLE
        assert analyst.calls == []

    @pytest.mark.parametrize(
        ("where", "error"),
        [
            ("calendar", EconomicCalendarProviderError(f"finnhub 401 for token {FAKE_SECRET}")),
            ("analyst", MacroAnalystProviderError(f"anthropic rejected {FAKE_SECRET}")),
            ("calendar", RuntimeError(f"unexpected {FAKE_SECRET}")),
        ],
    )
    def test_a_failure_anywhere_is_reported_as_unavailable(
        self, where: str, error: Exception, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(
            policy=permissive_policy,
            calendar_provider=FakeCalendarProvider(error=error if where == "calendar" else None),
            macro_analyst=FakeMacroAnalyst(error=error if where == "analyst" else None),
        )

        assert report.macro.availability is MacroAvailability.MACRO_UNAVAILABLE
        assert report.macro.detail == MACRO_UNAVAILABLE
        assert report.macro.opinion is None
        # The vendor's own words never reach the report.
        blob = report.model_dump_json()
        assert FAKE_SECRET not in blob
        assert "supersecret" not in blob
        assert "finnhub" not in blob.lower()
        assert type(error).__name__ not in blob


# ------------------------------------------------------------------ fail-soft


class TestMacroNeverBreaksTheAnalysis:
    @pytest.mark.parametrize(
        "calendar",
        [
            FakeCalendarProvider(error=EconomicCalendarProviderError("down")),
            FakeCalendarProvider(make_calendar(events=0)),
            FakeCalendarProvider(),
        ],
    )
    def test_the_rest_of_the_report_is_complete_whatever_macro_did(
        self, calendar: FakeCalendarProvider, permissive_policy: RiskPolicy
    ) -> None:
        report = analyse(
            policy=permissive_policy,
            calendar_provider=calendar,
            macro_analyst=FakeMacroAnalyst(),
        )

        assert report.evidence.bar_count > 0
        assert report.reasoning.confidence >= 0.0
        assert report.decision.analysis_id == report.analysis_id
        assert report.risk is not None

    def test_the_calendar_window_is_the_documented_one(self, permissive_policy: RiskPolicy) -> None:
        calendar = FakeCalendarProvider()

        analyse(
            policy=permissive_policy,
            calendar_provider=calendar,
            macro_analyst=FakeMacroAnalyst(),
        )

        start, end = calendar.windows[0]
        assert start == NOW - timedelta(days=2)
        assert end == NOW + timedelta(days=5)
        assert start.tzinfo is not None and end.tzinfo is not None


# ------------------------------------------------------------------ advisory


class TestMacroIsAdvisoryOnly:
    def test_the_decision_is_byte_identical_with_and_without_macro(
        self, permissive_policy: RiskPolicy
    ) -> None:
        """The load-bearing assertion of this whole capability.

        A macro read that could move the proposal would be an AI opinion
        acquiring authority the Risk Guardian never granted it. Comparing the
        whole serialised decision -- not one field -- is what makes that
        impossible to regress quietly.
        """
        without = analyse(policy=permissive_policy)
        with_macro = analyse(
            policy=permissive_policy,
            calendar_provider=FakeCalendarProvider(),
            macro_analyst=FakeMacroAnalyst(),
        )

        assert with_macro.macro.availability is MacroAvailability.AVAILABLE
        assert without.macro.availability is MacroAvailability.NOT_CONNECTED
        assert with_macro.decision.model_dump_json() == without.decision.model_dump_json()
        assert with_macro.risk.model_dump_json() == without.risk.model_dump_json()
        assert with_macro.evidence.model_dump_json() == without.evidence.model_dump_json()
        assert with_macro.reasoning.model_dump_json() == without.reasoning.model_dump_json()

    def test_the_evidence_the_model_reasoned_over_contains_no_macro(
        self, permissive_policy: RiskPolicy
    ) -> None:
        reasoning_provider = FakeReasoningProvider()

        run_analysis(
            intent=ANALYZE_GOLD,
            market_data=FakeMarketDataProvider(),
            reasoning_provider=reasoning_provider,
            policy=permissive_policy,
            portfolio_store=None,
            now=NOW,
            calendar_provider=FakeCalendarProvider(),
            macro_analyst=FakeMacroAnalyst(),
        )

        # Whatever Evidence the reasoning provider was handed, no calendar
        # event and no policy-pressure reading is in it.
        blob = "".join(evidence.model_dump_json() for evidence in reasoning_provider.calls)
        assert "CPI print" not in blob
        assert "tightening_pressure" not in blob

    def test_news_is_still_untouched(self, permissive_policy: RiskPolicy) -> None:
        """Only macro was wired. News says so, and still has nowhere to put a
        headline."""
        report = analyse(
            policy=permissive_policy,
            calendar_provider=FakeCalendarProvider(),
            macro_analyst=FakeMacroAnalyst(),
        )

        assert set(report.news.model_dump()) == {"status", "detail"}
        assert report.news.status.value == "not_connected"


# ------------------------------------------------------------------- schema


class TestThePanelCannotLie:
    def test_an_opinion_without_availability_is_unrepresentable(self) -> None:
        from orion.orchestrator.schemas import MacroPanel

        with pytest.raises(ValueError, match="may only accompany AVAILABLE"):
            MacroPanel(
                availability=MacroAvailability.NOT_CONNECTED,
                detail=MACRO_NOT_CONNECTED,
                opinion=make_opinion(),
            )

    def test_availability_without_an_opinion_is_unrepresentable(self) -> None:
        from orion.orchestrator.schemas import MacroPanel

        with pytest.raises(ValueError, match="no opinion is present"):
            MacroPanel(availability=MacroAvailability.AVAILABLE, detail=MACRO_AVAILABLE)

    def test_every_detail_sentence_is_a_module_constant(self) -> None:
        """Nothing here is ever composed at runtime, so nothing a vendor said
        can end up in it."""
        for sentence in (MACRO_NOT_CONNECTED, MACRO_AVAILABLE, MACRO_UNAVAILABLE, MACRO_NO_EVENTS):
            assert isinstance(sentence, str)
            assert 0 < len(sentence) <= 200
            assert not any(char.isdigit() for char in sentence)


def test_datetime_helpers_are_timezone_aware() -> None:
    assert NOW.tzinfo is not None
    assert make_calendar().source.retrieved_at.tzinfo is UTC or NOW.tzinfo is not None


# ------------------------------------------------------------------ wiring


class TestWiringFromSettings:
    """`FINNHUB_API_KEY` had no consumer in `src/` before this phase."""

    @staticmethod
    def settings_for(**overrides: str) -> Settings:
        base = {
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "TWELVE_DATA_API_KEY": "test-twelvedata-key",
            "FINNHUB_API_KEY": "test-finnhub-key",
        }
        base.update(overrides)
        return Settings(_env_file=None, **base)  # type: ignore[arg-type]

    def test_macro_is_available_only_with_a_calendar_credential(self) -> None:
        assert macro_available(self.settings_for()) is True
        assert macro_available(self.settings_for(FINNHUB_API_KEY="")) is False
        assert macro_available(self.settings_for(ANTHROPIC_API_KEY="")) is False

    def test_the_pair_is_built_when_the_credential_is_present(self) -> None:
        service = build_analysis_service(self.settings_for())

        assert service is not None
        assert service._calendar_provider is not None
        assert service._macro_analyst is not None

    def test_neither_is_built_without_the_credential(self) -> None:
        service = build_analysis_service(self.settings_for(FINNHUB_API_KEY=""))

        assert service is not None
        # An analysis still runs; it simply reports macro as not connected,
        # exactly as every build before this phase did.
        assert service._calendar_provider is None
        assert service._macro_analyst is None

    def test_an_analysis_is_still_available_without_a_calendar(self) -> None:
        assert analysis_available(self.settings_for(FINNHUB_API_KEY="")) is True
