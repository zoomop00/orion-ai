"""Shared builders for orion.paper tests.

Reuses `tests.risk.conftest`'s builders for Evidence/ProposedDecision/
RiskPolicy/PortfolioState rather than duplicating them — `orion.paper`
consumes exactly those objects, produced exactly the way the real pipeline
would produce them (via `decide()`/`review()`, not hand-constructed).
"""

from __future__ import annotations

from dataclasses import dataclass

from orion.analysis.evidence import Evidence
from orion.decision.schemas import ProposedDecision
from orion.reasoning.schemas import Action, Direction
from orion.risk.guardian import review
from orion.risk.portfolio import PortfolioState
from orion.risk.schemas import RiskOutcome, RiskVerdict
from tests.risk.conftest import (
    BASE_TIME,
    build_evidence,
    build_policy,
    build_portfolio,
    build_position,
    build_proposal,
)

__all__ = [
    "BASE_TIME",
    "ApprovedTrade",
    "build_evidence",
    "build_policy",
    "build_portfolio",
    "build_position",
    "build_proposal",
    "build_approved_trade",
]


@dataclass(frozen=True)
class ApprovedTrade:
    """Everything `orion.paper.execution.execute` needs, already validated
    through the real `decide()`/`review()` pipeline -- never hand-assembled.
    """

    proposal: ProposedDecision
    verdict: RiskVerdict
    evidence: Evidence
    portfolio: PortfolioState


def build_approved_trade(
    *,
    action: Action = Action.BUY,
    direction: Direction = Direction.LONG,
    entry_price: float = 2400.0,
    stop_loss: float = 2350.0,
    take_profit: float = 2500.0,
    cash: float = 100_000.0,
) -> ApprovedTrade:
    """A coherent, affordable trade proposal, reviewed through the real
    `decide()`/`review()` pipeline and asserted APPROVED -- the standard
    starting point for `orion.paper.execution` tests."""
    evidence = build_evidence(last_price=entry_price)
    proposal = build_proposal(
        action=action,
        direction=direction,
        evidence=evidence,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    policy = build_policy()
    portfolio = build_portfolio(cash=cash, starting_balance=cash)
    verdict = review(proposal, evidence, policy=policy, portfolio=portfolio, now=BASE_TIME)
    failed_details = [c.detail for c in verdict.control_outcomes if c.status.value == "failed"]
    assert verdict.outcome is RiskOutcome.APPROVED, (
        f"build_approved_trade's fixture must produce an APPROVED verdict, "
        f"got {verdict.outcome.value!r}: {failed_details}"
    )
    return ApprovedTrade(proposal=proposal, verdict=verdict, evidence=evidence, portfolio=portfolio)
