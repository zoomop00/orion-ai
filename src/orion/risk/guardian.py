"""The Risk Guardian: the final deterministic safety authority.

Boundary with the Decision Engine: the Decision Engine answers "does a
well-formed trade proposal follow from this evidence and this reasoning?"
The Risk Guardian answers a different question — "is this well-formed
proposal permitted to be acted on, at this moment, under these limits?" The
Decision Engine narrows on epistemic grounds (the evidence does not support
the claim); the Risk Guardian narrows on prudential grounds (even granting
the claim, we will not act on it right now). Both can only ever narrow to
WAIT. Neither authorizes execution; there is no executor anywhere in this
codebase.

Two things that look like natural Risk Guardian controls and are deliberately
not implemented here, because the state they need does not exist yet: max
daily/weekly loss, max drawdown, max exposure, position size, max risk/trade,
and correlation/liquidity controls all require knowing open positions and
account equity (Mission 4, unbuilt) or multiple simultaneously-tracked
instruments (ORION trades one symbol). Per the precedent in
`docs/DECISIONS.md` ("not building the code path at all is a stronger
guarantee than building it disabled"), these are not stubbed, not given a
`None`-defaulted parameter, and not given `RiskControlName` members here —
Mission 4 adds them by adding a required second context object to `review()`.

The guardian never accepts a `ReasoningOutput` directly — only a
`ProposedDecision`. Its frozen `reasoning_ref` (a `ReasoningRef`, see
`orion.decision.schemas`) carries the AI's action/direction/stop_loss/
take_profit, and — unlike `rationale` — also `confidence` and `risk_level`:
those two fields are present and directly readable as
`proposal.reasoning_ref.confidence`/`.risk_level` by any control function,
not structurally absent. `rationale` is the one field that genuinely has no
path into `ProposedDecision` at all — `ReasoningRef` never carries it, so
"Claude rationale must not be used as a risk signal" *is* a structural
guarantee. "AI confidence must never override a failed risk control" and
"AI risk_level is advisory only" are not: they hold only because no control
in `orion.risk.controls` reads either field today, a property enforced by
`tests/risk/test_guardian.py`'s `test_confidence_cannot_change_the_outcome`/
`test_risk_level_cannot_change_the_outcome`, not by this module's types. A
future control could read `proposal.reasoning_ref.confidence` and nothing
here would stop it at construction time — this remains a behavioral
invariant of this package's controls, not a structural one of its input.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.data.validation import TIMEFRAME_INTERVAL
from orion.decision.schemas import ProposedDecision
from orion.reasoning.schemas import Action, Direction
from orion.risk.controls import context_codes, evaluate_all
from orion.risk.policy import RiskPolicy
from orion.risk.schemas import (
    ControlStatus,
    DecisionRef,
    ExecutionMode,
    RiskOutcome,
    RiskPolicyRef,
    RiskReasonCode,
    RiskVerdict,
)

GUARDIAN_VERSION = "2026-08-23.1"


class RiskGuardianError(RuntimeError):
    """Raised when the guardian cannot meaningfully evaluate a proposal.

    Distinct from a returned `RiskVerdict`: a verdict means the guardian
    evaluated the question and the answer is APPROVED, VETOED, or
    NO_TRADE_PROPOSED. This error means the guardian could not evaluate the
    question at all — a missing or malformed input, not market or portfolio
    caution. "I could not decide" is not "the verdict is a veto." Never
    caught internally and converted to a veto or a synthetic WAIT verdict —
    that would hide a broken input behind a stream of plausible-looking
    refusals, exactly what `orion.decision.engine.DecisionEngineError`
    already refuses to do one layer down.
    """


class EvidenceLinkageError(RiskGuardianError):
    """Raised when `evidence_fingerprint(evidence)` does not match
    `proposal.evidence_ref.content_hash`.

    The exact analogue of `orion.decision.engine.EvidenceMismatchError`, one
    layer up: nothing about `proposal` or `evidence` individually is invalid,
    but there is no basis to believe `proposal` concerns this `evidence`, so
    evaluating a control against them would be evaluating a coincidence, not
    a proposal. Deliberately not a subclass of `DecisionEngineError` — each
    layer in ORION owns its own top-level `RuntimeError`
    (`MarketDataProviderError`, `ReasoningProviderError`,
    `DecisionEngineError`, `RiskGuardianError`), never inheriting another
    layer's error hierarchy.
    """


def review(
    proposal: ProposedDecision,
    evidence: Evidence,
    *,
    policy: RiskPolicy,
    now: datetime,
) -> RiskVerdict:
    """Decide whether `proposal` is permitted to be acted on, right now,
    under `policy`.

    Pure function: no network, filesystem, or randomness, and no clock or
    configuration beyond what is explicitly passed in. Identical arguments
    always produce an identical (save for object identity) `RiskVerdict`.

    `policy` and `now` are required, caller-supplied values — never read from
    a module-level singleton or minted internally — for the same reason
    `orion.decision.engine.decide` requires `analysis_id`/`now`: internal
    state or an internal clock would make two calls with the same proposal
    produce different verdicts depending on unobservable prior state,
    breaking the determinism guarantee above and making a journaled verdict
    unreproducible from its own recorded inputs. Build a fresh `RiskPolicy`
    once per analysis cycle (`RiskPolicy.from_settings(get_settings())`) —
    `get_settings()` is deliberately uncached — rather than caching one for
    the process lifetime; that is what gives an operator editing `.env`
    near-real-time kill-switch response with zero new state.

    Raises RiskGuardianError if `proposal`/`evidence`/`policy` is not a valid
    instance of its expected type, if `now`/`proposal.decided_at`/
    `evidence.as_of` is a naive (timezone-unaware) datetime, if
    `proposal.analysis_id` is blank, if `evidence.last_price` is not
    positive, or if `evidence.timeframe` has no known bar interval. Raises
    the more specific EvidenceLinkageError if `proposal.evidence_ref` does
    not match `evidence`. Otherwise always returns a RiskVerdict, including
    when every control fails: that case is an auditable VETOED verdict, never
    an exception, because a veto must remain a first-class, fully auditable
    outcome — see the module docstring's "I could not decide" distinction.
    """
    if not isinstance(proposal, ProposedDecision):
        raise RiskGuardianError(
            f"proposal must be a ProposedDecision instance, got {type(proposal)!r}"
        )
    if not isinstance(evidence, Evidence):
        raise RiskGuardianError(f"evidence must be an Evidence instance, got {type(evidence)!r}")
    if not isinstance(policy, RiskPolicy):
        raise RiskGuardianError(f"policy must be a RiskPolicy instance, got {type(policy)!r}")
    if now.tzinfo is None:
        raise RiskGuardianError("now must be a timezone-aware datetime")
    if proposal.decided_at.tzinfo is None:
        raise RiskGuardianError("proposal.decided_at must be a timezone-aware datetime")
    if evidence.as_of.tzinfo is None:
        raise RiskGuardianError("evidence.as_of must be a timezone-aware datetime")
    if not proposal.analysis_id.strip():
        raise RiskGuardianError("proposal.analysis_id must be a non-empty string")
    if evidence.last_price <= 0:
        raise RiskGuardianError(f"evidence.last_price must be positive, got {evidence.last_price}")
    if evidence.timeframe not in TIMEFRAME_INTERVAL:
        raise RiskGuardianError(f"no known bar interval for timeframe {evidence.timeframe!r}")
    if evidence_fingerprint(evidence) != proposal.evidence_ref.content_hash:
        raise EvidenceLinkageError(
            "proposal.evidence_ref does not match the Evidence passed to review(); "
            "cannot establish that this proposal was produced from this evidence"
        )

    outcomes = evaluate_all(proposal, evidence, policy, now)
    any_failed = any(o.status is ControlStatus.FAILED for o in outcomes)
    decision_is_wait = proposal.action is Action.WAIT

    if decision_is_wait:
        action, direction = Action.WAIT, Direction.NONE
        outcome = RiskOutcome.NO_TRADE_PROPOSED
    elif any_failed:
        action, direction = Action.WAIT, Direction.NONE
        outcome = RiskOutcome.VETOED
    else:
        action, direction = proposal.action, proposal.direction
        outcome = RiskOutcome.APPROVED

    approved_execution_mode = (
        ExecutionMode.PAPER if outcome is RiskOutcome.APPROVED else ExecutionMode.NONE
    )

    reason_codes: list[RiskReasonCode] = [
        {
            RiskOutcome.APPROVED: RiskReasonCode.ALL_CONTROLS_PASSED,
            RiskOutcome.VETOED: RiskReasonCode.VETOED_BY_RISK_CONTROL,
            RiskOutcome.NO_TRADE_PROPOSED: RiskReasonCode.NO_TRADE_PROPOSED,
        }[outcome]
    ]
    for o in outcomes:
        if o.reason_code is not None and o.reason_code not in reason_codes:
            reason_codes.append(o.reason_code)
    for code in context_codes(proposal):
        if code not in reason_codes:
            reason_codes.append(code)

    return RiskVerdict(
        analysis_id=proposal.analysis_id,
        reviewed_at=now,
        outcome=outcome,
        action=action,
        direction=direction,
        approved_execution_mode=approved_execution_mode,
        reason_codes=tuple(reason_codes),
        control_outcomes=outcomes,
        decision_ref=DecisionRef.from_proposal(proposal),
        policy_ref=RiskPolicyRef.from_policy(policy),
        guardian_version=GUARDIAN_VERSION,
    )
