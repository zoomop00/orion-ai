"""Operator-controlled paper-execution configuration.

`PaperExecutionPolicy` controls *how* a Risk-Guardian-approved trade is
simulated — spread, slippage, fees, starting balance — a different concern
from `orion.risk.policy.RiskPolicy`, which controls *whether* a trade is
permitted at all. Kept as a separate object per the Mission 4 architecture
decision in `docs/DECISIONS.md` (B5): each layer owns its own
configuration, and merging "is this allowed" config with "how is this
simulated" config would blur a distinction this codebase draws everywhere
else (`orion.decision`/`orion.risk` are separate packages for the same
reason).

Every cost here is deterministic. No field, computation, or caller of this
policy may introduce randomness — spread/slippage/fees must be reproducible
from `(policy, price)` alone, so identical inputs to `orion.paper.execution.
execute` always produce an identical `Fill`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orion.config import Settings


class PaperExecutionPolicy(BaseModel):
    """Deterministic simulated-execution parameters. Spread/slippage/fee
    are basis points (1 bps = 0.01%), applied against the reference price —
    never a risk limit; see `orion.risk.policy.RiskPolicy` for those."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    starting_balance: float = Field(default=10_000.0, gt=0)
    spread_bps: float = Field(default=2.0, ge=0)
    slippage_bps: float = Field(default=1.0, ge=0)
    fee_bps: float = Field(default=1.0, ge=0)

    @classmethod
    def from_settings(cls, settings: Settings) -> PaperExecutionPolicy:
        """Build a `PaperExecutionPolicy` from `Settings`, mirroring
        `RiskPolicy.from_settings` exactly."""
        return cls(
            starting_balance=settings.paper_starting_balance,
            spread_bps=settings.paper_spread_bps,
            slippage_bps=settings.paper_slippage_bps,
            fee_bps=settings.paper_fee_bps,
        )
