"""The durable implementation of `orion.paper.store.PortfolioStore`, backed
by SQLite via `orion.memory.schema.portfolio_state_revision_table`.

Implements the *existing* `PortfolioStore` interface exactly — `orion.paper`
and `orion.risk` do not change at all. See that module's docstring for the
full contract this class must honor (monotonic-revision compare-and-swap,
the ABA problem a pure content fingerprint has, why `load`/`save`/
`initialize` all return a `PortfolioRecord` atomically).

Storage strategy: append-only INSERT, never UPDATE, one permanent row per
revision (`orion.memory.schema`'s module docstring explains why this is a
deliberate improvement over the `UPDATE ... WHERE revision = ?` sketch in
`orion.paper.store`'s own docstring). `revision` is the table's primary
key, so the actual safety net against a genuine race between two concurrent
writers who both read the same "current" revision is the database's own
UNIQUE constraint on that column, not the pre-check below — the pre-check
(`current != expected_revision`) only makes the *common*, non-racing case
fail fast with a clear message instead of relying on the constraint for
everything. `PRAGMA busy_timeout` (set in `orion.memory.engine.
create_memory_engine`) gives a genuinely concurrent second writer time to
wait for the first writer's transaction to commit rather than failing on a
transient lock; the *outcome* is identical either way once both writers
resolve — exactly one succeeds, the other gets `PortfolioStoreConcurrencyError`.

`recorded_at`/"when was this written" is deliberately not tracked as a
separate column here: `PortfolioStore.save()`/`initialize()`'s existing
signature (which this class must not change) takes no such parameter, and
`PortfolioState.as_of` — already required, already caller-supplied — serves
the equivalent purpose for this table.

`load()` never lets a raw `pydantic.ValidationError` escape `PortfolioState.
model_validate_json`: a payload that is not valid JSON, or is valid JSON
that fails `PortfolioState`'s own schema, is wrapped in `PortfolioStoreError`
exactly like any other reason this store cannot produce a real
`PortfolioRecord` — the same "I could not persist" fail-closed contract the
class docstring already promises, closing a gap a fresh adversarial pass
found empirically (a genuinely unparseable payload previously raised an
unwrapped `pydantic_core.ValidationError` instead).
"""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from orion.memory.schema import portfolio_state_revision_table
from orion.paper.store import (
    PortfolioRecord,
    PortfolioStore,
    PortfolioStoreConcurrencyError,
    PortfolioStoreError,
)
from orion.risk.portfolio import PortfolioState


class SqlitePortfolioStore(PortfolioStore):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self) -> PortfolioRecord:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    select(
                        portfolio_state_revision_table.c.revision,
                        portfolio_state_revision_table.c.payload,
                    ).order_by(portfolio_state_revision_table.c.revision.desc())
                ).first()
        except SQLAlchemyError as exc:
            raise PortfolioStoreError(f"could not load PortfolioState: {exc}") from exc
        if row is None:
            raise PortfolioStoreError(
                "no PortfolioState has been initialized; call initialize() first"
            )
        try:
            state = PortfolioState.model_validate_json(row.payload)
        except ValidationError as exc:
            raise PortfolioStoreError(
                f"stored PortfolioState at revision {row.revision} could not be "
                f"reconstructed: the stored payload may be corrupted ({exc})"
            ) from exc
        return PortfolioRecord(state=state, revision=row.revision)

    def save(self, state: PortfolioState, *, expected_revision: int) -> PortfolioRecord:
        if not isinstance(state, PortfolioState):
            raise PortfolioStoreError(
                f"state must be a PortfolioState instance, got {type(state)!r}"
            )
        payload = state.model_dump_json()
        try:
            with self._engine.begin() as conn:
                current = conn.execute(
                    select(func.max(portfolio_state_revision_table.c.revision))
                ).scalar()
                if current is None:
                    raise PortfolioStoreConcurrencyError(
                        "no PortfolioState has been initialized; call initialize() "
                        "first, not save()"
                    )
                if current != expected_revision:
                    raise PortfolioStoreConcurrencyError(
                        f"expected_revision ({expected_revision}) does not match the "
                        f"store's actual current revision ({current}); this write is "
                        "based on a stale snapshot and was not applied"
                    )
                new_revision = current + 1
                try:
                    conn.execute(
                        portfolio_state_revision_table.insert().values(
                            revision=new_revision,
                            symbol=state.symbol,
                            as_of=state.as_of.isoformat(),
                            payload=payload,
                        )
                    )
                except IntegrityError as exc:
                    raise PortfolioStoreConcurrencyError(
                        f"revision {new_revision} was already written by a concurrent "
                        "writer between this write's read and insert; this write was "
                        "based on a stale snapshot and was not applied"
                    ) from exc
        except SQLAlchemyError as exc:
            raise PortfolioStoreError(f"could not save PortfolioState: {exc}") from exc
        return PortfolioRecord(state=state, revision=new_revision)

    def initialize(self, state: PortfolioState) -> PortfolioRecord:
        if not isinstance(state, PortfolioState):
            raise PortfolioStoreError(
                f"state must be a PortfolioState instance, got {type(state)!r}"
            )
        payload = state.model_dump_json()
        try:
            with self._engine.begin() as conn:
                current = conn.execute(
                    select(func.max(portfolio_state_revision_table.c.revision))
                ).scalar()
                if current is not None:
                    raise PortfolioStoreError(
                        "a PortfolioState already exists; use save() to update it, not initialize()"
                    )
                try:
                    conn.execute(
                        portfolio_state_revision_table.insert().values(
                            revision=0,
                            symbol=state.symbol,
                            as_of=state.as_of.isoformat(),
                            payload=payload,
                        )
                    )
                except IntegrityError as exc:
                    raise PortfolioStoreError(
                        "a PortfolioState already exists; use save() to update it, not initialize()"
                    ) from exc
        except SQLAlchemyError as exc:
            raise PortfolioStoreError(f"could not initialize PortfolioState: {exc}") from exc
        return PortfolioRecord(state=state, revision=0)
