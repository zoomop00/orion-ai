"""The report's shape, and the invariants that make absence unfakeable.

`NotConnectedPanel` is checked structurally rather than by convention: the
point of the model is that there is nowhere in it to put a headline, so the
test asserts the field set itself, not just today's values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from orion.data.models import Timeframe
from orion.orchestrator.schemas import (
    AnalysisReport,
    CapabilityStatus,
    Intent,
    IntentKind,
    NotConnectedPanel,
    RiskAvailability,
    RiskPanel,
)

NOW = datetime(2026, 8, 28, 12, 5, tzinfo=UTC)


class TestIntent:
    def test_an_analyze_intent_must_name_a_symbol_and_a_timeframe(self) -> None:
        with pytest.raises(ValidationError):
            Intent(kind=IntentKind.ANALYZE)
        with pytest.raises(ValidationError):
            Intent(kind=IntentKind.ANALYZE, symbol="XAUUSD")
        with pytest.raises(ValidationError):
            Intent(kind=IntentKind.ANALYZE, timeframe=Timeframe.H1)

    def test_an_unknown_intent_may_resolve_nothing(self) -> None:
        with pytest.raises(ValidationError):
            Intent(kind=IntentKind.UNKNOWN, symbol="XAUUSD", timeframe=Timeframe.H1)
        with pytest.raises(ValidationError):
            Intent(kind=IntentKind.UNKNOWN, symbol="XAUUSD")

    def test_is_frozen_and_closed(self) -> None:
        intent = Intent(kind=IntentKind.UNKNOWN)
        with pytest.raises(ValidationError):
            intent.kind = IntentKind.ANALYZE  # type: ignore[misc]
        with pytest.raises(ValidationError):
            Intent(kind=IntentKind.UNKNOWN, unexpected="x")  # type: ignore[call-arg]


class TestNotConnectedPanel:
    def test_has_exactly_one_status_and_it_is_not_connected(self) -> None:
        assert list(CapabilityStatus) == [CapabilityStatus.NOT_CONNECTED]

    def test_carries_no_field_capable_of_holding_data(self) -> None:
        # The structural guarantee: there is no list, mapping, number or
        # timestamp field here, so a fabricated headline or an invented
        # macro reading has nowhere to go. Asserting the exact field set
        # means adding one is a deliberate, visible act.
        assert set(NotConnectedPanel.model_fields) == {"status", "detail"}

        annotations = {
            name: field.annotation for name, field in NotConnectedPanel.model_fields.items()
        }
        assert annotations["status"] is CapabilityStatus
        assert annotations["detail"] is str

    def test_detail_is_bounded_and_required_to_be_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            NotConnectedPanel(detail="")
        with pytest.raises(ValidationError):
            NotConnectedPanel(detail="x" * 201)

    def test_is_frozen_and_closed(self) -> None:
        panel = NotConnectedPanel(detail="nothing here")
        with pytest.raises(ValidationError):
            panel.detail = "something"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            NotConnectedPanel(detail="x", headlines=[])  # type: ignore[call-arg]


class TestRiskPanel:
    def test_available_requires_a_verdict(self) -> None:
        with pytest.raises(ValidationError):
            RiskPanel(availability=RiskAvailability.AVAILABLE)

    def test_unavailable_forbids_a_verdict(self, approved_verdict) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValidationError):
            RiskPanel(
                availability=RiskAvailability.NO_PORTFOLIO_STORE,
                verdict=approved_verdict,
                portfolio_as_of=NOW,
            )

    def test_every_unavailable_reason_constructs_without_a_verdict(self) -> None:
        for availability in RiskAvailability:
            if availability is RiskAvailability.AVAILABLE:
                continue
            panel = RiskPanel(availability=availability)
            assert panel.verdict is None

    def test_portfolio_as_of_must_be_timezone_aware(self) -> None:
        with pytest.raises(ValidationError):
            RiskPanel(
                availability=RiskAvailability.PORTFOLIO_UNAVAILABLE,
                portfolio_as_of=datetime(2026, 8, 28, 12, 0),
            )


class TestAnalysisReport:
    def test_rejects_a_naive_analyzed_at(self, report) -> None:  # type: ignore[no-untyped-def]
        payload = report.model_dump()
        payload["analyzed_at"] = datetime(2026, 8, 28, 12, 5)
        with pytest.raises(ValidationError):
            AnalysisReport.model_validate(payload)

    def test_rejects_a_symbol_that_disagrees_with_its_evidence(self, report) -> None:  # type: ignore[no-untyped-def]
        payload = report.model_dump()
        payload["symbol"] = "EURUSD"
        with pytest.raises(ValidationError):
            AnalysisReport.model_validate(payload)

    def test_rejects_a_timeframe_that_disagrees_with_its_evidence(self, report) -> None:  # type: ignore[no-untyped-def]
        payload = report.model_dump()
        payload["timeframe"] = Timeframe.D1.value
        with pytest.raises(ValidationError):
            AnalysisReport.model_validate(payload)

    def test_rejects_a_decision_from_a_different_analysis(self, report) -> None:  # type: ignore[no-untyped-def]
        payload = report.model_dump()
        payload["analysis_id"] = "analysis-somewhere-else"
        with pytest.raises(ValidationError):
            AnalysisReport.model_validate(payload)

    def test_rejects_a_verdict_from_a_different_analysis(self, report) -> None:  # type: ignore[no-untyped-def]
        if report.risk.verdict is None:
            pytest.skip("this fixture has no verdict to mismatch")
        payload = report.model_dump()
        payload["risk"]["verdict"]["analysis_id"] = "analysis-somewhere-else"
        with pytest.raises(ValidationError):
            AnalysisReport.model_validate(payload)

    def test_rejects_a_thesis_from_a_different_analysis(self, report) -> None:  # type: ignore[no-untyped-def]
        """THE THIRD MEMBER OF THE CHAIN, CHECKED LIKE THE OTHER TWO.

        A `MarketThesis` points at nothing, so `analysis_id` is the only thing
        tying it to the analysis it was formed for. `orion.cli` reads it off
        this report and hands it to the journal, and `orion.reflection` finds
        it again by that key alone -- so a mismatch that left this validator
        would journal advisory evidence that is either invisible to reflection
        or findable from a trade it was never formed beside.
        """
        assert report.thesis.thesis is not None
        payload = report.model_dump()
        payload["thesis"]["thesis"]["analysis_id"] = "analysis-somewhere-else"
        with pytest.raises(ValidationError, match="thesis analysis_id"):
            AnalysisReport.model_validate(payload)

    def test_accepts_a_report_that_formed_no_thesis(self, report) -> None:  # type: ignore[no-untyped-def]
        """The absent thesis is a real state, not a mismatch: a report whose
        specialists produced nothing carries `thesis=None` and validates."""
        payload = report.model_dump()
        payload["thesis"] = {"detail": "no thesis", "thesis": None}

        assert AnalysisReport.model_validate(payload).thesis.thesis is None

    def test_is_frozen_and_closed(self, report) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(ValidationError):
            report.symbol = "EURUSD"
        payload = report.model_dump()
        payload["injected"] = True
        with pytest.raises(ValidationError):
            AnalysisReport.model_validate(payload)

    def test_round_trips_through_json_unchanged(self, report) -> None:  # type: ignore[no-untyped-def]
        # It will cross an HTTP boundary in Phase 5.2; proving it survives
        # its own serialisation now costs nothing and catches a field that
        # cannot be represented before anything depends on it.
        restored = AnalysisReport.model_validate_json(report.model_dump_json())
        assert restored == report


@pytest.fixture
def report(permissive_policy, market_data, reasoning_provider, portfolio_store):  # type: ignore[no-untyped-def]
    from orion.orchestrator.analysis import run_analysis
    from orion.orchestrator.intent import parse_intent

    return run_analysis(
        intent=parse_intent("analyze gold"),
        market_data=market_data,
        reasoning_provider=reasoning_provider,
        policy=permissive_policy,
        portfolio_store=portfolio_store,
        now=NOW + timedelta(0),
    )


@pytest.fixture
def approved_verdict(report):  # type: ignore[no-untyped-def]
    assert report.risk.verdict is not None
    return report.risk.verdict
