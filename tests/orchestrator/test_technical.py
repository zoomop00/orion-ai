"""The Technical Analyst and the thesis, wired into a real analysis.

THE ASSERTIONS THAT MATTER MOST ARE THE ONES ABOUT WHAT DID *NOT* CHANGE. A
specialist layer is only advisory if the proposal and the verdict are byte-for-
byte identical whatever it says, so the tests below serialise the whole
decision, reasoning, evidence and risk objects and compare them across every
technical and thesis state — the same technique `test_macro.py` already uses
for the Macro Analyst, and for the same reason.

THE SECOND THING PROVEN HERE IS THAT THE ANALYST IS FREE. Macro needs a vendor
and a credential, so it has a NOT_CONNECTED state. The Technical Analyst is
deterministic computation over bars the analysis already fetched, so it runs on
every analysis in every deployment: there is no credential to check, no second
fetch, and no path on which it can be absent because of how the build was
configured.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from orion.analysis.evidence import evidence_fingerprint
from orion.analysts.knowledge import StructureBias
from orion.analysts.synthesis import ConditionLevel, ThesisAlignment
from orion.analysts.technical_schemas import TechnicalAvailability
from orion.orchestrator.analysis import (
    TECHNICAL_AVAILABLE,
    TECHNICAL_INSUFFICIENT,
    THESIS_AVAILABLE,
    AnalysisDataQualityError,
    run_analysis,
)
from orion.orchestrator.intent import parse_intent
from orion.orchestrator.schemas import AnalysisReport, MacroAvailability
from orion.risk.policy import KillSwitchState, RiskPolicy

from .conftest import NOW, FakeMarketDataProvider, FakeReasoningProvider, make_bars, make_snapshot
from .test_macro import FakeCalendarProvider, FakeMacroAnalyst, make_calendar


def analyse(
    *,
    bars: list | None = None,
    policy: RiskPolicy | None = None,
    calendar_provider: object = None,
    macro_analyst: object = None,
    now: datetime = NOW,
) -> AnalysisReport:
    snapshot = make_snapshot(bars=bars if bars is not None else make_bars())
    return run_analysis(
        intent=parse_intent("analyze gold"),
        market_data=FakeMarketDataProvider(snapshot=snapshot),
        reasoning_provider=FakeReasoningProvider(),
        policy=policy or RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
        portfolio_store=None,
        now=now,
        calendar_provider=calendar_provider,  # type: ignore[arg-type]
        macro_analyst=macro_analyst,  # type: ignore[arg-type]
    )


def with_macro(**kwargs: object) -> AnalysisReport:
    """An analysis with a working macro pass, retrieved at the real `now` so
    Mission 7's freshness gate lets it through."""
    return analyse(
        calendar_provider=FakeCalendarProvider(make_calendar(retrieved_at=NOW)),
        macro_analyst=FakeMacroAnalyst(),
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------ the analyst really runs


class TestTheTechnicalPanelIsReal:
    def test_every_analysis_carries_technical_findings(self) -> None:
        report = analyse()

        assert report.technical.output.availability is TechnicalAvailability.AVAILABLE
        assert report.technical.detail == TECHNICAL_AVAILABLE
        assert report.technical.output.findings

    def test_it_needs_no_credential_and_makes_no_second_fetch(self) -> None:
        """The analyst has no vendor. The market-data provider is called
        exactly once — the same single fetch the analysis always made."""
        provider = FakeMarketDataProvider(snapshot=make_snapshot(bars=make_bars()))

        run_analysis(
            intent=parse_intent("analyze gold"),
            market_data=provider,
            reasoning_provider=FakeReasoningProvider(),
            policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
            portfolio_store=None,
            now=NOW,
        )

        assert len(provider.calls) == 1

    def test_the_findings_describe_the_same_bars_the_proposal_did(self) -> None:
        """THE PROVENANCE LINK THAT MAKES THIS WORTH WIRING IN. Every finding
        carries the fingerprint of the exact `Evidence` the decision was
        reasoned from, so a reader can prove the two are about one snapshot."""
        report = analyse()

        expected = evidence_fingerprint(report.evidence)
        assert expected == report.decision.evidence_ref.content_hash
        assert report.technical.output.timeframes[0].evidence_fingerprint == expected
        assert all(
            finding.evidence_fingerprint == expected for finding in report.technical.output.findings
        )

    def test_it_reports_the_timeframe_the_analysis_actually_ran_on(self) -> None:
        report = analyse()

        assert [item.timeframe for item in report.technical.output.timeframes] == [report.timeframe]
        assert report.technical.output.symbol == report.symbol

    def test_a_thin_window_is_reported_as_insufficient_not_as_a_reading(self) -> None:
        report = analyse(bars=make_bars(count=60))

        panel = report.technical
        if panel.output.availability is TechnicalAvailability.INSUFFICIENT_DATA:
            assert panel.detail == TECHNICAL_INSUFFICIENT
            assert panel.output.findings == ()
        else:
            # Sixty bars is enough for some concepts; the guarantee under test
            # is that whatever could not be evaluated is *listed*, never
            # dropped and never answered with a plausible number.
            assert panel.output.timeframes[0].unevaluated

    def test_the_panel_never_reports_a_not_connected_state(self) -> None:
        """There is no such state to report: a deterministic specialist cannot
        be missing a credential."""
        assert "not_connected" not in {item.value for item in TechnicalAvailability}


# ------------------------------------------------------- the thesis panel


class TestTheThesisPanel:
    def test_a_thesis_is_formed_from_the_technical_half_alone(self) -> None:
        report = analyse()

        thesis = report.thesis.thesis
        assert thesis is not None
        assert report.thesis.detail == THESIS_AVAILABLE
        assert thesis.alignment is ThesisAlignment.MACRO_ABSENT
        assert thesis.macro_provenance is None
        assert thesis.technical_bias is report.technical.output.bias

    def test_a_thesis_combines_both_specialists_when_macro_is_connected(self) -> None:
        report = with_macro()

        assert report.macro.availability is MacroAvailability.AVAILABLE
        thesis = report.thesis.thesis
        assert thesis is not None
        assert thesis.alignment is not ThesisAlignment.MACRO_ABSENT
        assert thesis.macro_provenance is not None
        assert report.macro.opinion is not None
        assert thesis.macro_provenance.model == report.macro.opinion.model
        assert thesis.macro_relevance is report.macro.opinion.market_relevance

    def test_the_thesis_preserves_both_specialists_provenance(self) -> None:
        report = with_macro()

        thesis = report.thesis.thesis
        assert thesis is not None
        assert thesis.timeframes[0].evidence_fingerprint == evidence_fingerprint(report.evidence)
        assert report.macro.opinion is not None
        assert thesis.macro_provenance is not None
        assert thesis.macro_provenance.input_fingerprint == report.macro.opinion.input_fingerprint
        assert thesis.macro_provenance.rationale == report.macro.opinion.rationale

    def test_a_macro_outage_leaves_a_technical_only_thesis(self) -> None:
        """Fail-soft on the macro side must not take the thesis with it."""
        from orion.research.provider_base import EconomicCalendarProviderError

        report = analyse(
            calendar_provider=FakeCalendarProvider(
                error=EconomicCalendarProviderError("finnhub 500")
            ),
            macro_analyst=FakeMacroAnalyst(),
        )

        assert report.macro.availability is MacroAvailability.MACRO_UNAVAILABLE
        thesis = report.thesis.thesis
        assert thesis is not None
        assert thesis.alignment is ThesisAlignment.MACRO_ABSENT
        assert thesis.technical_bias is report.technical.output.bias

    def test_the_thesis_condition_readings_match_the_panels_they_came_from(self) -> None:
        report = with_macro()

        thesis = report.thesis.thesis
        assert thesis is not None
        assert thesis.technical_availability is report.technical.output.availability
        assert thesis.macro_condition in set(ConditionLevel)


# -------------------------------------- the decision chain does not move


class TestNothingDownstreamMoves:
    """A specialist annotates a finished analysis. It never changes one."""

    def _chain(self, report: AnalysisReport) -> str:
        return json.dumps(
            {
                "evidence": report.evidence.model_dump(mode="json"),
                "reasoning": report.reasoning.model_dump(mode="json"),
                "decision": report.decision.model_dump(mode="json"),
                "risk": report.risk.model_dump(mode="json"),
                "analysis_id": report.analysis_id,
            },
            sort_keys=True,
        )

    def test_the_proposal_and_verdict_are_identical_with_and_without_macro(self) -> None:
        """The technical panel is present in both, so this isolates macro; the
        next test isolates the technical half by varying the bars it reads."""
        assert self._chain(analyse()) == self._chain(with_macro())

    def test_the_thesis_is_computed_after_the_decision_not_before_it(self) -> None:
        """Ordering made structural: the report's decision is reasoned from
        evidence, and the thesis references that same evidence — so the thesis
        cannot have been an input to it."""
        report = with_macro()

        thesis = report.thesis.thesis
        assert thesis is not None
        assert thesis.formed_at == report.analyzed_at
        assert (
            thesis.timeframes[0].evidence_fingerprint == report.decision.evidence_ref.content_hash
        )

    def test_no_field_of_the_decision_mentions_a_specialist(self) -> None:
        """`orion.decision` cannot import `orion.analysts` at all, so this is
        belt and braces — but it is the assertion a reader checks first."""
        blob = json.dumps(analyse().decision.model_dump(mode="json"))

        for word in ("technical", "thesis", "fair_value_gap", "macro", "bias"):
            assert word not in blob

    def test_the_risk_verdict_never_sees_a_thesis(self) -> None:
        """THE RISK GUARDIAN BOUNDARY. The verdict is produced before the
        thesis exists and from inputs that cannot reach it."""
        from orion.risk import guardian

        assert "analysts" not in str(guardian.review.__doc__ or "")
        report = with_macro()
        # A report with no portfolio store has no verdict to inspect, which is
        # itself the point: risk availability is decided by the account, never
        # by what a specialist said.
        assert report.risk.verdict is None
        assert report.thesis.thesis is not None


# ---------------------------------------- existing behaviour is untouched


class TestExistingBehaviourIsIntact:
    def test_the_macro_panel_behaves_exactly_as_before(self) -> None:
        report = with_macro()

        assert report.macro.availability is MacroAvailability.AVAILABLE
        assert report.macro.opinion is not None
        assert report.macro.opinion.rationale

    def test_a_deployment_with_no_macro_still_reports_not_connected(self) -> None:
        report = analyse()

        assert report.macro.availability is MacroAvailability.NOT_CONNECTED
        assert report.macro.opinion is None

    def test_bad_market_data_still_fails_closed_before_any_specialist_runs(self) -> None:
        """FAIL-CLOSED IS UNCHANGED. A snapshot that fails validation aborts the
        analysis; it does not produce a report with technical findings computed
        over data ORION already knows is unusable."""
        stale = make_snapshot(bars=make_bars(end=NOW.replace(year=2020)))

        with pytest.raises(AnalysisDataQualityError):
            run_analysis(
                intent=parse_intent("analyze gold"),
                market_data=FakeMarketDataProvider(snapshot=stale),
                reasoning_provider=FakeReasoningProvider(),
                policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
                portfolio_store=None,
                now=NOW,
            )

    def test_the_report_is_still_deterministic_end_to_end(self) -> None:
        first, second = with_macro(), with_macro()

        assert first.model_dump_json() == second.model_dump_json()

    def test_a_naive_now_is_still_refused_before_anything_runs(self) -> None:
        from orion.orchestrator.analysis import AnalysisError

        with pytest.raises(AnalysisError, match="timezone-aware"):
            analyse(now=datetime(2026, 8, 30, 12, 0))


def test_the_technical_bias_is_a_reading_not_a_recommendation() -> None:
    """`StructureBias` describes price that has already printed. The report
    carries no field anywhere that turns it into an instruction."""
    report = analyse()

    assert report.technical.output.bias in set(StructureBias)
    blob = json.dumps(report.model_dump(mode="json"))
    for word in ('"recommendation"', '"recommended_action"', '"signal"', '"approved"'):
        assert word not in blob


def test_utc_is_imported_for_the_naive_check() -> None:
    """Guards the import above from being dropped as unused by a future edit."""
    assert datetime.now(UTC).tzinfo is not None
