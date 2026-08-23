"""Operator-controlled Risk Guardian configuration.

`RiskPolicy` is the guardian's *input*, not its output — deliberately in a
separate module from `orion.risk.schemas` (the audit records the guardian
produces), so the thing being evaluated is never confused with the record of
the evaluation.

The kill switch lives here, as a policy field, and never as mutable state on
a long-lived guardian object. `orion.risk.guardian.review` must be
deterministic (same inputs + same timestamp/config -> same verdict); internal
mutable state would mean two calls at the same instant with the same
proposal could return different verdicts depending on unobservable prior
mutation, which breaks that guarantee outright and makes a journaled verdict
unreproducible from its own recorded inputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orion.analysis.structure import VolatilityLevel
from orion.config import Settings
from orion.data.validation import DEFAULT_MAX_STALENESS_BARS


class KillSwitchState(StrEnum):
    """Two states only — no `UNKNOWN`. An ambiguous switch is not a third
    state to reason about; it must resolve to blocking (see `Settings.
    orion_kill_switch`'s fail-safe default and `RiskPolicy.kill_switch`'s)."""

    ENGAGED = "engaged"
    DISENGAGED = "disengaged"


class RiskPolicy(BaseModel):
    """The operating limits the Risk Guardian evaluates every proposal against.

    Every field has a fail-closed default, so `RiskPolicy()` with zero
    arguments blocks every trade proposal outright (kill switch engaged) —
    there is no permissive degenerate case.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kill_switch: KillSwitchState = Field(default=KillSwitchState.ENGAGED)
    max_staleness_bars: float = Field(default=DEFAULT_MAX_STALENESS_BARS, gt=0)
    max_decision_age_bars: float = Field(default=1.0, gt=0)
    min_risk_reward: float = Field(default=1.0, ge=1.0)
    blocked_volatility_levels: tuple[VolatilityLevel, ...] = Field(
        default=(VolatilityLevel.UNKNOWN,)
    )

    @model_validator(mode="after")
    def _canonicalize_blocked_volatility_levels(self) -> RiskPolicy:
        # Sorted and de-duplicated so `policy_ref.content_hash` (orion.risk.
        # schemas.RiskPolicyRef) is reproducible regardless of construction
        # order — a `frozenset` here would make the JSON-dumped order depend
        # on per-process string hash randomization, silently breaking
        # provenance-hash reproducibility across processes. `object.
        # __setattr__` is the documented way to normalize a field from within
        # an "after" validator on a frozen model.
        canonical = tuple(sorted(set(self.blocked_volatility_levels), key=lambda v: v.value))
        object.__setattr__(self, "blocked_volatility_levels", canonical)
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskPolicy:
        """Build a `RiskPolicy` from `Settings`.

        Call this once per analysis cycle from a fresh `get_settings()`
        (which is deliberately uncached) rather than caching a `RiskPolicy`
        for the process lifetime — that is what gives an operator editing
        `.env` near-real-time kill-switch response without any new
        persistence or mutable state.
        """
        blocked = (
            (VolatilityLevel.UNKNOWN, VolatilityLevel.HIGH)
            if settings.orion_block_high_volatility
            else (VolatilityLevel.UNKNOWN,)
        )
        return cls(
            kill_switch=KillSwitchState(settings.orion_kill_switch),
            max_staleness_bars=settings.orion_max_staleness_bars,
            max_decision_age_bars=settings.orion_max_decision_age_bars,
            min_risk_reward=settings.orion_min_risk_reward,
            blocked_volatility_levels=blocked,
        )
