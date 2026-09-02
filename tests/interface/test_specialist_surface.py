"""The specialist panels, as a client actually receives them.

WHAT THIS FILE PROTECTS. Mission 8 v2 computed the Technical Analyst and the
`MarketThesis` and left both inside `AnalysisReport`, where nothing could read
them. v2.1 surfaces them, and surfacing is exactly the moment a boundary starts
leaking: a fingerprint, a vendor name, a supporting-value dump, or a field that
reads like an instruction. Every one of those is asserted absent here.

The other half is that the surfacing must be *honest*: a concept ORION could
not evaluate has to reach the client as an explicit unevaluated entry with its
reason, and no concept ORION cannot compute at all may appear as though it had
been detected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from orion.analysts.knowledge import (
    TECHNICAL_KNOWLEDGE,
    UNSUPPORTED_CONCEPTS,
    StructureBias,
    TechnicalConcept,
)
from orion.analysts.technical_schemas import TechnicalAvailability, UnevaluatedReason
from orion.interface import schemas as interface_schemas
from orion.interface.schemas import AnalysisSummary

from .test_analysis_turn import (
    ANALYZE,
    TEXT_URL,
    analysing_client,
    build_analysis_service,
)

pytestmark = pytest.mark.usefixtures("deepgram")


@pytest.fixture
def analysing(deepgram: respx.MockRouter) -> TestClient:
    """A client whose service can analyse, with Deepgram already mocked.

    Defined here rather than imported: pytest fixtures are not inherited
    across sibling modules by a plain import, and re-exporting one shadows the
    name at every use site."""
    del deepgram
    return analysing_client()


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty working directory, so `rglob` measures this turn and nothing
    else. Mirrors `tests/interface/test_security.py`'s fixture of the same
    name; redefined rather than imported because pytest fixtures are not
    inherited across sibling modules by a plain import."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def analysis_of(client: TestClient) -> dict[str, object]:
    payload = client.post(TEXT_URL, json={"text": ANALYZE}).json()
    assert payload["analysis"] is not None
    return payload["analysis"]  # type: ignore[no-any-return]


@pytest.fixture
def analysis(analysing: TestClient) -> dict[str, object]:
    return analysis_of(analysing)


# ------------------------------------------------- the panels really arrive


class TestTheSpecialistsCrossTheBoundary:
    def test_the_technical_panel_carries_real_findings(self, analysis: dict[str, object]) -> None:
        technical = analysis["technical"]
        assert isinstance(technical, dict)

        assert technical["status"] == TechnicalAvailability.AVAILABLE.value
        assert technical["bias"] in {item.value for item in StructureBias}
        assert technical["timeframes"]
        findings = [f for tf in technical["timeframes"] for f in tf["findings"]]
        assert findings, "the panel arrived with no findings at all"

    def test_every_finding_carries_its_computed_observation_and_looked_up_words(
        self, analysis: dict[str, object]
    ) -> None:
        """THE SPLIT SURVIVES SERIALISATION. Numbers live in `observation`,
        which is computed; the sentence lives in `interpretation`, which is the
        knowledge registry's own committed text."""
        technical = analysis["technical"]
        canonical = {
            sentence
            for definition in TECHNICAL_KNOWLEDGE.values()
            for sentence in definition.interpretations.values()
        }

        for timeframe in technical["timeframes"]:  # type: ignore[index]
            for finding in timeframe["findings"]:
                assert any(char.isdigit() for char in finding["observation"])
                assert not any(char.isdigit() for char in finding["interpretation"])
                assert finding["interpretation"] in canonical, (
                    "a client received commentary the knowledge registry did not author"
                )
                assert finding["deterministic"] is True

    def test_the_thesis_arrives_with_its_canonical_summary(
        self, analysis: dict[str, object]
    ) -> None:
        thesis = analysis["thesis"]
        assert isinstance(thesis, dict)

        assert thesis["status"] == "available"
        assert thesis["alignment"]
        assert thesis["summary"]
        assert thesis["technical_bias"] in {item.value for item in StructureBias}

    def test_a_conflict_reaches_the_client_with_both_readings(self) -> None:
        """Neither specialist is discarded on the way out. The macro path is
        exercised end to end so the point genuinely comes from two analysts."""
        from datetime import UTC, datetime

        from tests.orchestrator.test_macro import (
            FakeCalendarProvider,
            FakeMacroAnalyst,
            make_calendar,
        )

        service = build_analysis_service(
            calendar_provider=FakeCalendarProvider(make_calendar(retrieved_at=datetime.now(UTC))),
            macro_analyst=FakeMacroAnalyst(),
        )
        thesis = analysis_of(analysing_client(service))["thesis"]
        assert isinstance(thesis, dict)

        assert thesis["macro_condition"] is not None
        for point in list(thesis["agreements"]) + list(thesis["conflicts"]):
            assert point["technical_reading"]
            assert point["detail"]
            if point["macro_reading"] is not None:
                assert point["macro_reading"]


# ------------------------------------------------------- honest absences


class TestAbsenceSurvivesTheBoundary:
    def test_unevaluated_concepts_reach_the_client_with_their_reason(
        self, analysis: dict[str, object]
    ) -> None:
        """ "ORION could not look" must stay distinguishable from "ORION looked
        and found nothing" on the wire, not only inside the engine."""
        technical = analysis["technical"]
        unevaluated = [
            item
            for timeframe in technical["timeframes"]  # type: ignore[index]
            for item in timeframe["unevaluated"]
        ]

        assert unevaluated, "the shared series leaves several concepts unevaluated"
        reasons = {item.value for item in UnevaluatedReason}
        concepts = {item.value for item in TechnicalConcept}
        for item in unevaluated:
            assert item["reason"] in reasons
            assert item["concept"] in concepts

    def test_every_concept_is_accounted_for_on_every_timeframe(
        self, analysis: dict[str, object]
    ) -> None:
        """The engine's completeness invariant, re-checked after serialisation:
        a concept cannot vanish between the analyst and the client."""
        for timeframe in analysis["technical"]["timeframes"]:  # type: ignore[index]
            seen = [f["concept"] for f in timeframe["findings"]] + [
                u["concept"] for u in timeframe["unevaluated"]
            ]
            assert sorted(seen) == sorted(item.value for item in TechnicalConcept)

    def test_no_unsupported_concept_is_presented_as_detected(
        self, analysis: dict[str, object]
    ) -> None:
        """THE CLAIM ORION MUST NOT MAKE. VWAP, MACD, volume profile, order
        blocks and Elliott wave are declared unsupported with concrete reasons;
        none may appear anywhere in a payload, in any casing, as though it had
        been measured."""
        blob = json.dumps(analysis).lower()

        for name in ("vwap", "macd", "obv", "order block", "elliott", "volume profile"):
            assert name not in blob, f"{name!r} reached a client as if it were detected"
        assert UNSUPPORTED_CONCEPTS  # the declared list is not empty

    def test_the_concept_vocabulary_on_the_wire_is_the_closed_one(
        self, analysis: dict[str, object]
    ) -> None:
        wire_concepts = {
            finding["concept"]
            for timeframe in analysis["technical"]["timeframes"]  # type: ignore[index]
            for finding in timeframe["findings"]
        }

        assert wire_concepts <= {item.value for item in TechnicalConcept}


# --------------------------------------------------- what must not cross


class TestTheAllowlistStillHolds:
    def test_no_evidence_fingerprint_reaches_a_client(
        self, analysing: TestClient, analysis: dict[str, object]
    ) -> None:
        """`_to_analysis_summary` has always dropped the evidence fingerprint.
        Adding a specialist panel must not smuggle it back in through a
        finding's provenance."""
        import re

        blob = json.dumps(analysis)

        assert not re.search(r"[0-9a-f]{64}", blob), "a 64-character fingerprint crossed"

    def test_no_vendor_name_reaches_a_client(self, analysis: dict[str, object]) -> None:
        """The market-data provider is on every `TechnicalTimeframeReport` and
        stops at the interface, exactly as the model identifier does for
        macro."""
        blob = json.dumps(analysis).lower()

        for vendor in ("twelvedata", "finnhub", "anthropic", "deepgram", "claude"):
            assert vendor not in blob

    def test_no_supporting_value_dump_reaches_a_client(self, analysis: dict[str, object]) -> None:
        """Every number a finding rests on is already inside its formatted
        `observation`. Shipping the raw list as well would put one measurement
        on the wire twice, in two shapes that could drift apart."""
        for timeframe in analysis["technical"]["timeframes"]:  # type: ignore[index]
            for finding in timeframe["findings"]:
                assert "values" not in finding

    def test_no_specialist_field_can_encode_a_trade_instruction(self) -> None:
        """The blocklist the analyst schemas already carry, applied to the wire
        shapes. A `recommended_action` here would be an instruction reaching a
        browser under the name of an observation."""
        from pydantic import BaseModel

        banned = (
            "action",
            "buy",
            "sell",
            "entry",
            "stop",
            "take_profit",
            "target",
            "quantity",
            "size",
            "order",
            "execute",
            "approved",
            "approval",
            "authorize",
            "signal",
            "recommend",
        )
        specialists = (
            interface_schemas.TechnicalSummary,
            interface_schemas.TechnicalTimeframeSummary,
            interface_schemas.TechnicalFindingSummary,
            interface_schemas.UnevaluatedConceptSummary,
            interface_schemas.ThesisSummary,
            interface_schemas.ThesisPointSummary,
        )
        for model in specialists:
            assert issubclass(model, BaseModel)
            offenders = [
                name for name in model.model_fields if any(word in name.lower() for word in banned)
            ]
            assert not offenders, f"{model.__name__} has trade-instruction fields: {offenders}"

    def test_the_specialist_shapes_are_frozen_and_closed(self) -> None:
        for model in (
            interface_schemas.TechnicalSummary,
            interface_schemas.TechnicalTimeframeSummary,
            interface_schemas.TechnicalFindingSummary,
            interface_schemas.UnevaluatedConceptSummary,
            interface_schemas.ThesisSummary,
            interface_schemas.ThesisPointSummary,
        ):
            assert model.model_config.get("frozen") is True
            assert model.model_config.get("extra") == "forbid"


# -------------------------------------------- the guarantees that predate this


class TestTheOlderGuaranteesAreUnchanged:
    def test_a_turn_that_surfaces_specialists_still_writes_nothing_to_disk(
        self, analysing: TestClient, isolated_cwd: Path
    ) -> None:
        """THE INTERFACE SECURITY GUARANTEE, RE-ASSERTED FOR THIS PATH. The
        specialist panels are computed in the orchestrator and reduced here;
        nothing on the way constructs a store, a journal or an engine."""
        analysing.post(TEXT_URL, json={"text": ANALYZE})

        assert list(isolated_cwd.rglob("*")) == []

    def test_the_risk_panel_is_untouched_by_the_specialists(
        self, analysis: dict[str, object]
    ) -> None:
        """The Risk Guardian's answer reaches a client through `RiskSummary`
        and nowhere else. No specialist field carries an outcome, and the risk
        panel's own shape has not moved."""
        risk = analysis["risk"]
        assert isinstance(risk, dict)

        assert set(risk) == {
            "availability",
            "outcome",
            "reason_codes",
            "failed_controls",
            "portfolio_as_of",
        }
        technical = json.dumps(analysis["technical"]) + json.dumps(analysis["thesis"])
        for word in ("approved", "vetoed", "no_trade_proposed", "outcome"):
            assert word not in technical

    def test_the_macro_panel_is_unchanged(self, analysis: dict[str, object]) -> None:
        macro = analysis["macro"]
        assert isinstance(macro, dict)

        assert set(macro) == {
            "status",
            "detail",
            "policy_pressure",
            "market_relevance",
            "confidence",
            "rationale",
            "event_count",
        }

    def test_the_summary_still_refuses_an_unexpected_field(self) -> None:
        """`extra="forbid"` on the top-level shape has not been relaxed to make
        room for the new panels."""
        from pydantic import ValidationError

        assert AnalysisSummary.model_config.get("extra") == "forbid"
        with pytest.raises(ValidationError):
            AnalysisSummary.model_validate({"surprise": 1})


def test_a_second_turn_serialises_identically(
    analysing: TestClient, deepgram: respx.MockRouter
) -> None:
    """Deterministic all the way to the wire: the specialists are pure
    functions, so two turns over the same fixture produce the same panels."""
    first = analysis_of(analysing)
    second = analysis_of(analysing)

    assert json.dumps(first["technical"], sort_keys=True) == json.dumps(
        second["technical"], sort_keys=True
    )
    assert json.dumps(first["thesis"], sort_keys=True) == json.dumps(
        second["thesis"], sort_keys=True
    )
