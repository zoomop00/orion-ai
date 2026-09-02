"""The Technical Analyst's shapes: closed, frozen, and unable to instruct.

MIRRORS `test_schemas.py` DELIBERATELY. The Macro Analyst's schema test checks
its field names against a blocklist of trade-instruction vocabulary rather than
trusting a reviewer to catch a field that drifts toward one. The Technical
Analyst carries a *directional* reading, so it needs the same check more, not
less: `StructureBias.BULLISH` describes price geometry that has already
printed, and the thing that keeps it from becoming an order is that no field
here can express one.

The other invariant tested here is the one that makes ORION's prose auditable:
a `TechnicalFinding`'s `interpretation` must be the exact sentence the
knowledge registry holds for its concept and bias. It is enforced by a model
validator rather than only by the analyst, so even a hand-built finding with
invented commentary is rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from orion.analysis.structure import VolatilityLevel
from orion.analysts import technical_schemas
from orion.analysts.knowledge import (
    KNOWLEDGE_VERSION,
    TECHNICAL_KNOWLEDGE,
    StructureBias,
    TechnicalConcept,
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

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64

#: Vocabulary that would make a field an instruction rather than an
#: observation. The same list Mission 8 v1 applied to the Macro Analyst, plus
#: the execution-layer words a technical schema could plausibly grow.
_TRADE_INSTRUCTION_WORDS = (
    "action",
    "buy",
    "sell",
    "entry",
    "entry_price",
    "exit",
    "stop",
    "stop_loss",
    "take_profit",
    "target",
    "quantity",
    "size",
    "position_size",
    "lot",
    "leverage",
    "risk_per_trade",
    "order",
    "execute",
    "approved",
    "approval",
    "authorize",
    "signal",
    "recommendation",
    "recommend",
)


def _models() -> list[type[BaseModel]]:
    return [
        attribute
        for attribute in vars(technical_schemas).values()
        if isinstance(attribute, type)
        and issubclass(attribute, BaseModel)
        and attribute is not BaseModel
        and attribute.__module__ == technical_schemas.__name__
    ]


def finding(
    concept: TechnicalConcept = TechnicalConcept.TREND_REGIME,
    bias: StructureBias = StructureBias.BULLISH,
    **overrides: object,
) -> TechnicalFinding:
    payload: dict[str, object] = {
        "concept": concept,
        "timeframe": Timeframe.H1,
        "bias": bias,
        "observation": "last close 2500.0000 against EMA20 2490.0000",
        # `.get`, not `[...]`: a test that deliberately builds a
        # concept/bias pair the registry has no words for still needs to
        # reach the model, so the rejection comes from the validator under
        # test rather than from this helper.
        "interpretation": TECHNICAL_KNOWLEDGE[concept].interpretations.get(bias, "placeholder"),
        "values": (TechnicalValue(name="last_close", value=2500.0, unit="price"),),
        "deterministic": True,
        "evidence_fingerprint": FINGERPRINT,
        "source_provider": "fake",
        "retrieved_at": NOW,
        "as_of": NOW,
        "bar_count": 120,
        "observed_at": NOW,
        "knowledge_version": KNOWLEDGE_VERSION,
    }
    payload.update(overrides)
    return TechnicalFinding(**payload)  # type: ignore[arg-type]


def _all_concepts_report(findings: tuple[TechnicalFinding, ...]) -> TechnicalTimeframeReport:
    covered = {item.concept for item in findings}
    return TechnicalTimeframeReport(
        timeframe=Timeframe.H1,
        symbol="XAUUSD",
        bar_count=120,
        evidence_fingerprint=FINGERPRINT,
        source_provider="fake",
        retrieved_at=NOW,
        as_of=NOW,
        findings=findings,
        unevaluated=tuple(
            UnevaluatedConcept(concept=concept, reason=UnevaluatedReason.INSUFFICIENT_BARS)
            for concept in TechnicalConcept
            if concept not in covered
        ),
        bias=StructureBias.BULLISH,
        volatility=VolatilityLevel.MEDIUM,
    )


# --------------------------------------------- nothing can encode an order


@pytest.mark.parametrize("model", _models(), ids=lambda model: model.__name__)
def test_no_field_can_encode_a_trade_instruction(model: type[BaseModel]) -> None:
    """THE STRUCTURAL GUARANTEE, CHECKED RATHER THAN REVIEWED.

    A future field named `entry_price` or `recommended_action` would make this
    package capable of expressing an order, which is the one thing an advisory
    specialist must never be able to do. Checked against the model's actual
    field names so it fails at the point the field is added.
    """
    offenders = [
        name
        for name in model.model_fields
        if any(word in name.lower() for word in _TRADE_INSTRUCTION_WORDS)
    ]
    assert not offenders, f"{model.__name__} has trade-instruction fields: {offenders}"


@pytest.mark.parametrize("model", _models(), ids=lambda model: model.__name__)
def test_every_model_is_frozen_and_closed(model: type[BaseModel]) -> None:
    assert model.model_config.get("frozen") is True
    assert model.model_config.get("extra") == "forbid"


def test_an_unexpected_field_is_a_construction_bug() -> None:
    with pytest.raises(ValidationError):
        finding(recommended_action="buy")


# ------------------------------------- interpretations come from the registry


class TestInterpretationsAreLookedUpNeverWritten:
    def test_a_registry_sentence_is_accepted(self) -> None:
        item = finding()

        assert (
            item.interpretation
            == TECHNICAL_KNOWLEDGE[TechnicalConcept.TREND_REGIME].interpretations[
                StructureBias.BULLISH
            ]
        )

    def test_invented_commentary_is_rejected(self) -> None:
        """Even hand-assembled. This is what makes "ORION never writes market
        commentary" a property of the type rather than of one function."""
        with pytest.raises(ValidationError, match="looked up, never written"):
            finding(interpretation="Gold is going to the moon, load up.")

    def test_the_right_sentence_under_the_wrong_bias_is_rejected(self) -> None:
        """A subtler failure than invented prose, and a likelier one: real
        registry text attached to a reading it does not describe."""
        bearish = TECHNICAL_KNOWLEDGE[TechnicalConcept.TREND_REGIME].interpretations[
            StructureBias.BEARISH
        ]

        with pytest.raises(ValidationError, match="looked up, never written"):
            finding(bias=StructureBias.BULLISH, interpretation=bearish)

    def test_a_bias_the_registry_has_no_words_for_is_rejected(self) -> None:
        """Volatility has no bullish reading, so a finding claiming one cannot
        be constructed at all."""
        with pytest.raises(ValidationError, match="no interpretation"):
            finding(
                concept=TechnicalConcept.VOLATILITY_ATR,
                bias=StructureBias.BULLISH,
                interpretation="anything",
            )


# ------------------------------------------------------ report invariants


class TestEveryConceptIsAccountedFor:
    def test_a_report_missing_a_concept_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="evaluated or unevaluated"):
            TechnicalTimeframeReport(
                timeframe=Timeframe.H1,
                symbol="XAUUSD",
                bar_count=120,
                evidence_fingerprint=FINGERPRINT,
                source_provider="fake",
                retrieved_at=NOW,
                as_of=NOW,
                findings=(finding(),),
                unevaluated=(),
                bias=StructureBias.BULLISH,
                volatility=VolatilityLevel.LOW,
            )

    def test_a_concept_in_both_lists_is_rejected(self) -> None:
        report_findings = (finding(),)
        with pytest.raises(ValidationError, match="more than once"):
            TechnicalTimeframeReport(
                timeframe=Timeframe.H1,
                symbol="XAUUSD",
                bar_count=120,
                evidence_fingerprint=FINGERPRINT,
                source_provider="fake",
                retrieved_at=NOW,
                as_of=NOW,
                findings=report_findings,
                unevaluated=tuple(
                    UnevaluatedConcept(concept=concept, reason=UnevaluatedReason.INSUFFICIENT_BARS)
                    for concept in TechnicalConcept
                ),
                bias=StructureBias.BULLISH,
                volatility=VolatilityLevel.LOW,
            )

    def test_a_finding_from_another_timeframe_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not this report"):
            _all_concepts_report((finding(timeframe=Timeframe.H4),))

    def test_a_finding_from_another_evidence_is_rejected(self) -> None:
        """Provenance cannot be mixed: a report's findings must all describe
        the bars the report itself names."""
        with pytest.raises(ValidationError, match="different Evidence"):
            _all_concepts_report((finding(evidence_fingerprint=OTHER_FINGERPRINT),))

    def test_a_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            finding(observed_at=datetime(2026, 8, 30, 12, 0))


class TestAvailabilityMatchesContent:
    def _output(self, **overrides: object) -> TechnicalAnalystOutput:
        payload: dict[str, object] = {
            "availability": TechnicalAvailability.AVAILABLE,
            "symbol": "XAUUSD",
            "analyzed_at": NOW,
            "analyst_version": "test",
            "knowledge_version": KNOWLEDGE_VERSION,
            "timeframes": (_all_concepts_report((finding(),)),),
            "bias": StructureBias.BULLISH,
            "timeframe_agreement": True,
        }
        payload.update(overrides)
        return TechnicalAnalystOutput(**payload)  # type: ignore[arg-type]

    def test_available_with_findings_is_valid(self) -> None:
        assert self._output().availability is TechnicalAvailability.AVAILABLE

    def test_available_without_findings_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="no timeframe produced a finding"):
            self._output(timeframes=(_all_concepts_report(()),))

    def test_findings_without_availability_are_rejected(self) -> None:
        """The `RiskPanel`/`MacroPanel` invariant, applied here: a reading may
        only accompany the state that says there is one."""
        with pytest.raises(ValidationError, match="may only accompany AVAILABLE"):
            self._output(availability=TechnicalAvailability.INSUFFICIENT_DATA)

    def test_no_input_with_timeframes_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="NO_INPUT"):
            self._output(
                availability=TechnicalAvailability.NO_INPUT,
                timeframes=(_all_concepts_report(()),),
            )

    def test_a_timeframe_for_another_symbol_is_rejected(self) -> None:
        other = _all_concepts_report((finding(),)).model_copy(update={"symbol": "EURUSD"})
        with pytest.raises(ValidationError, match="one pass covers one symbol"):
            self._output(timeframes=(other,))

    def test_a_repeated_timeframe_is_rejected(self) -> None:
        report = _all_concepts_report((finding(),))
        with pytest.raises(ValidationError, match="reported twice"):
            self._output(timeframes=(report, report))

    def test_a_naive_analyzed_at_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            self._output(analyzed_at=datetime(2026, 8, 30, 12, 0))
