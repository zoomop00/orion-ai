"""Strict shapes for what the Technical Analyst is allowed to say.

DELIBERATELY NOT SHAPED LIKE `orion.analysts.schemas`. The Macro Analyst splits
into a model-authored `MacroAnalystVerdict` and a provider-stamped
`MacroAnalystOutput` because a language model writes half of it. The Technical
Analyst has no model: every value below is computed by `orion.analysis` from
real OHLC bars, and every sentence is copied verbatim from
`orion.analysts.knowledge`. There is nothing to constrain a model to and
nothing to verify afterwards, so there is one model rather than two — the
honest shape for a deterministic specialist.

EVERY FINDING CARRIES ITS OWN PROVENANCE. `evidence_fingerprint` is the hash of
the exact `Evidence` the finding was computed from — the same fingerprint
`ProposedDecision.evidence_ref.content_hash` carries, so a finding and the
proposal made from the same bars are verifiably about the same bars.
`source_provider`/`retrieved_at`/`as_of`/`bar_count` come off the snapshot's own
`DataSourceMeta` and the evidence, never from anything this module composes.

NOTHING HERE CAN ENCODE A TRADE INSTRUCTION, AND IT IS CHECKED. There is no
action, no direction-to-trade, no quantity, no entry, no stop, no target, no
risk parameter and no approval field anywhere in this file.
`StructureBias` describes the shape of price that has already printed;
`tests/analysts/test_technical_schemas.py` checks every field name in every
model here against a blocklist of trade-instruction vocabulary, exactly as the
Macro Analyst's schema test already does, so a future field that drifts toward
one fails rather than being caught by review.

`observation` IS COMPUTED; `interpretation` IS LOOKED UP. That split is the
whole design. An observation is a fixed template filled with real numbers, so
it can be wrong only if the arithmetic is wrong. An interpretation is a
committed sentence from the knowledge registry, so it can be wrong only if
someone changes the registry — never because a run produced novel prose.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.analysis.structure import VolatilityLevel
from orion.analysts.knowledge import (
    TECHNICAL_KNOWLEDGE,
    StructureBias,
    TechnicalConcept,
)
from orion.data.models import Timeframe

# Re-exported, not redefined -- see `orion.analysts.knowledge`'s identical note.
# `TechnicalAvailability` is a field of `MarketThesis`.
from orion.thesis.schemas import TechnicalAvailability as TechnicalAvailability

_FINGERPRINT_FIELD = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

#: Bumped when the detectors or the finding set change, so a stored finding can
#: be traced to the code that produced it. Paired with
#: `orion.analysts.knowledge.KNOWLEDGE_VERSION`, which versions the words.
TECHNICAL_ANALYST_VERSION = "2026-08-30.1"


class TechnicalValue(BaseModel):
    """One named number a finding rests on.

    Findings are auditable because of this list: every claim in an observation
    is backed by the values that produced it, so a reader can recompute the
    sentence rather than trust it. `name` is a fixed identifier chosen in
    `orion.analysts.technical`, never derived from data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=48)
    value: float
    unit: str | None = Field(
        default=None,
        max_length=16,
        description="'price', 'percent', 'ratio', 'count' or 'index'. None when the number "
        "is dimensionless and naming a unit would invent precision.",
    )


class UnevaluatedReason(StrEnum):
    """Why a concept produced no finding on a timeframe.

    A closed vocabulary, because "ORION did not evaluate this" must be
    distinguishable from "ORION evaluated this and found nothing" — the latter
    is a finding with a NEUTRAL bias, and collapsing the two would let missing
    data read as a measured absence.
    """

    #: The window holds fewer bars than the rule needs.
    INSUFFICIENT_BARS = "insufficient_bars"
    #: The indicator this concept reads was not computable from this window.
    INDICATOR_UNAVAILABLE = "indicator_unavailable"
    #: No confirmed swing points exist for the rule to measure against.
    NO_CONFIRMED_SWINGS = "no_confirmed_swings"
    #: No valid, distinct, ordered pair of opposing swings exists to anchor to.
    NO_VALID_ANCHOR = "no_valid_anchor"


class UnevaluatedConcept(BaseModel):
    """A concept ORION could not evaluate on one timeframe, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: TechnicalConcept
    reason: UnevaluatedReason


class TechnicalFinding(BaseModel):
    """One concept, evaluated on one timeframe, with the numbers behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: TechnicalConcept
    timeframe: Timeframe
    bias: StructureBias
    observation: str = Field(
        min_length=1,
        max_length=240,
        description="What was measured, formatted from `values` by a fixed template in "
        "orion.analysts.technical. Computed, never authored.",
    )
    interpretation: str = Field(
        min_length=1,
        max_length=400,
        description="The canonical sentence for this concept and bias, copied verbatim from "
        "orion.analysts.knowledge. Looked up, never composed.",
    )
    values: tuple[TechnicalValue, ...] = Field(
        description="The named numbers this finding rests on. Empty only where the concept "
        "genuinely has none to report, which is itself checked below."
    )
    deterministic: bool = Field(
        description="True for every finding this build produces: all of them come from pure "
        "computation over real bars. The field exists so a future finding that is not — one "
        "derived by a model, say — cannot enter the same list without saying so."
    )

    # --- provenance, all copied from the snapshot and the evidence ---
    evidence_fingerprint: str = _FINGERPRINT_FIELD
    source_provider: str = Field(min_length=1, max_length=64)
    retrieved_at: datetime
    as_of: datetime = Field(description="the close time of the last bar this rested on.")
    bar_count: int = Field(ge=1)
    observed_at: datetime = Field(
        description="when this finding was computed — caller-supplied, never datetime.now()."
    )
    knowledge_version: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _check_the_interpretation_is_the_registrys_own(self) -> TechnicalFinding:
        """The load-bearing invariant: a finding cannot carry prose the
        knowledge registry did not author.

        Checked on the model rather than only at construction, so a
        hand-assembled `TechnicalFinding` with invented interpretation text is
        rejected too. This is what makes "ORION never writes market commentary"
        a structural property instead of a convention in one function.
        """
        definition = TECHNICAL_KNOWLEDGE.get(self.concept)
        if definition is None:
            raise ValueError(
                f"concept {self.concept.value!r} has no definition in the technical "
                "knowledge registry"
            )
        expected = definition.interpretations.get(self.bias)
        if expected is None:
            raise ValueError(
                f"the knowledge registry has no interpretation for {self.concept.value!r} "
                f"with bias {self.bias.value!r}"
            )
        if self.interpretation != expected:
            raise ValueError(
                f"interpretation for {self.concept.value!r}/{self.bias.value!r} is not the "
                "one in the knowledge registry; interpretations are looked up, never written"
            )
        return self

    @model_validator(mode="after")
    def _check_timestamps_are_aware(self) -> TechnicalFinding:
        for name, moment in (
            ("retrieved_at", self.retrieved_at),
            ("as_of", self.as_of),
            ("observed_at", self.observed_at),
        ):
            if moment.tzinfo is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        return self


class TechnicalTimeframeReport(BaseModel):
    """Everything the analyst found on one timeframe, and what it could not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeframe: Timeframe
    symbol: str = Field(min_length=1)
    bar_count: int = Field(ge=0)
    evidence_fingerprint: str = _FINGERPRINT_FIELD
    source_provider: str = Field(min_length=1, max_length=64)
    retrieved_at: datetime
    as_of: datetime
    findings: tuple[TechnicalFinding, ...]
    unevaluated: tuple[UnevaluatedConcept, ...]
    bias: StructureBias = Field(
        description="The timeframe's overall lean, from the directional findings on it. "
        "NEUTRAL when they balance, UNCLEAR when none is directional."
    )
    volatility: VolatilityLevel = Field(
        description="The regime orion.analysis.structure.classify_volatility assigned this "
        "window, copied rather than re-derived. Carried as a field, not left inside a "
        "finding's observation text, because orion.analysts.synthesis reads it — a consumer "
        "parsing a sentence to recover a number it could have been handed is exactly the "
        "kind of seam that rots."
    )

    @model_validator(mode="after")
    def _check_every_concept_is_accounted_for(self) -> TechnicalTimeframeReport:
        """Each concept appears exactly once, as a finding or as unevaluated.

        A concept that silently appears in neither list would be an absence
        nobody can tell from an oversight, which is the reporting failure this
        codebase refuses everywhere else.
        """
        found = [finding.concept for finding in self.findings]
        skipped = [item.concept for item in self.unevaluated]
        together = found + skipped
        if len(set(together)) != len(together):
            raise ValueError(
                "a concept appears more than once across findings and unevaluated: "
                f"{sorted(concept.value for concept in together)}"
            )
        missing = set(TechnicalConcept) - set(together)
        if missing:
            raise ValueError(
                "every concept must be reported as either evaluated or unevaluated; "
                f"missing: {sorted(concept.value for concept in missing)}"
            )
        for finding in self.findings:
            if finding.timeframe is not self.timeframe:
                raise ValueError(
                    f"finding for {finding.concept.value!r} is on "
                    f"{finding.timeframe.value!r}, not this report's {self.timeframe.value!r}"
                )
            if finding.evidence_fingerprint != self.evidence_fingerprint:
                raise ValueError(
                    f"finding for {finding.concept.value!r} references a different Evidence "
                    "than this timeframe report"
                )
        return self


class TechnicalAnalystOutput(BaseModel):
    """One Technical Analyst pass over one symbol across one or more timeframes.

    ADVISORY ONLY, AND STRUCTURALLY SO. Nothing downstream reads this:
    `orion.decision` never sees it, the Risk Guardian never sees it, and it
    cannot reach `ProposedDecision`. It informs the person reading the report
    and the synthesis layer next door, never the machine proposing a trade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    availability: TechnicalAvailability
    symbol: str = Field(min_length=1)
    analyzed_at: datetime
    analyst_version: str = Field(min_length=1, max_length=32)
    knowledge_version: str = Field(min_length=1, max_length=32)
    timeframes: tuple[TechnicalTimeframeReport, ...]
    bias: StructureBias = Field(
        description="The aggregate lean across timeframes. Higher timeframes are not "
        "weighted more heavily: no calibration exists in this build to justify a weighting, "
        "so every timeframe counts once and a tie is reported as NEUTRAL."
    )
    timeframe_agreement: bool = Field(
        description="Whether every timeframe that resolved to a direction resolved to the "
        "same one. False when they disagree, which is a real finding rather than a problem "
        "to average away."
    )

    @model_validator(mode="after")
    def _check_availability_matches_content(self) -> TechnicalAnalystOutput:
        available = self.availability is TechnicalAvailability.AVAILABLE
        has_findings = any(report.findings for report in self.timeframes)
        if available and not has_findings:
            raise ValueError("availability is AVAILABLE but no timeframe produced a finding")
        if not available and has_findings:
            raise ValueError(
                f"availability is {self.availability.value!r} but findings are present; "
                "findings may only accompany AVAILABLE"
            )
        if self.availability is TechnicalAvailability.NO_INPUT and self.timeframes:
            raise ValueError("availability is NO_INPUT but timeframe reports are present")
        for report in self.timeframes:
            if report.symbol != self.symbol:
                raise ValueError(
                    f"timeframe report is for {report.symbol!r}, not this output's "
                    f"{self.symbol!r}; one pass covers one symbol"
                )
        seen = [report.timeframe for report in self.timeframes]
        if len(set(seen)) != len(seen):
            raise ValueError(
                f"a timeframe is reported twice: {sorted(item.value for item in seen)}"
            )
        if self.analyzed_at.tzinfo is None:
            raise ValueError("analyzed_at must be a timezone-aware datetime")
        return self

    @property
    def findings(self) -> tuple[TechnicalFinding, ...]:
        """Every finding across every timeframe, in timeframe order."""
        return tuple(finding for report in self.timeframes for finding in report.findings)
