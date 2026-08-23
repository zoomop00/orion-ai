"""Deterministic Decision Engine: Evidence + ReasoningOutput -> ProposedDecision.

The Decision Engine answers exactly one question: does a well-formed trade
proposal follow from this evidence and this reasoning? It compares two
already-validated objects for internal coherence. It does not recompute
trend, momentum, volatility, or levels — that is `orion.analysis`, and
re-deriving its conclusions under a different name would be duplication, not
a second opinion. It does not read the model's free-form `rationale`, and it
does not read `confidence` as a threshold: no rule in `orion.decision`
branches on how sure the model claimed to be. It does not evaluate whether a
coherent proposal is affordable, survivable, or permitted — position size,
max risk per trade, daily/weekly loss limits, drawdown, exposure,
correlation, liquidity, minimum risk/reward, abnormal-volatility and
stale-data gates, and the emergency kill switch are money-and-portfolio
controls owned by the Risk Guardian (Mission 3), which is independently
testable and remains the final safety authority. The Decision Engine can only
ever narrow: it passes the AI's action through unchanged, or it downgrades to
WAIT. It never upgrades WAIT to a trade, never invents a direction the AI did
not hold, and never authorizes anything. A ProposedDecision with action=BUY
means "nothing in the evidence contradicts this proposal," not "take this
trade."

Two things that look like natural Decision Engine rules and are deliberately
not implemented here:

* Staleness. `decided_at` is stamped on the record and never compared to
  `evidence.as_of` — "stale data" is an explicit Risk Guardian control, and
  `orion.data.validation.validate_snapshot` already classifies it upstream.
* Volatility magnitude. `structure.volatility` is read only for the UNKNOWN
  case (ATR unavailable); its LOW/MEDIUM/HIGH cut points are documented in
  `docs/DECISIONS.md` as uncalibrated placeholders, so no rule here branches
  on them. "Abnormal volatility" is a Risk Guardian control, to be built on
  calibrated thresholds once Mission 6 produces them.

Before any rule runs, the engine verifies that `reasoning` actually belongs to
`evidence`: `reasoning.evidence_fingerprint` (stamped by the reasoning
provider, never model-authored — see `orion.reasoning.claude_provider`) must
equal `evidence_fingerprint(evidence)` recomputed here. A mismatch is not
market uncertainty and is never downgraded to a WAIT ProposedDecision — it
means this engine cannot establish what the reasoning was even about, so
evaluating any rule would produce a plausible-looking but unfounded result.
It raises EvidenceMismatchError instead, the same fail-closed treatment as
every other input this engine cannot meaningfully evaluate.
"""

from __future__ import annotations

from datetime import datetime

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.decision.rules import context_codes, evaluate_all
from orion.decision.schemas import (
    DecisionReasonCode,
    EvidenceRef,
    ProposedDecision,
    ReasoningRef,
    RuleStatus,
)
from orion.reasoning.schemas import Action, Direction, ReasoningOutput

ENGINE_VERSION = "2026-08-23.2"


class DecisionEngineError(RuntimeError):
    """Raised when the engine cannot meaningfully evaluate a decision.

    Distinct from a WAIT ProposedDecision: WAIT means the engine evaluated
    the question and the answer is "not this one." This error means the
    engine could not evaluate the question at all — a missing or malformed
    input, not market uncertainty. "I could not decide" is not "the decision
    is WAIT." Never caught internally and converted to a WAIT record — that
    would hide a broken input behind a stream of plausible-looking
    abstentions, exactly what `orion.reasoning.provider_base` already refuses
    to do for provider failures.
    """


class EvidenceMismatchError(DecisionEngineError):
    """Raised when `reasoning.evidence_fingerprint` does not match `evidence`.

    A specific subclass, not just a specific message: this is an integrity
    failure distinct from a malformed or missing input. Nothing about
    `evidence` or `reasoning` individually is invalid — the problem is that
    this engine has no basis to believe `reasoning` was produced from this
    `evidence` at all, so evaluating a rule against them would be evaluating
    a coincidence, not a proposal. Fails closed exactly like every other
    DecisionEngineError: raised, never caught internally and laundered into
    a WAIT ProposedDecision, which would make a pairing bug look like
    ordinary market uncertainty.
    """


def decide(
    evidence: Evidence,
    reasoning: ReasoningOutput,
    *,
    analysis_id: str,
    now: datetime,
) -> ProposedDecision:
    """Turn one Evidence + ReasoningOutput pair into a ProposedDecision.

    Pure function: no network, filesystem, clock, or randomness. Identical
    arguments always produce an identical (save for object identity)
    ProposedDecision, which is what makes this testable without calling
    Anthropic or Twelve Data.

    `analysis_id` and `now` are required, caller-supplied values rather than
    minted here (`uuid4()`, `datetime.now()`) — generating either internally
    would make two calls on identical inputs produce unequal results,
    breaking the determinism guarantee above. No orchestrator exists yet to
    mint them; callers (and tests) supply a literal until one does.

    Raises DecisionEngineError if `evidence` or `reasoning` is not a valid
    instance of its expected type, or is structurally impossible (a
    non-positive price, zero bars, a blank analysis_id) — see the module
    docstring's "I could not decide" distinction. Raises the more specific
    EvidenceMismatchError if `reasoning.evidence_fingerprint` does not match
    `evidence` — this engine cannot establish that `reasoning` was produced
    from `evidence` at all, which is not the same fact as evidence and
    reasoning individually being valid but disagreeing (that case, unlike
    this one, downgrades to a WAIT ProposedDecision — see `rules.py`).
    Otherwise always returns a ProposedDecision, including when every rule
    fails: that case downgrades to WAIT rather than raising, because WAIT
    must remain a first-class, fully auditable decision.
    """
    if not isinstance(evidence, Evidence):
        raise DecisionEngineError(f"evidence must be an Evidence instance, got {type(evidence)!r}")
    if not isinstance(reasoning, ReasoningOutput):
        raise DecisionEngineError(
            f"reasoning must be a ReasoningOutput instance, got {type(reasoning)!r}"
        )
    if evidence_fingerprint(evidence) != reasoning.evidence_fingerprint:
        raise EvidenceMismatchError(
            "reasoning.evidence_fingerprint does not match the Evidence passed to "
            "decide(); cannot establish that this reasoning was produced from this "
            "evidence"
        )
    if not analysis_id.strip():
        raise DecisionEngineError("analysis_id must be a non-empty string")
    if evidence.last_price <= 0:
        raise DecisionEngineError(
            f"evidence.last_price must be positive, got {evidence.last_price}"
        )
    if evidence.bar_count < 1:
        raise DecisionEngineError(
            f"evidence.bar_count must be at least 1, got {evidence.bar_count}"
        )

    outcomes = evaluate_all(evidence, reasoning)
    any_failed = any(outcome.status is RuleStatus.FAILED for outcome in outcomes)

    if any_failed:
        action, direction = Action.WAIT, Direction.NONE
    else:
        action, direction = reasoning.action, reasoning.direction

    reason_codes: list[DecisionReasonCode] = [
        DecisionReasonCode.DOWNGRADED_TO_WAIT if any_failed else DecisionReasonCode.ALL_RULES_PASSED
    ]
    for outcome in outcomes:
        if outcome.reason_code is not None and outcome.reason_code not in reason_codes:
            reason_codes.append(outcome.reason_code)
    for code in context_codes(reasoning):
        if code not in reason_codes:
            reason_codes.append(code)

    return ProposedDecision(
        analysis_id=analysis_id,
        decided_at=now,
        action=action,
        direction=direction,
        reason_codes=tuple(reason_codes),
        rule_outcomes=outcomes,
        evidence_ref=EvidenceRef.from_evidence(evidence),
        reasoning_ref=ReasoningRef.from_reasoning(reasoning),
        engine_version=ENGINE_VERSION,
    )
