"""Pure, read-only computation of prediction-vs-outcome facts for one
closed paper trade.

`reflect_on_closed_position` is the whole of Mission 5's v1 surface: given
a `DecisionJournal` and a `ClosedPosition`'s content fingerprint, it walks
the already-fingerprint-linked chain backward (`ClosedPosition` ->
`RiskVerdict` -> `ProposedDecision`, optionally enriched by the opening
`Fill`) and returns a `TradeReflection` — a set of *facts*, never a quality
score, a good/bad verdict, or anything that could be wired up to
automatically adjust `RiskPolicy`, reasoning prompts, or trading behavior.
"Distinguish a bad outcome from a bad decision" (the master brief's own
words for Mission 5) is satisfied by exposing the right facts side by side
— how large was the approval margin above policy minimum, did the exit
match the predicted take-profit or stop-loss — the judgment itself is left
to whoever reads a `TradeReflection`, deliberately never made by this
module. Computing that judgment here would make Reflection a second risk
authority sitting downstream of `orion.risk.guardian.review`, with its own
thresholds that could silently diverge from the one that actually gates
execution — exactly what this module must never become.

Pure function: no network, filesystem, randomness, or internal clock.
`now` is a required, caller-supplied parameter, stamped onto the result as
`reflected_at` — never `datetime.now()`, matching `orion.decision.engine.
decide`/`orion.risk.guardian.review`/`orion.paper.execution.execute`'s
identical discipline. Takes only a `DecisionJournal` (the abstract
interface, never `SqliteDecisionJournal` or any other concrete class) —
never a `PortfolioStore` of any kind, so there is no path through this
module's own type signature for it to ever touch current account state.
`DecisionJournal` itself declares no update/delete method (see `orion.
memory.journal`), so even a bug in this module has no method available to
call that could mutate history.

The predicted risk/reward ratio reuses `orion.risk.controls.
classify_trade_levels` (the exact coherence check `orion.risk.controls.
risk_reward_meets_minimum` itself performs first) and then the identical
inline formula that control uses — there is no separately exported
ratio-computing function to import instead, so this is the faithful reuse,
not a second implementation that could drift from the first. No new
hashing mechanism: every fingerprint on `TradeReflection` is copied
directly from the fingerprint fields already present on the journaled
records (`ClosedPosition.opened_by_verdict_fingerprint`, `RiskVerdict.
decision_ref.content_hash`), never recomputed with a new function.

`TradeReflection.thesis` carries the specialist evidence journaled for the same
analysis, when there was one. It is enrichment on the same terms as
`opening_fee`: reported beside the outcome, never scored against it, and absent
without complaint. Reflection still computes no judgement — a thesis that read
bullish before a losing trade is a fact about what ORION observed, not evidence
that the decision was wrong, and this module continues to leave that reading
entirely to whoever holds the result.

`reflect_on_recent_closed_positions` is the batch form, and the only place in
this module that hashes anything: `DecisionJournal.list_closed_positions_by_
symbol` returns `ClosedPosition` objects rather than the fingerprints they are
stored under, so a fingerprint has to be derived to look each one's chain up.
It is derived with `orion.provenance.canonical_hash` — literally the function
`SqliteDecisionJournal.record_closed_position` writes with and
`get_closed_position` verifies with, since neither `Fill` nor `ClosedPosition`
has a domain-named wrapper the way `Evidence`/`ProposedDecision`/`RiskVerdict`
do. One shared mechanism, not a second implementation of it.
"""

from __future__ import annotations

from datetime import datetime

from orion.memory.journal import DecisionJournal
from orion.paper.schemas import ClosedPosition, CloseReason
from orion.provenance import canonical_hash
from orion.reflection.schemas import ReflectionBatch, TradeReflection
from orion.risk.controls import classify_trade_levels
from orion.risk.schemas import RiskVerdict
from orion.thesis.schemas import MarketThesis


class ReflectionError(RuntimeError):
    """Raised when a `TradeReflection` cannot be computed at all — a
    missing link in the journaled chain, a naive timestamp, or an
    ambiguous opening `Fill`. Own top-level hierarchy, never inheriting
    `DecisionJournalError` or any other layer's — mirrors this codebase's
    established "every layer owns its own RuntimeError" rule
    (`RiskGuardianError`, `PaperExecutionError`, `DecisionJournalError`).

    `DecisionJournalIntegrityError` raised by the underlying
    `DecisionJournal` calls (a corrupted journal record) is never caught
    here — it propagates unchanged, since it already carries the correct
    fail-closed meaning one layer down, and re-wrapping it would hide which
    layer actually detected the corruption.
    """


def reflect_on_closed_position(
    journal: DecisionJournal,
    closed_position_fingerprint: str,
    *,
    now: datetime,
) -> TradeReflection:
    """Compute the prediction-vs-outcome facts for one closed trade.

    Walks `ClosedPosition.opened_by_verdict_fingerprint` -> `RiskVerdict` ->
    `RiskVerdict.decision_ref.content_hash` -> `ProposedDecision`, using
    only `journal`'s existing read methods. Raises `ReflectionError` if the
    `ClosedPosition`, its `RiskVerdict`, or that verdict's `ProposedDecision`
    is missing from the journal — an incomplete write sequence is a real,
    reachable state (Mission 2 deliberately does not enforce cross-record
    ordering), not a hypothetical one — never a partial or best-guess
    result. `DecisionJournalIntegrityError` from a corrupted journal record
    propagates unchanged.

    The `MarketThesis` is optional enrichment too (`thesis`, `None` if none
    was journaled), looked up by the verdict's own `analysis_id` — see
    `_find_thesis`. A trade whose analysis recorded no thesis, and every trade
    opened before Mission 8 v2.2 existed, reflects perfectly well without one.

    The opening `Fill` is optional enrichment (`opening_fee`, `None` if
    absent): looked up via `list_fills_by_verdict_fingerprint` and matched
    to this `ClosedPosition` by `fill.filled_at == closed.opened_at` — the
    exact instant the position was opened. Zero matches leaves `opening_fee`
    `None`; more than one match is a genuine ambiguity (e.g. a replayed
    `execute()` call journaled more than one `Fill` for the same verdict)
    and raises `ReflectionError` rather than guessing which one is real.

    Raises `ReflectionError` if `now` is a naive (timezone-unaware)
    datetime, matching `orion.decision.engine.decide`/`orion.risk.guardian.
    review`/`orion.paper.execution.execute`'s identical check, or if the
    decision's trade levels are missing or geometrically incoherent — this
    is provably unreachable through any construction path that also
    produced an APPROVED verdict (`review()` already required coherent,
    non-degenerate levels before approving), so it has no dedicated
    positive test; it is restated defense-in-depth, the same category as
    `orion.paper.execution.execute`'s own stop_loss/take_profit check, not
    a gap in coverage.
    """
    if now.tzinfo is None:
        raise ReflectionError("now must be a timezone-aware datetime")

    closed = journal.get_closed_position(closed_position_fingerprint)
    if closed is None:
        raise ReflectionError(
            f"no ClosedPosition found under fingerprint {closed_position_fingerprint!r}"
        )

    verdict = journal.get_verdict(closed.opened_by_verdict_fingerprint)
    if verdict is None:
        raise ReflectionError(
            f"ClosedPosition under fingerprint {closed_position_fingerprint!r} references "
            f"verdict fingerprint {closed.opened_by_verdict_fingerprint!r}, which is not in "
            "the journal; cannot reflect on an incomplete chain"
        )

    decision = journal.get_decision(verdict.decision_ref.content_hash)
    if decision is None:
        raise ReflectionError(
            f"RiskVerdict under fingerprint {closed.opened_by_verdict_fingerprint!r} "
            f"references decision fingerprint {verdict.decision_ref.content_hash!r}, which "
            "is not in the journal; cannot reflect on an incomplete chain"
        )

    ref = decision.reasoning_ref
    evidence_ref = decision.evidence_ref
    failure = classify_trade_levels(
        ref.direction, evidence_ref.last_price, ref.stop_loss, ref.take_profit
    )
    if failure is not None:
        raise ReflectionError(
            f"decision fingerprint {verdict.decision_ref.content_hash!r} has missing or "
            f"incoherent trade levels ({failure.value}); cannot compute a predicted "
            "risk/reward for it"
        )
    assert ref.stop_loss is not None and ref.take_profit is not None  # narrowed by the check above

    predicted_risk = abs(evidence_ref.last_price - ref.stop_loss)
    predicted_reward = abs(ref.take_profit - evidence_ref.last_price)
    predicted_risk_reward = predicted_reward / predicted_risk

    opening_fee = _find_opening_fee(journal, closed)
    thesis = _find_thesis(journal, verdict)

    return TradeReflection(
        closed_position_fingerprint=closed_position_fingerprint,
        opened_by_verdict_fingerprint=closed.opened_by_verdict_fingerprint,
        decision_fingerprint=verdict.decision_ref.content_hash,
        predicted_action=ref.action,
        predicted_direction=ref.direction,
        predicted_confidence=ref.confidence,
        predicted_risk_level=ref.risk_level,
        predicted_stop_loss=ref.stop_loss,
        predicted_take_profit=ref.take_profit,
        predicted_risk_reward=predicted_risk_reward,
        policy_min_risk_reward=verdict.policy_ref.min_risk_reward,
        approval_margin=predicted_risk_reward - verdict.policy_ref.min_risk_reward,
        close_reason=closed.close_reason,
        entry_price=closed.entry_price,
        exit_price=closed.exit_price,
        realized_pnl=closed.realized_pnl,
        closing_fee=closed.fee,
        opening_fee=opening_fee,
        exit_matched_predicted_take_profit=closed.close_reason is CloseReason.TAKE_PROFIT_HIT,
        exit_matched_predicted_stop_loss=closed.close_reason is CloseReason.STOP_LOSS_HIT,
        thesis=thesis,
        reflected_at=now,
    )


def _find_thesis(journal: DecisionJournal, verdict: RiskVerdict) -> MarketThesis | None:
    """The `MarketThesis` formed for the analysis this trade came from.

    FOUND BY A SIBLING KEY, NEVER BY A BACK-REFERENCE. `analysis_id` is minted
    once per analysis and stamped identically on the decision, the verdict and
    the thesis; the thesis holds no fingerprint of the verdict, so nothing in
    the advisory record points at the authority that reviewed it. This is the
    whole reason Mission 8 v2.2 could connect the two at all.

    Returns `None` when no thesis was recorded — the permanent state of every
    trade opened before this existed — and raises `ReflectionError` when more
    than one shares the id. That is a real, reachable ambiguity rather than a
    hypothetical: `analysis_id` is derived from the symbol, the timeframe and
    the last bar's timestamp, so two analyses inside one bar share it, and each
    can form its own thesis at a different `formed_at`. `_find_opening_fee`
    refuses to guess between duplicates for the same reason and this does too.
    """
    candidates = journal.list_theses_by_analysis_id(verdict.analysis_id)
    if len(candidates) > 1:
        raise ReflectionError(
            f"{len(candidates)} MarketTheses are recorded under analysis_id "
            f"{verdict.analysis_id!r}; cannot determine which one this trade was formed "
            "alongside without guessing"
        )
    return candidates[0] if candidates else None


def _find_opening_fee(journal: DecisionJournal, closed: ClosedPosition) -> float | None:
    """The fee of the Fill that opened `closed`, disambiguated by
    `filled_at == closed.opened_at` — the exact instant the position was
    opened. Returns `None` if no Fill matches (optional enrichment, never
    required for a valid `TradeReflection`). Raises `ReflectionError` if
    more than one Fill matches — a genuine ambiguity this function refuses
    to silently resolve by guessing."""
    candidates = journal.list_fills_by_verdict_fingerprint(closed.opened_by_verdict_fingerprint)
    matches = [fill for fill in candidates if fill.filled_at == closed.opened_at]
    if len(matches) > 1:
        raise ReflectionError(
            f"{len(matches)} Fills match verdict fingerprint "
            f"{closed.opened_by_verdict_fingerprint!r} at filled_at="
            f"{closed.opened_at.isoformat()!r}; cannot determine which one opened this "
            "position without guessing"
        )
    return matches[0].fee if matches else None


def reflect_on_recent_closed_positions(
    journal: DecisionJournal,
    symbol: str,
    *,
    limit: int,
    now: datetime,
) -> ReflectionBatch:
    """Reflect on `symbol`'s `limit` most recently closed trades.

    Pure and read-only, exactly like `reflect_on_closed_position`: this calls
    it once per position and adds no judgment of its own. `now` is stamped on
    every `TradeReflection` and on the batch, so one batch is one instant.

    THE TAIL SLICE IS DELIBERATE, AND SO IS NOT PUSHING IT INTO THE QUERY.
    `list_closed_positions_by_symbol`'s `limit` is applied to a `closed_at`
    *ascending* order, so passing it through would return the oldest trades
    under the name "recent". Rather than change a journal contract other
    callers already depend on, this reads the symbol's closed positions and
    takes the last `limit` of them — which also yields the honest
    `total_closed_positions` count the batch reports. For a local paper journal
    that is a bounded read; if it ever stops being one, the fix belongs in the
    journal's query surface, not in a narrower answer here.

    Raises `ReflectionError` for a naive `now` or a non-positive `limit` —
    checked before any read, so a bad call reads nothing rather than
    misreporting every trade as unreflectable. A `ClosedPosition` whose chain
    cannot be walked is *not* an error: its fingerprint is listed in
    `unreflectable_fingerprints`. `DecisionJournalIntegrityError` is never
    caught and propagates, because corruption is not incompleteness.
    """
    if now.tzinfo is None:
        raise ReflectionError("now must be a timezone-aware datetime")
    if limit < 1:
        raise ReflectionError(f"limit must be at least 1, got {limit}")

    closed_positions = journal.list_closed_positions_by_symbol(symbol)
    window = closed_positions[-limit:] if limit < len(closed_positions) else closed_positions

    reflections: list[TradeReflection] = []
    unreflectable: list[str] = []
    for closed in window:
        # The fingerprint the journal stored this record under. Recomputed with
        # the journal's own hashing function rather than carried out of the
        # query, because the read API returns models, not row keys.
        fingerprint = canonical_hash(closed)
        try:
            reflections.append(reflect_on_closed_position(journal, fingerprint, now=now))
        except ReflectionError:
            # The exception is not bound, formatted or re-raised: which link is
            # missing is discoverable from the journal itself, and the batch's
            # job is to say *that* a reflection is impossible, never to
            # paraphrase why in a field a caller might parse.
            unreflectable.append(fingerprint)

    return ReflectionBatch(
        symbol=symbol,
        reflections=tuple(reflections),
        unreflectable_fingerprints=tuple(unreflectable),
        total_closed_positions=len(closed_positions),
        reflected_at=now,
    )
