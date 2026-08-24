"""Strict shapes for what the Macro Analyst is allowed to say.

Mirrors `orion.reasoning.schemas`' two-model split exactly: `MacroAnalystVerdict`
is exactly the JSON schema sent to Claude as `output_format` — nothing here is
provenance, so the model is never asked to name itself, version its own
prompt, or report which input it analyzed. `MacroAnalystOutput` adds the
provenance `orion.analysts.claude_macro_provider` observed, never asked of
the model.

Macro Analyst is advisory only. Nothing here can encode a trade direction, a
position size, a risk-policy change, a tool call, code, SQL, or a URL —
every field is a closed enum or a bounded scalar. `MacroPolicyPressure` is
deliberately phrased as a characterization of what the underlying economic
DATA implies about monetary-policy-relevant conditions, never as
asset-direction vocabulary ("bullish"/"bearish") and never as an action verb
("buy"/"sell") — see `docs/DECISIONS.md` for the full Mission 8 security
boundary this schema is one layer of.

`rationale`/`MacroEventAssessment.note` are the only free-text fields, both
length-bounded. Nothing in this package (or anywhere else — see
`tests/analysts/test_import_hygiene.py`) ever passes either field to `exec`,
`eval`, a shell, a database, or a second LLM call — they are display/audit
text only, exactly like `ReasoningVerdict.rationale`'s existing, tested
"never read by a decision rule" guarantee, extended to a package with no
decision rules at all.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SurpriseDirection(StrEnum):
    """How one event's actual value compared to its estimate — a factual
    comparison of two numbers, nothing more."""

    ABOVE_ESTIMATE = "above_estimate"
    BELOW_ESTIMATE = "below_estimate"
    IN_LINE = "in_line"
    NOT_APPLICABLE = "not_applicable"


class MarketRelevance(StrEnum):
    """How much the supplied batch, taken together, matters — independent
    of which direction any surprise leans."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class MacroPolicyPressure(StrEnum):
    """What the aggregate of releases implies about monetary-policy-relevant
    conditions — a characterization of the DATA's implied pressure on
    policy, never a trade direction or recommendation.

    `TIGHTENING_PRESSURE` means the data collectively reads firmer/hotter
    than expected (conditions that would tend to support tighter monetary
    policy). `EASING_PRESSURE` means the reverse. Deliberately not phrased
    as "bullish"/"bearish", "hawkish"/"dovish", or any asset-direction or
    trading-signal vocabulary — this describes the data, not an instruction
    about what to do with it.
    """

    TIGHTENING_PRESSURE = "tightening_pressure"
    EASING_PRESSURE = "easing_pressure"
    MIXED = "mixed"
    IN_LINE = "in_line"
    INSUFFICIENT_DATA = "insufficient_data"


class MacroEventAssessment(BaseModel):
    """One event's assessment, keyed back to its input by a deterministic,
    ORION-computed `event_id` — never a model-invented identifier. See
    `orion.analysts.claude_macro_provider` for how `event_id` is derived
    and independently verified against the exact set of events supplied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    surprise: SurpriseDirection
    note: str = Field(min_length=1, max_length=160)


class MacroAnalystVerdict(BaseModel):
    """The model-authored half of a Macro Analyst opinion — exactly the
    JSON schema sent to Claude as `output_format`. Immutable and closed
    (`extra="forbid"`): an unexpected key is a failed call, not a field to
    ignore.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_pressure: MacroPolicyPressure = Field(
        description="What the supplied events collectively imply about monetary-policy-"
        "relevant conditions. Never a trade direction."
    )
    market_relevance: MarketRelevance = Field(
        description="How much the supplied batch, taken together, matters."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Calibrated belief in this specific assessment, 0.0-1.0.",
    )
    key_event_assessments: tuple[MacroEventAssessment, ...] = Field(
        min_length=1,
        max_length=50,
        description="One assessment per supplied event, referencing each by its event_id. "
        "Every supplied event must be covered; no event may be omitted or invented.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=500,
        description="Concise plain-language summary of what in the supplied events drove "
        "this assessment. No trading recommendation, no position size, no risk-policy "
        "statement, no code, no commands, no URLs — text only.",
    )

    @model_validator(mode="after")
    def _check_no_duplicate_event_ids(self) -> MacroAnalystVerdict:
        ids = [a.event_id for a in self.key_event_assessments]
        if len(set(ids)) != len(ids):
            raise ValueError(f"key_event_assessments contains a duplicate event_id: {ids}")
        return self


class MacroAnalystOutput(MacroAnalystVerdict):
    """A validated Macro Analyst opinion plus the provenance needed to audit
    it later. Advisory only — nothing in ORION treats this as a decision,
    and nothing in this mission reads it at all beyond returning it to the
    caller (zero persistence, zero integration with Evidence/Decision/Risk/
    Paper/Memory/Reflection/Backtest — see `docs/DECISIONS.md`).
    """

    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    source_provider: str = Field(min_length=1)
    query_start: datetime
    query_end: datetime
    retrieved_at: datetime
    analyzed_at: datetime
