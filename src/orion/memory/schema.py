"""SQLAlchemy Core table definitions for the Decision Memory journal and the
durable `PortfolioStore` backing.

Core, not the ORM: every row's `payload` column is the exact canonical JSON
of an existing Pydantic domain model (`Evidence`, `ReasoningOutput`,
`ProposedDecision`, `RiskVerdict`, `Fill`, `ClosedPosition`,
`PortfolioState`) — the domain model is already the single source of truth
for that shape, so mapping it a second time onto an ORM class would create
two parallel schemas that can silently drift apart. A handful of columns
are denormalized out of the payload purely for indexing/filtering (fingerprint,
analysis_id, symbol, the record's own domain timestamp); the payload column
is what every `get_*`/reconstruction path actually reads.

Every journal table's primary key is the record's own content fingerprint
(`evidence_fingerprint`/`canonical_hash`/`decision_fingerprint`/
`risk_verdict_fingerprint` — the exact functions already used everywhere
else in ORION for provenance). This is deliberate, not incidental: a
content-addressed primary key makes a duplicate write of byte-identical
content a no-op at the database layer, which is simultaneously ORION's
provenance-linkage mechanism (D/E) and its idempotency/replay-protection
mechanism (I/J) — one mechanism answering both questions, per `docs/
DECISIONS.md`'s approved Mission 2 architecture.

`portfolio_state_revision` is intentionally append-only (never UPDATEd or
DELETEd), one permanent row per revision the store has ever issued —
`revision` is the table's primary key, so a second writer racing to insert
the same revision number fails on the UNIQUE constraint at the database
level, which is what makes `orion.memory.portfolio_store.
SqlitePortfolioStore`'s compare-and-swap safe under concurrent writers
without any explicit row locking. This supersedes (for the durable
implementation only) the `UPDATE ... WHERE revision = ?` sketch in `orion.
paper.store`'s own module docstring — see the Mission 2 architecture entry
in `docs/DECISIONS.md` for why an append-only insert was chosen instead:
it keeps the full equity/position history permanently queryable rather than
overwriting it, at no extra cost. `orion.paper.store.PortfolioStore`'s
public interface (`load`/`save`/`initialize` returning `PortfolioRecord`)
is completely unchanged by this — only what backs it durably.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table

metadata = MetaData()

evidence_table = Table(
    "evidence",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("symbol", String, nullable=False),
    Column("timeframe", String, nullable=False),
    Column("as_of", String, nullable=False, index=True),
    Column("recorded_at", String, nullable=False),
    Column("payload", String, nullable=False),
)

reasoning_output_table = Table(
    "reasoning_output",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("evidence_fingerprint", String(64), nullable=False, index=True),
    Column("model", String, nullable=False),
    Column("action", String, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("payload", String, nullable=False),
)

proposed_decision_table = Table(
    "proposed_decision",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("analysis_id", String, nullable=False, index=True),
    Column("evidence_fingerprint", String(64), nullable=False, index=True),
    Column("reasoning_fingerprint", String(64), nullable=False, index=True),
    Column("action", String, nullable=False),
    Column("decided_at", String, nullable=False, index=True),
    Column("recorded_at", String, nullable=False),
    Column("payload", String, nullable=False),
)

risk_verdict_table = Table(
    "risk_verdict",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("analysis_id", String, nullable=False, index=True),
    Column("decision_fingerprint", String(64), nullable=False, index=True),
    Column("symbol", String, nullable=False, index=True),
    Column("outcome", String, nullable=False, index=True),
    Column("action", String, nullable=False),
    Column("reviewed_at", String, nullable=False, index=True),
    Column("recorded_at", String, nullable=False),
    Column("payload", String, nullable=False),
)

fill_table = Table(
    "fill",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("risk_verdict_fingerprint", String(64), nullable=False, index=True),
    Column("symbol", String, nullable=False),
    Column("filled_at", String, nullable=False, index=True),
    Column("recorded_at", String, nullable=False),
    Column("payload", String, nullable=False),
)

closed_position_table = Table(
    "closed_position",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("opened_by_verdict_fingerprint", String(64), nullable=False, index=True),
    Column("symbol", String, nullable=False),
    Column("closed_at", String, nullable=False, index=True),
    Column("recorded_at", String, nullable=False),
    Column("payload", String, nullable=False),
)

portfolio_state_revision_table = Table(
    "portfolio_state_revision",
    metadata,
    Column("revision", Integer, primary_key=True),
    Column("symbol", String, nullable=False, index=True),
    Column("as_of", String, nullable=False),
    Column("payload", String, nullable=False),
)
