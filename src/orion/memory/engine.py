"""Engine construction for the Decision Memory SQLite backend.

The one place `orion.memory` turns a plain path (or `:memory:`) into a
configured SQLAlchemy `Engine` — every table-creating/querying module below
gets its engine from here, never constructs one itself, so connection
settings (WAL mode, busy timeout) are never accidentally applied
inconsistently across call sites.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

from orion.memory.schema import metadata


def _is_in_memory(db_path: str) -> bool:
    return db_path == ":memory:" or db_path.startswith("file::memory:")


def create_memory_engine(db_path: str) -> Engine:
    """Build and return a ready-to-use `Engine` for `db_path`, with all
    ORION Decision Memory tables already created.

    `:memory:` gets `StaticPool` + `check_same_thread=False`: SQLAlchemy's
    default pool opens a fresh connection per checkout, and a `:memory:`
    SQLite database is private to the connection that created it — without
    `StaticPool` forcing every checkout onto the *same* connection, tables
    created by one checkout would be invisible to the next, and data would
    appear to vanish between calls. A real file path needs no such pinning;
    each connection opens the same on-disk database.

    Every connection gets `PRAGMA journal_mode=WAL` (concurrent readers
    alongside a single writer — the standard recommendation for any
    non-trivial SQLite usage; a no-op, not an error, on `:memory:`, which
    SQLite always keeps in its own in-memory journal mode regardless) and
    `PRAGMA busy_timeout=5000` (a writer that finds another writer's
    transaction in flight waits up to 5s for it to finish rather than
    failing immediately with "database is locked" — genuine staleness is
    still rejected afterward via the revision/fingerprint check, this only
    avoids a spurious low-level lock error masking that real, meaningful
    rejection under real concurrent access).
    """
    connect_args: dict[str, object] = {}
    pool_kwargs: dict[str, object] = {}
    if _is_in_memory(db_path):
        connect_args["check_same_thread"] = False
        pool_kwargs["poolclass"] = StaticPool

    engine = create_engine(
        f"sqlite:///{db_path}", future=True, connect_args=connect_args, **pool_kwargs
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    return engine
