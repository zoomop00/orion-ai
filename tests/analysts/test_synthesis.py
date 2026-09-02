"""The synthesis seam: two specialists, one thesis, neither side discarded.

THE FOUR PROPERTIES THESE TESTS EXIST FOR.

1. Agreement and conflict are both real, computed outcomes — not an averaging
   step that quietly resolves a disagreement into a middle reading.
2. When they conflict, *both* readings survive onto the thesis. A synthesis
   that dropped the inconvenient half would be worse than no synthesis.
3. Provenance survives: every fingerprint, model, prompt version and retrieval
   time needed to go back and re-fetch either specialist's input is on the
   thesis.
4. A thesis cannot approve anything. There is no field capable of expressing an
   action, a size or a permission, and nothing in the decision or execution
   chain can even see this package.

THE COMPARISON AXIS IS SIGNIFICANCE, NOT DIRECTION, and that is tested too:
`MacroPolicyPressure` is deliberately not asset-direction vocabulary, so a
thesis that turned "tightening pressure" into a directional call would be
asserting an economic relationship this build has never established.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from orion.analysis.structure import VolatilityLevel
from orion.analysts import synthesis as synthesis_module
from orion.analysts.knowledge import KNOWLEDGE_VERSION, StructureBias, TechnicalConcept
from orion.analysts.schemas import (
    MacroAnalystOutput,
    MacroEventAssessment,
    MacroPolicyPressure,
    MarketRelevance,
    SurpriseDirection,
)
from orion.analysts.synthesis import (
    THESIS_VERSION,
    ConditionLevel,
    MarketThesis,
    SynthesisError,
    ThesisAlignment,
    ThesisTopic,
    synthesize,
)
from orion.analysts.technical_schemas import (
    TechnicalAnalystOutput,
    TechnicalAvailability,
    TechnicalFinding,
    TechnicalTimeframeReport,
    TechnicalValue,
    UnevaluatedConcept,
    UnevaluatedReason,
)
from orion.data.models import Timeframe
from orion.thesis import schemas as thesis_schemas

from .test_technical_schemas import _TRADE_INSTRUCTION_WORDS, finding

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
#: The id the orchestrator mints per analysis and stamps on the decision, the
#: verdict and the thesis alike -- a sibling key, never a back-reference.
ANALYSIS_ID = "analysis-XAUUSD-1h-2026-08-30T11:00:00+00:00"
FINGERPRINT = "c" * 64
MACRO_FINGERPRINT = "d" * 64
EVENT_ID = "e" * 64


# ------------------------------------------------------------- builders


def timeframe_report(
    *,
    timeframe: Timeframe = Timeframe.H1,
    bias: StructureBias = StructureBias.BULLISH,
    volatility: VolatilityLevel = VolatilityLevel.LOW,
    findings: tuple[TechnicalFinding, ...] | None = None,
    fingerprint: str = FINGERPRINT,
) -> TechnicalTimeframeReport:
    items = (
        findings
        if findings is not None
        else (finding(timeframe=timeframe, evidence_fingerprint=fingerprint),)
    )
    covered = {item.concept for item in items}
    return TechnicalTimeframeReport(
        timeframe=timeframe,
        symbol="XAUUSD",
        bar_count=120,
        evidence_fingerprint=fingerprint,
        source_provider="twelvedata",
        retrieved_at=NOW,
        as_of=NOW - timedelta(hours=1),
        findings=items,
        unevaluated=tuple(
            UnevaluatedConcept(concept=concept, reason=UnevaluatedReason.INSUFFICIENT_BARS)
            for concept in TechnicalConcept
            if concept not in covered
        ),
        bias=bias,
        volatility=volatility,
    )


def technical(
    *,
    reports: tuple[TechnicalTimeframeReport, ...] | None = None,
    availability: TechnicalAvailability = TechnicalAvailability.AVAILABLE,
    bias: StructureBias = StructureBias.BULLISH,
    agreement: bool = True,
) -> TechnicalAnalystOutput:
    return TechnicalAnalystOutput(
        availability=availability,
        symbol="XAUUSD",
        analyzed_at=NOW,
        analyst_version="test-analyst",
        knowledge_version=KNOWLEDGE_VERSION,
        timeframes=reports if reports is not None else (timeframe_report(),),
        bias=bias,
        timeframe_agreement=agreement,
    )


def event_finding(
    concept: TechnicalConcept,
    bias: StructureBias,
    *,
    timeframe: Timeframe = Timeframe.H1,
) -> TechnicalFinding:
    """A structure-event finding with a real direction, i.e. something
    happened. The analyst reports a count of zero as NEUTRAL."""
    return finding(
        concept=concept,
        bias=bias,
        timeframe=timeframe,
        observation="1 in the last 120 bars",
        values=(TechnicalValue(name="event_count", value=1.0, unit="count"),),
        evidence_fingerprint=FINGERPRINT,
    )


def macro(
    *,
    relevance: MarketRelevance = MarketRelevance.HIGH,
    pressure: MacroPolicyPressure = MacroPolicyPressure.TIGHTENING_PRESSURE,
    confidence: float = 0.72,
) -> MacroAnalystOutput:
    return MacroAnalystOutput(
        policy_pressure=pressure,
        market_relevance=relevance,
        confidence=confidence,
        key_event_assessments=(
            MacroEventAssessment(
                event_id=EVENT_ID,
                surprise=SurpriseDirection.ABOVE_ESTIMATE,
                note="CPI came in above estimate",
            ),
        ),
        rationale="Inflation printed above estimate across the batch.",
        model="claude-opus-5",
        prompt_version="macro-v1",
        input_fingerprint=MACRO_FINGERPRINT,
        source_provider="finnhub",
        query_start=NOW - timedelta(days=2),
        query_end=NOW + timedelta(days=5),
        retrieved_at=NOW,
        analyzed_at=NOW,
    )


def _models() -> list[type[BaseModel]]:
    """Every model the thesis is made of.

    POINTED AT `orion.thesis.schemas`, NOT AT `synthesis`, AND THE NON-EMPTY
    ASSERTION IS THE POINT. Mission 8 v2.2 moved these shapes out of
    `orion.analysts.synthesis` so the storage and reflection layers could name
    them without importing an analyst. The old filter matched on
    `__module__ == synthesis`, so after the move it returned nothing and the
    parametrized blocklist test below silently became a skip -- a
    trade-instruction field could then have been added to `MarketThesis` with
    no test failing. The guard means this can never quietly stop testing
    anything again.
    """
    models = [
        attribute
        for attribute in vars(thesis_schemas).values()
        if isinstance(attribute, type)
        and issubclass(attribute, BaseModel)
        and attribute is not BaseModel
        and attribute.__module__ == thesis_schemas.__name__
    ]
    assert models, "the thesis schema module exposes no models to check"
    return models


# -------------------------------------------------------------- agreement


class TestTheSpecialistsCanAgree:
    def test_an_active_price_picture_and_a_heavy_calendar_corroborate(self) -> None:
        """Both specialists say conditions are eventful, on their own terms:
        a structural break on one side, HIGH relevance on the other."""
        thesis = synthesize(
            technical=technical(
                reports=(
                    timeframe_report(
                        findings=(
                            event_finding(
                                TechnicalConcept.BREAK_OF_STRUCTURE, StructureBias.BULLISH
                            ),
                        )
                    ),
                )
            ),
            macro=macro(relevance=MarketRelevance.HIGH),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.alignment is ThesisAlignment.CORROBORATED_ACTIVE
        assert thesis.technical_condition is ConditionLevel.ACTIVE
        assert thesis.macro_condition is ConditionLevel.ACTIVE
        assert [point.topic for point in thesis.agreements] == [ThesisTopic.MARKET_CONDITIONS]
        assert thesis.conflicts == ()

    def test_a_quiet_price_picture_and_an_empty_calendar_corroborate(self) -> None:
        thesis = synthesize(
            technical=technical(reports=(timeframe_report(volatility=VolatilityLevel.LOW),)),
            macro=macro(relevance=MarketRelevance.LOW),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.alignment is ThesisAlignment.CORROBORATED_QUIET
        assert thesis.technical_condition is ConditionLevel.QUIET
        assert thesis.macro_condition is ConditionLevel.QUIET
        assert len(thesis.agreements) == 1

    def test_high_volatility_alone_makes_the_price_side_active(self) -> None:
        thesis = synthesize(
            technical=technical(reports=(timeframe_report(volatility=VolatilityLevel.HIGH),)),
            macro=macro(relevance=MarketRelevance.HIGH),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.technical_condition is ConditionLevel.ACTIVE
        assert thesis.alignment is ThesisAlignment.CORROBORATED_ACTIVE

    def test_agreeing_timeframes_are_recorded_as_their_own_agreement(self) -> None:
        thesis = synthesize(
            technical=technical(
                reports=(
                    timeframe_report(timeframe=Timeframe.H1, bias=StructureBias.BULLISH),
                    timeframe_report(timeframe=Timeframe.H4, bias=StructureBias.BULLISH),
                ),
                agreement=True,
            ),
            macro=None,
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        topics = [point.topic for point in thesis.agreements]
        assert ThesisTopic.TIMEFRAME_STRUCTURE in topics


# --------------------------------------------------------------- conflict


class TestTheSpecialistsCanConflict:
    def test_a_quiet_chart_against_a_heavy_calendar_is_a_conflict(self) -> None:
        """THE CASE THIS COMPARISON EXISTS FOR. The calendar says something big
        is landing and price has not moved yet — exactly the disagreement a
        human needs to see rather than have averaged away."""
        thesis = synthesize(
            technical=technical(reports=(timeframe_report(volatility=VolatilityLevel.LOW),)),
            macro=macro(relevance=MarketRelevance.HIGH),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.alignment is ThesisAlignment.CONFLICTED
        assert thesis.agreements == ()
        assert [point.topic for point in thesis.conflicts] == [ThesisTopic.MARKET_CONDITIONS]

    def test_a_breaking_chart_against_an_empty_calendar_is_a_conflict(self) -> None:
        """The mirror: price is doing something the calendar cannot explain."""
        thesis = synthesize(
            technical=technical(
                reports=(
                    timeframe_report(
                        findings=(
                            event_finding(
                                TechnicalConcept.CHANGE_OF_CHARACTER, StructureBias.BEARISH
                            ),
                        )
                    ),
                )
            ),
            macro=macro(relevance=MarketRelevance.NONE),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.alignment is ThesisAlignment.CONFLICTED
        assert thesis.technical_condition is ConditionLevel.ACTIVE
        assert thesis.macro_condition is ConditionLevel.QUIET

    def test_neither_reading_is_discarded_by_a_conflict(self) -> None:
        """BOTH SIDES SURVIVE, and both are recoverable from the thesis alone.

        The conflict point names each specialist's own reading, and the thesis
        still carries the macro pressure, relevance, confidence and the Macro
        Analyst's own rationale verbatim.
        """
        opinion = macro(relevance=MarketRelevance.HIGH)

        thesis = synthesize(
            technical=technical(
                reports=(timeframe_report(volatility=VolatilityLevel.LOW),),
                bias=StructureBias.BULLISH,
            ),
            macro=opinion,
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        point = thesis.conflicts[0]
        assert point.technical_reading == ConditionLevel.QUIET.value
        assert point.macro_reading == ConditionLevel.ACTIVE.value
        assert point.agreed is False

        assert thesis.technical_bias is StructureBias.BULLISH
        assert thesis.macro_policy_pressure is MacroPolicyPressure.TIGHTENING_PRESSURE
        assert thesis.macro_relevance is MarketRelevance.HIGH
        assert thesis.macro_confidence == pytest.approx(0.72)
        assert thesis.macro_provenance is not None
        assert thesis.macro_provenance.rationale == opinion.rationale

    def test_disagreeing_timeframes_are_recorded_as_a_conflict(self) -> None:
        thesis = synthesize(
            technical=technical(
                reports=(
                    timeframe_report(timeframe=Timeframe.H1, bias=StructureBias.BULLISH),
                    timeframe_report(timeframe=Timeframe.H4, bias=StructureBias.BEARISH),
                ),
                bias=StructureBias.NEUTRAL,
                agreement=False,
            ),
            macro=None,
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert [point.topic for point in thesis.conflicts] == [ThesisTopic.TIMEFRAME_STRUCTURE]
        assert thesis.technical_timeframe_agreement is False
        # And both timeframes are still on the thesis, with their own readings.
        assert {report.bias for report in thesis.timeframes} == {
            StructureBias.BULLISH,
            StructureBias.BEARISH,
        }


# ------------------------------------------------------- honest abstention


class TestItDeclinesToCompareWhenItCannot:
    def test_absent_macro_is_a_state_not_an_assumption(self) -> None:
        thesis = synthesize(technical=technical(), macro=None, analysis_id=ANALYSIS_ID, now=NOW)

        assert thesis.alignment is ThesisAlignment.MACRO_ABSENT
        assert thesis.macro_policy_pressure is None
        assert thesis.macro_relevance is None
        assert thesis.macro_provenance is None
        assert thesis.conflicts == ()
        # The technical half is still reported in full.
        assert thesis.technical_bias is StructureBias.BULLISH
        assert thesis.timeframes

    def test_a_medium_calendar_reading_is_undetermined_not_split_in_half(self) -> None:
        """MEDIUM is the analyst declining to commit; reading it as either
        extreme would put words in its mouth."""
        thesis = synthesize(
            technical=technical(reports=(timeframe_report(volatility=VolatilityLevel.HIGH),)),
            macro=macro(relevance=MarketRelevance.MEDIUM),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.macro_condition is ConditionLevel.UNDETERMINED
        assert thesis.alignment is ThesisAlignment.INSUFFICIENT_EVIDENCE
        assert thesis.agreements == ()
        assert thesis.conflicts == ()

    def test_a_medium_volatility_chart_with_no_events_is_undetermined(self) -> None:
        thesis = synthesize(
            technical=technical(reports=(timeframe_report(volatility=VolatilityLevel.MEDIUM),)),
            macro=macro(relevance=MarketRelevance.HIGH),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.technical_condition is ConditionLevel.UNDETERMINED
        assert thesis.alignment is ThesisAlignment.INSUFFICIENT_EVIDENCE

    def test_unavailable_technical_evidence_is_undetermined(self) -> None:
        thesis = synthesize(
            technical=technical(
                availability=TechnicalAvailability.NO_INPUT,
                reports=(),
                bias=StructureBias.UNCLEAR,
                agreement=False,
            ),
            macro=macro(),
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert thesis.technical_condition is ConditionLevel.UNDETERMINED
        assert thesis.alignment is ThesisAlignment.INSUFFICIENT_EVIDENCE

    def test_a_single_timeframe_produces_no_agreement_claim_about_timeframes(self) -> None:
        """One timeframe cannot agree with itself, and reporting that it did
        would be an unearned corroboration."""
        thesis = synthesize(technical=technical(), macro=None, analysis_id=ANALYSIS_ID, now=NOW)

        topics = [point.topic for point in thesis.agreements + thesis.conflicts]
        assert ThesisTopic.TIMEFRAME_STRUCTURE not in topics


# ------------------------------------------------------------ provenance


class TestProvenanceSurvivesSynthesis:
    def test_every_technical_fingerprint_reaches_the_thesis(self) -> None:
        thesis = synthesize(
            technical=technical(
                reports=(
                    timeframe_report(timeframe=Timeframe.H1, fingerprint="1" * 64),
                    timeframe_report(timeframe=Timeframe.H4, fingerprint="2" * 64),
                )
            ),
            macro=None,
            analysis_id=ANALYSIS_ID,
            now=NOW,
        )

        assert [report.evidence_fingerprint for report in thesis.timeframes] == [
            "1" * 64,
            "2" * 64,
        ]
        assert all(report.source_provider == "twelvedata" for report in thesis.timeframes)
        assert all(report.bar_count == 120 for report in thesis.timeframes)

    def test_every_macro_provenance_field_is_copied_verbatim(self) -> None:
        opinion = macro()

        thesis = synthesize(technical=technical(), macro=opinion, analysis_id=ANALYSIS_ID, now=NOW)

        provenance = thesis.macro_provenance
        assert provenance is not None
        assert provenance.model == opinion.model
        assert provenance.prompt_version == opinion.prompt_version
        assert provenance.input_fingerprint == opinion.input_fingerprint
        assert provenance.source_provider == opinion.source_provider
        assert provenance.query_start == opinion.query_start
        assert provenance.query_end == opinion.query_end
        assert provenance.retrieved_at == opinion.retrieved_at
        assert provenance.analyzed_at == opinion.analyzed_at
        assert provenance.event_count == len(opinion.key_event_assessments)

    def test_the_analyst_and_knowledge_versions_travel_with_the_thesis(self) -> None:
        thesis = synthesize(technical=technical(), macro=None, analysis_id=ANALYSIS_ID, now=NOW)

        assert thesis.technical_analyst_version == "test-analyst"
        assert thesis.technical_knowledge_version == KNOWLEDGE_VERSION
        assert thesis.thesis_version == THESIS_VERSION

    def test_formed_at_is_the_callers_now(self) -> None:
        moment = datetime(2027, 3, 3, 9, 0, tzinfo=UTC)

        assert (
            synthesize(
                technical=technical(), macro=None, analysis_id=ANALYSIS_ID, now=moment
            ).formed_at
            == moment
        )


# ------------------------------------------- it cannot approve anything


class TestItCannotApproveAnything:
    @pytest.mark.parametrize("model", _models(), ids=lambda model: model.__name__)
    def test_no_field_can_encode_an_order_or_a_permission(self, model: type[BaseModel]) -> None:
        """THE BOUNDARY, CHECKED RATHER THAN ASSERTED IN PROSE.

        The Risk Guardian is the only thing in ORION that approves a trade. A
        thesis cannot express one, cannot size one and cannot permit one,
        because no field here is capable of carrying any of those.
        """
        offenders = [
            name
            for name in model.model_fields
            if any(word in name.lower() for word in _TRADE_INSTRUCTION_WORDS)
        ]
        assert not offenders, f"{model.__name__} has trade-instruction fields: {offenders}"

    def test_a_thesis_never_turns_macro_pressure_into_a_direction(self) -> None:
        """`MacroPolicyPressure` is deliberately not asset-direction vocabulary.

        Turning "tightening pressure" into a directional call requires an
        asset-specific economic assumption this build has never established, so
        the technical bias must be identical whatever macro says.
        """
        biases = set()
        for pressure in MacroPolicyPressure:
            thesis = synthesize(
                technical=technical(bias=StructureBias.BULLISH),
                macro=macro(pressure=pressure),
                analysis_id=ANALYSIS_ID,
                now=NOW,
            )
            biases.add(thesis.technical_bias)
            assert thesis.macro_policy_pressure is pressure

        assert biases == {StructureBias.BULLISH}

    def test_the_summary_is_a_committed_sentence_not_composed_prose(self) -> None:
        """Invented summary text is rejected by the model itself, so a thesis
        cannot carry commentary no one wrote."""
        thesis = synthesize(technical=technical(), macro=None, analysis_id=ANALYSIS_ID, now=NOW)

        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="looked up, never written"):
            MarketThesis(**{**thesis.model_dump(), "summary": "Gold is going higher, buy it."})

    def test_the_macro_half_is_all_or_nothing(self) -> None:
        """A partial macro read must not be presentable as a complete one."""
        thesis = synthesize(technical=technical(), macro=macro(), analysis_id=ANALYSIS_ID, now=NOW)

        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="all-or-nothing"):
            MarketThesis(**{**thesis.model_dump(), "macro_provenance": None})

    def test_synthesis_reads_nothing_from_the_decision_or_execution_chain(self) -> None:
        """Static, and the counterpart to the import-hygiene suite: this module
        names no decision, risk, paper, memory or orchestrator module at all."""
        import ast
        from pathlib import Path

        source = Path(synthesis_module.__file__ or "")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}

        for banned in (
            "orion.decision",
            "orion.risk.guardian",
            "orion.risk.policy",
            "orion.paper",
            "orion.memory",
            "orion.orchestrator",
            "orion.interface",
        ):
            assert not any(module.startswith(banned) for module in modules), (
                f"orion.analysts.synthesis reaches {banned}"
            )


# ------------------------------------------------------------- refusals


def test_a_naive_now_is_refused() -> None:
    with pytest.raises(SynthesisError, match="timezone-aware"):
        synthesize(
            technical=technical(),
            macro=None,
            analysis_id=ANALYSIS_ID,
            now=datetime(2026, 8, 30, 12, 0),
        )


def test_synthesis_is_deterministic() -> None:
    first = synthesize(technical=technical(), macro=macro(), analysis_id=ANALYSIS_ID, now=NOW)
    second = synthesize(technical=technical(), macro=macro(), analysis_id=ANALYSIS_ID, now=NOW)

    assert first.model_dump_json() == second.model_dump_json()
