"""The SQLite implementation of `orion.memory.journal.DecisionJournal`.

Every `record_*` is one atomic transaction (`engine.begin()`): an
`INSERT ... ON CONFLICT(fingerprint) DO NOTHING`, followed — inside the same
transaction — by reading back the row now stored under that fingerprint and
comparing it byte-for-byte against what this call intended to write. Three
outcomes, and only three:

* No row existed: the insert lands, the read-back trivially matches. A
  genuinely new fact.
* A row already existed with identical payload: the insert is a no-op, the
  read-back matches anyway. A safe, idempotent replay — the whole point of
  content-addressed primary keys.
* A row already existed with *different* payload under the same
  fingerprint: cryptographically should never happen (a SHA-256 collision),
  but is verified rather than assumed — `DecisionJournalIntegrityError` is
  raised instead of silently keeping whichever version was written first.

`get_*` performs the mirror-image check on the way out: reconstructs the
domain object from the stored payload, recomputes its fingerprint, and
raises `DecisionJournalIntegrityError` if it no longer matches the row's own
primary key (on-disk corruption, a hand-edited row, a bit flip) — the same
"recompute and compare, never trust stored linkage blindly" discipline
`orion.risk.guardian` already applies to `PortfolioState.equity`.

Reconstruction itself (`Model.model_validate_json`) is never called
unguarded: `_deserialize` below wraps every call site (every `get_*`,
`list_decisions_by_analysis_id`, `_verdicts_from_rows`) so a payload that is
not even valid JSON, or is valid JSON that fails the model's own schema —
not just "parses fine but hashes wrong" — also raises
`DecisionJournalIntegrityError`, never a raw `pydantic.ValidationError`. A
fresh adversarial pass after this module's first implementation found that
gap empirically (a payload of literal garbage bytes raised an unwrapped
`pydantic_core.ValidationError`, violating this class's own documented "I
could not read the real journal, fail closed through my own error type"
contract) — this is the fix, not a hypothetical concern.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, Row, Select, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from orion.analysis.evidence import Evidence, evidence_fingerprint
from orion.decision.schemas import ProposedDecision, decision_fingerprint
from orion.memory.journal import (
    DecisionJournal,
    DecisionJournalError,
    DecisionJournalIntegrityError,
)
from orion.memory.schema import (
    closed_position_table,
    evidence_table,
    fill_table,
    proposed_decision_table,
    reasoning_output_table,
    risk_verdict_table,
)
from orion.paper.schemas import ClosedPosition, Fill
from orion.provenance import canonical_hash
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.schemas import RiskVerdict, risk_verdict_fingerprint


def _deserialize[ModelT: BaseModel](
    model_cls: type[ModelT], payload: str, *, fingerprint: str, table_name: str
) -> ModelT:
    """Reconstruct `model_cls` from `payload`, never letting a raw
    `pydantic.ValidationError` escape -- malformed JSON and schema-invalid
    JSON are both treated as journal corruption, exactly like a payload that
    parses fine but hashes wrong (`DecisionJournalIntegrityError` either
    way, for the same reason: the journal's own data cannot be trusted)."""
    try:
        return model_cls.model_validate_json(payload)
    except ValidationError as exc:
        raise DecisionJournalIntegrityError(
            f"stored payload under fingerprint {fingerprint!r} in {table_name!r} could "
            f"not be reconstructed as {model_cls.__name__}: the stored payload may be "
            f"corrupted ({exc})"
        ) from exc


class SqliteDecisionJournal(DecisionJournal):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _write(self, table: object, fingerprint: str, payload: str, **indexed: object) -> str:
        stmt = sqlite_insert(table).values(fingerprint=fingerprint, payload=payload, **indexed)  # type: ignore[arg-type]
        stmt = stmt.on_conflict_do_nothing(index_elements=["fingerprint"])
        try:
            with self._engine.begin() as conn:
                conn.execute(stmt)
                stored = conn.execute(
                    select(table.c.payload).where(table.c.fingerprint == fingerprint)  # type: ignore[attr-defined]
                ).scalar_one()
        except SQLAlchemyError as exc:
            raise DecisionJournalError(
                f"could not write to {table.name!r}: {exc}"  # type: ignore[attr-defined]
            ) from exc
        if stored != payload:
            raise DecisionJournalIntegrityError(
                f"fingerprint {fingerprint!r} already exists in "
                f"{table.name!r} with different content; refusing to treat "  # type: ignore[attr-defined]
                "this as the same record"
            )
        return fingerprint

    def _read(self, table: object, fingerprint: str) -> str | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    select(table.c.payload).where(table.c.fingerprint == fingerprint)  # type: ignore[attr-defined]
                ).first()
        except SQLAlchemyError as exc:
            raise DecisionJournalError(
                f"could not read from {table.name!r}: {exc}"  # type: ignore[attr-defined]
            ) from exc
        return None if row is None else row.payload

    def record_evidence(self, evidence: Evidence, *, recorded_at: datetime) -> str:
        fingerprint = evidence_fingerprint(evidence)
        return self._write(
            evidence_table,
            fingerprint,
            evidence.model_dump_json(),
            symbol=evidence.symbol,
            timeframe=evidence.timeframe.value,
            as_of=evidence.as_of.isoformat(),
            recorded_at=recorded_at.isoformat(),
        )

    def record_reasoning(self, reasoning: ReasoningOutput, *, recorded_at: datetime) -> str:
        fingerprint = canonical_hash(reasoning)
        return self._write(
            reasoning_output_table,
            fingerprint,
            reasoning.model_dump_json(),
            evidence_fingerprint=reasoning.evidence_fingerprint,
            model=reasoning.model,
            action=reasoning.action.value,
            recorded_at=recorded_at.isoformat(),
        )

    def record_decision(self, decision: ProposedDecision, *, recorded_at: datetime) -> str:
        fingerprint = decision_fingerprint(decision)
        return self._write(
            proposed_decision_table,
            fingerprint,
            decision.model_dump_json(),
            analysis_id=decision.analysis_id,
            evidence_fingerprint=decision.evidence_ref.content_hash,
            reasoning_fingerprint=decision.reasoning_ref.content_hash,
            action=decision.action.value,
            decided_at=decision.decided_at.isoformat(),
            recorded_at=recorded_at.isoformat(),
        )

    def record_verdict(self, verdict: RiskVerdict, *, recorded_at: datetime) -> str:
        fingerprint = risk_verdict_fingerprint(verdict)
        return self._write(
            risk_verdict_table,
            fingerprint,
            verdict.model_dump_json(),
            analysis_id=verdict.analysis_id,
            decision_fingerprint=verdict.decision_ref.content_hash,
            symbol=verdict.portfolio_ref.symbol,
            outcome=verdict.outcome.value,
            action=verdict.action.value,
            reviewed_at=verdict.reviewed_at.isoformat(),
            recorded_at=recorded_at.isoformat(),
        )

    def record_fill(self, fill: Fill, *, recorded_at: datetime) -> str:
        fingerprint = canonical_hash(fill)
        return self._write(
            fill_table,
            fingerprint,
            fill.model_dump_json(),
            risk_verdict_fingerprint=fill.risk_verdict_fingerprint,
            symbol=fill.symbol,
            filled_at=fill.filled_at.isoformat(),
            recorded_at=recorded_at.isoformat(),
        )

    def record_closed_position(self, closed: ClosedPosition, *, recorded_at: datetime) -> str:
        fingerprint = canonical_hash(closed)
        return self._write(
            closed_position_table,
            fingerprint,
            closed.model_dump_json(),
            opened_by_verdict_fingerprint=closed.opened_by_verdict_fingerprint,
            symbol=closed.symbol,
            closed_at=closed.closed_at.isoformat(),
            recorded_at=recorded_at.isoformat(),
        )

    def get_evidence(self, fingerprint: str) -> Evidence | None:
        payload = self._read(evidence_table, fingerprint)
        if payload is None:
            return None
        evidence = _deserialize(
            Evidence, payload, fingerprint=fingerprint, table_name=evidence_table.name
        )
        if evidence_fingerprint(evidence) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored Evidence under fingerprint {fingerprint!r} no longer hashes "
                "to that fingerprint; the stored payload may be corrupted"
            )
        return evidence

    def get_reasoning(self, fingerprint: str) -> ReasoningOutput | None:
        payload = self._read(reasoning_output_table, fingerprint)
        if payload is None:
            return None
        reasoning = _deserialize(
            ReasoningOutput,
            payload,
            fingerprint=fingerprint,
            table_name=reasoning_output_table.name,
        )
        if canonical_hash(reasoning) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored ReasoningOutput under fingerprint {fingerprint!r} no longer "
                "hashes to that fingerprint; the stored payload may be corrupted"
            )
        return reasoning

    def get_decision(self, fingerprint: str) -> ProposedDecision | None:
        payload = self._read(proposed_decision_table, fingerprint)
        if payload is None:
            return None
        decision = _deserialize(
            ProposedDecision,
            payload,
            fingerprint=fingerprint,
            table_name=proposed_decision_table.name,
        )
        if decision_fingerprint(decision) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored ProposedDecision under fingerprint {fingerprint!r} no longer "
                "hashes to that fingerprint; the stored payload may be corrupted"
            )
        return decision

    def get_verdict(self, fingerprint: str) -> RiskVerdict | None:
        payload = self._read(risk_verdict_table, fingerprint)
        if payload is None:
            return None
        verdict = _deserialize(
            RiskVerdict, payload, fingerprint=fingerprint, table_name=risk_verdict_table.name
        )
        if risk_verdict_fingerprint(verdict) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored RiskVerdict under fingerprint {fingerprint!r} no longer hashes "
                "to that fingerprint; the stored payload may be corrupted"
            )
        return verdict

    def _execute_query(self, query: Select[Any]) -> list[Row[Any]]:
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(query).all())
        except SQLAlchemyError as exc:
            raise DecisionJournalError(f"could not query the journal: {exc}") from exc

    def list_decisions_by_analysis_id(self, analysis_id: str) -> tuple[ProposedDecision, ...]:
        rows = self._execute_query(
            select(proposed_decision_table.c.payload, proposed_decision_table.c.fingerprint)
            .where(proposed_decision_table.c.analysis_id == analysis_id)
            .order_by(proposed_decision_table.c.recorded_at.asc())
        )
        decisions = []
        for row in rows:
            decision = _deserialize(
                ProposedDecision,
                row.payload,
                fingerprint=row.fingerprint,
                table_name=proposed_decision_table.name,
            )
            if decision_fingerprint(decision) != row.fingerprint:
                raise DecisionJournalIntegrityError(
                    f"stored ProposedDecision under fingerprint {row.fingerprint!r} no "
                    "longer hashes to that fingerprint; the stored payload may be corrupted"
                )
            decisions.append(decision)
        return tuple(decisions)

    def list_verdicts_by_analysis_id(self, analysis_id: str) -> tuple[RiskVerdict, ...]:
        rows = self._execute_query(
            select(risk_verdict_table.c.payload, risk_verdict_table.c.fingerprint)
            .where(risk_verdict_table.c.analysis_id == analysis_id)
            .order_by(risk_verdict_table.c.recorded_at.asc())
        )
        return self._verdicts_from_rows(rows)

    def list_verdicts_by_symbol(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[RiskVerdict, ...]:
        query = select(risk_verdict_table.c.payload, risk_verdict_table.c.fingerprint).where(
            risk_verdict_table.c.symbol == symbol
        )
        if start is not None:
            query = query.where(risk_verdict_table.c.reviewed_at >= start.isoformat())
        if end is not None:
            query = query.where(risk_verdict_table.c.reviewed_at <= end.isoformat())
        query = query.order_by(risk_verdict_table.c.reviewed_at.asc())
        if limit is not None:
            query = query.limit(limit)
        rows = self._execute_query(query)
        return self._verdicts_from_rows(rows)

    def _verdicts_from_rows(self, rows: list[Row[Any]]) -> tuple[RiskVerdict, ...]:
        verdicts = []
        for row in rows:
            verdict = _deserialize(
                RiskVerdict,
                row.payload,
                fingerprint=row.fingerprint,
                table_name=risk_verdict_table.name,
            )
            if risk_verdict_fingerprint(verdict) != row.fingerprint:
                raise DecisionJournalIntegrityError(
                    f"stored RiskVerdict under fingerprint {row.fingerprint!r} no longer "
                    "hashes to that fingerprint; the stored payload may be corrupted"
                )
            verdicts.append(verdict)
        return tuple(verdicts)
