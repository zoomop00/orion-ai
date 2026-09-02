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
unguarded: `_deserialize` below wraps every call site (every `get_*` and,
via `_rows_to_models`, every `list_*`) so a payload that is not even valid
JSON, or is valid JSON that fails the model's own schema — not just "parses
fine but hashes wrong" — also raises `DecisionJournalIntegrityError`, never
a raw `pydantic.ValidationError`. A fresh adversarial pass after this
module's first implementation found that gap empirically (a payload of
literal garbage bytes raised an unwrapped `pydantic_core.ValidationError`,
violating this class's own documented "I could not read the real journal,
fail closed through my own error type" contract) — this is the fix, not a
hypothetical concern.

`Fill`/`ClosedPosition` reads (`get_fill`/`get_closed_position`/
`list_fills_by_*`/`list_closed_positions_by_*`) use `orion.provenance.
canonical_hash` directly for their integrity check, exactly like
`record_fill`/`record_closed_position` already did on write — neither type
has a domain-named fingerprint wrapper (unlike `evidence_fingerprint`/
`decision_fingerprint`/`risk_verdict_fingerprint`), so there is nothing to
import beyond the one shared hashing mechanism this whole package already
depends on. See `docs/DECISIONS.md` for the writer-trust-boundary note this
read path is subject to: these methods verify a `Fill`/`ClosedPosition` was
stored unmodified and correctly references a `RiskVerdict` fingerprint —
they cannot prove the numbers were actually computed by `orion.paper.
execution.execute`/`resolve_stop_take_profit`, only that whatever was
recorded has not been altered since.
"""

from __future__ import annotations

from collections.abc import Callable
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
    market_thesis_table,
    proposed_decision_table,
    reasoning_output_table,
    risk_verdict_table,
)
from orion.paper.schemas import ClosedPosition, Fill
from orion.provenance import canonical_hash
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.schemas import RiskVerdict, risk_verdict_fingerprint
from orion.thesis.schemas import MarketThesis, market_thesis_fingerprint


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

    def record_thesis(self, thesis: MarketThesis, *, recorded_at: datetime) -> str:
        fingerprint = market_thesis_fingerprint(thesis)
        return self._write(
            market_thesis_table,
            fingerprint,
            thesis.model_dump_json(),
            analysis_id=thesis.analysis_id,
            symbol=thesis.symbol,
            formed_at=thesis.formed_at.isoformat(),
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

    def get_thesis(self, fingerprint: str) -> MarketThesis | None:
        payload = self._read(market_thesis_table, fingerprint)
        if payload is None:
            return None
        thesis = _deserialize(
            MarketThesis, payload, fingerprint=fingerprint, table_name=market_thesis_table.name
        )
        if market_thesis_fingerprint(thesis) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored MarketThesis under fingerprint {fingerprint!r} no longer hashes "
                "to that fingerprint; the stored payload may be corrupted"
            )
        return thesis

    def get_fill(self, fingerprint: str) -> Fill | None:
        payload = self._read(fill_table, fingerprint)
        if payload is None:
            return None
        fill = _deserialize(Fill, payload, fingerprint=fingerprint, table_name=fill_table.name)
        if canonical_hash(fill) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored Fill under fingerprint {fingerprint!r} no longer hashes to that "
                "fingerprint; the stored payload may be corrupted"
            )
        return fill

    def get_closed_position(self, fingerprint: str) -> ClosedPosition | None:
        payload = self._read(closed_position_table, fingerprint)
        if payload is None:
            return None
        closed = _deserialize(
            ClosedPosition, payload, fingerprint=fingerprint, table_name=closed_position_table.name
        )
        if canonical_hash(closed) != fingerprint:
            raise DecisionJournalIntegrityError(
                f"stored ClosedPosition under fingerprint {fingerprint!r} no longer hashes "
                "to that fingerprint; the stored payload may be corrupted"
            )
        return closed

    def _execute_query(self, query: Select[Any]) -> list[Row[Any]]:
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(query).all())
        except SQLAlchemyError as exc:
            raise DecisionJournalError(f"could not query the journal: {exc}") from exc

    def _rows_to_models[ModelT: BaseModel](
        self,
        model_cls: type[ModelT],
        rows: list[Row[Any]],
        *,
        fingerprint_fn: Callable[[ModelT], str],
        table_name: str,
    ) -> tuple[ModelT, ...]:
        """Reconstruct and integrity-check every row in `rows`, in the order
        the query already sorted them (SQL `ORDER BY`, never re-sorted here)
        -- the shared tail of every `list_*` method, so each one only needs
        to state its own filter/ordering, not repeat this loop."""
        models = []
        for row in rows:
            model = _deserialize(
                model_cls, row.payload, fingerprint=row.fingerprint, table_name=table_name
            )
            if fingerprint_fn(model) != row.fingerprint:
                raise DecisionJournalIntegrityError(
                    f"stored {model_cls.__name__} under fingerprint {row.fingerprint!r} no "
                    "longer hashes to that fingerprint; the stored payload may be corrupted"
                )
            models.append(model)
        return tuple(models)

    def list_decisions_by_analysis_id(self, analysis_id: str) -> tuple[ProposedDecision, ...]:
        rows = self._execute_query(
            select(proposed_decision_table.c.payload, proposed_decision_table.c.fingerprint)
            .where(proposed_decision_table.c.analysis_id == analysis_id)
            .order_by(proposed_decision_table.c.recorded_at.asc())
        )
        return self._rows_to_models(
            ProposedDecision,
            rows,
            fingerprint_fn=decision_fingerprint,
            table_name=proposed_decision_table.name,
        )

    def list_verdicts_by_analysis_id(self, analysis_id: str) -> tuple[RiskVerdict, ...]:
        rows = self._execute_query(
            select(risk_verdict_table.c.payload, risk_verdict_table.c.fingerprint)
            .where(risk_verdict_table.c.analysis_id == analysis_id)
            .order_by(risk_verdict_table.c.recorded_at.asc())
        )
        return self._rows_to_models(
            RiskVerdict,
            rows,
            fingerprint_fn=risk_verdict_fingerprint,
            table_name=risk_verdict_table.name,
        )

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
        return self._rows_to_models(
            RiskVerdict,
            rows,
            fingerprint_fn=risk_verdict_fingerprint,
            table_name=risk_verdict_table.name,
        )

    def list_theses_by_analysis_id(self, analysis_id: str) -> tuple[MarketThesis, ...]:
        rows = self._execute_query(
            select(market_thesis_table.c.payload, market_thesis_table.c.fingerprint)
            .where(market_thesis_table.c.analysis_id == analysis_id)
            .order_by(market_thesis_table.c.recorded_at.asc())
        )
        return self._rows_to_models(
            MarketThesis,
            rows,
            fingerprint_fn=market_thesis_fingerprint,
            table_name=market_thesis_table.name,
        )

    def list_fills_by_verdict_fingerprint(self, risk_verdict_fingerprint: str) -> tuple[Fill, ...]:
        rows = self._execute_query(
            select(fill_table.c.payload, fill_table.c.fingerprint)
            .where(fill_table.c.risk_verdict_fingerprint == risk_verdict_fingerprint)
            .order_by(fill_table.c.recorded_at.asc())
        )
        return self._rows_to_models(
            Fill, rows, fingerprint_fn=canonical_hash, table_name=fill_table.name
        )

    def list_fills_by_symbol(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[Fill, ...]:
        query = select(fill_table.c.payload, fill_table.c.fingerprint).where(
            fill_table.c.symbol == symbol
        )
        if start is not None:
            query = query.where(fill_table.c.filled_at >= start.isoformat())
        if end is not None:
            query = query.where(fill_table.c.filled_at <= end.isoformat())
        query = query.order_by(fill_table.c.filled_at.asc())
        if limit is not None:
            query = query.limit(limit)
        rows = self._execute_query(query)
        return self._rows_to_models(
            Fill, rows, fingerprint_fn=canonical_hash, table_name=fill_table.name
        )

    def list_closed_positions_by_verdict_fingerprint(
        self, opened_by_verdict_fingerprint: str
    ) -> tuple[ClosedPosition, ...]:
        rows = self._execute_query(
            select(closed_position_table.c.payload, closed_position_table.c.fingerprint)
            .where(
                closed_position_table.c.opened_by_verdict_fingerprint
                == opened_by_verdict_fingerprint
            )
            .order_by(closed_position_table.c.recorded_at.asc())
        )
        return self._rows_to_models(
            ClosedPosition,
            rows,
            fingerprint_fn=canonical_hash,
            table_name=closed_position_table.name,
        )

    def list_closed_positions_by_symbol(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[ClosedPosition, ...]:
        query = select(closed_position_table.c.payload, closed_position_table.c.fingerprint).where(
            closed_position_table.c.symbol == symbol
        )
        if start is not None:
            query = query.where(closed_position_table.c.closed_at >= start.isoformat())
        if end is not None:
            query = query.where(closed_position_table.c.closed_at <= end.isoformat())
        query = query.order_by(closed_position_table.c.closed_at.asc())
        if limit is not None:
            query = query.limit(limit)
        rows = self._execute_query(query)
        return self._rows_to_models(
            ClosedPosition,
            rows,
            fingerprint_fn=canonical_hash,
            table_name=closed_position_table.name,
        )
