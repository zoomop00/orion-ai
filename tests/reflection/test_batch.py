"""`reflect_on_recent_closed_positions`: the read surface `orion reflect` uses.

THE TWO GUARANTEES THIS FILE EXISTS FOR. A closed trade whose authorising chain
is not in the journal must be *reported*, never dropped and never allowed to
hide the trades around it — that is the difference between "ORION has two
reflections" and "ORION has ten trades and can explain two of them". And a
corrupted record must still fail closed: incompleteness is collected,
`DecisionJournalIntegrityError` is not.

Every trade here is produced by `open_close_and_journal`, which runs the real
`execute`/`resolve_stop_take_profit` against a real approved verdict and writes
it to a real `:memory:` SQLite journal. Nothing is hand-assembled.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.memory.journal import DecisionJournalIntegrityError
from orion.memory.schema import closed_position_table
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.provenance import canonical_hash
from orion.reflection.analysis import ReflectionError, reflect_on_recent_closed_positions
from orion.reflection.schemas import ReflectionBatch, TradeReflection
from tests.reflection.conftest import BASE_TIME, open_close_and_journal

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
SYMBOL = "XAUUSD"


def _journal_trades(journal: SqliteDecisionJournal, count: int, **kwargs: object) -> list[str]:
    """`count` real trades, each closed an hour after the last, oldest first."""
    fingerprints = []
    for index in range(count):
        trade = open_close_and_journal(
            journal,
            opened_at=BASE_TIME + timedelta(hours=index * 2),
            closed_at=BASE_TIME + timedelta(hours=index * 2 + 1),
            **kwargs,  # type: ignore[arg-type]
        )
        fingerprints.append(trade.closed_position_fingerprint)
    return fingerprints


# ----------------------------------------------------------- the happy path


def test_an_empty_journal_is_an_answer_not_an_error(journal: SqliteDecisionJournal) -> None:
    """Nothing closed yet is an ordinary state, reported as an empty batch."""
    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    assert isinstance(batch, ReflectionBatch)
    assert batch.reflections == ()
    assert batch.unreflectable_fingerprints == ()
    assert batch.total_closed_positions == 0
    assert batch.symbol == SYMBOL


def test_every_complete_trade_is_reflected_on(journal: SqliteDecisionJournal) -> None:
    fingerprints = _journal_trades(journal, 3)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    assert [r.closed_position_fingerprint for r in batch.reflections] == fingerprints
    assert batch.total_closed_positions == 3
    assert batch.unreflectable_fingerprints == ()
    assert all(isinstance(r, TradeReflection) for r in batch.reflections)


def test_the_batch_stamps_one_instant_on_everything(journal: SqliteDecisionJournal) -> None:
    """`now` is caller-supplied and never re-read per trade, so a batch is a
    single point in time rather than a slow scan with drifting timestamps."""
    _journal_trades(journal, 2)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    assert batch.reflected_at == NOW
    assert [r.reflected_at for r in batch.reflections] == [NOW, NOW]


def test_a_different_symbol_sees_none_of_it(journal: SqliteDecisionJournal) -> None:
    _journal_trades(journal, 2)

    batch = reflect_on_recent_closed_positions(journal, "EURUSD", limit=10, now=NOW)

    assert batch.reflections == ()
    assert batch.total_closed_positions == 0


# ------------------------------------------------------------- the tail slice


def test_the_limit_returns_the_most_recent_trades_not_the_oldest(
    journal: SqliteDecisionJournal,
) -> None:
    """THE BUG THIS TEST EXISTS TO PREVENT. `list_closed_positions_by_symbol`
    applies `limit` to a `closed_at` *ascending* order, so handing the limit
    straight to the query would return the three oldest trades under the name
    "most recent". The tail slice is what makes the word honest."""
    fingerprints = _journal_trades(journal, 5)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=2, now=NOW)

    assert [r.closed_position_fingerprint for r in batch.reflections] == fingerprints[-2:]
    # And the count is of everything, so a short answer is distinguishable
    # from an exhausted one.
    assert batch.total_closed_positions == 5


def test_a_limit_larger_than_the_history_returns_all_of_it(
    journal: SqliteDecisionJournal,
) -> None:
    fingerprints = _journal_trades(journal, 2)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=500, now=NOW)

    assert [r.closed_position_fingerprint for r in batch.reflections] == fingerprints


def test_a_limit_of_one_returns_the_latest_trade(journal: SqliteDecisionJournal) -> None:
    fingerprints = _journal_trades(journal, 4)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=1, now=NOW)

    assert [r.closed_position_fingerprint for r in batch.reflections] == [fingerprints[-1]]


# -------------------------------------------------- the incomplete chain


def test_a_trade_with_no_journaled_verdict_is_listed_not_dropped(
    journal: SqliteDecisionJournal,
) -> None:
    """Every position opened before the forward cycle began journaling its
    chain is permanently in this state. Reporting it as absent would make the
    journal look smaller than it is."""
    orphan = open_close_and_journal(journal, journal_verdict=False)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    assert batch.reflections == ()
    assert batch.unreflectable_fingerprints == (orphan.closed_position_fingerprint,)
    assert batch.total_closed_positions == 1


def test_a_trade_with_no_journaled_decision_is_listed_too(
    journal: SqliteDecisionJournal,
) -> None:
    orphan = open_close_and_journal(journal, journal_decision=False)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    assert batch.unreflectable_fingerprints == (orphan.closed_position_fingerprint,)


def test_one_broken_chain_does_not_hide_the_working_ones(
    journal: SqliteDecisionJournal,
) -> None:
    """The whole reason this returns a batch rather than raising: a single
    legacy trade must not make every later reflection unreachable."""
    good_first = open_close_and_journal(journal, opened_at=BASE_TIME)
    orphan = open_close_and_journal(
        journal,
        opened_at=BASE_TIME + timedelta(hours=2),
        closed_at=BASE_TIME + timedelta(hours=3),
        # Distinct price levels, so this trade's verdict is genuinely a
        # different record. The journal is content-addressed: an identical
        # verdict already written by another trade would legitimately satisfy
        # this one's reference, and the chain would not be broken at all.
        entry_price=1900.0,
        stop_loss=1850.0,
        take_profit=2000.0,
        journal_verdict=False,
    )
    good_last = open_close_and_journal(
        journal,
        opened_at=BASE_TIME + timedelta(hours=4),
        closed_at=BASE_TIME + timedelta(hours=5),
    )

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    assert [r.closed_position_fingerprint for r in batch.reflections] == [
        good_first.closed_position_fingerprint,
        good_last.closed_position_fingerprint,
    ]
    assert batch.unreflectable_fingerprints == (orphan.closed_position_fingerprint,)
    assert batch.total_closed_positions == 3


def test_every_examined_trade_lands_in_exactly_one_tuple(
    journal: SqliteDecisionJournal,
) -> None:
    """Nothing is silently discarded on the way through."""
    open_close_and_journal(journal, opened_at=BASE_TIME)
    open_close_and_journal(
        journal,
        opened_at=BASE_TIME + timedelta(hours=2),
        closed_at=BASE_TIME + timedelta(hours=3),
        entry_price=1900.0,
        stop_loss=1850.0,
        take_profit=2000.0,
        journal_decision=False,
    )

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    examined = len(batch.reflections) + len(batch.unreflectable_fingerprints)
    assert examined == batch.total_closed_positions == 2


def test_a_listed_fingerprint_really_is_the_journals_own_key(
    journal: SqliteDecisionJournal,
) -> None:
    """The fingerprint reported for an unreflectable trade must be usable to
    go and fetch the record — otherwise naming it is no better than counting
    it. Recomputed with the journal's own hashing function, not a new one."""
    orphan = open_close_and_journal(journal, journal_verdict=False)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)
    fingerprint = batch.unreflectable_fingerprints[0]

    fetched = journal.get_closed_position(fingerprint)
    assert fetched == orphan.closed_position
    assert fingerprint == canonical_hash(orphan.closed_position)


# --------------------------------------------------------------- fail closed


def test_a_corrupted_record_is_never_collected_as_incompleteness(
    engine, journal: SqliteDecisionJournal
) -> None:
    """CORRUPTION IS NOT AN INCOMPLETE CHAIN. `reflect_on_closed_position` lets
    `DecisionJournalIntegrityError` propagate, and so must the batch: a journal
    whose contents cannot be verified must never be presented as a journal with
    a couple of unexplained trades in it."""
    trade = open_close_and_journal(journal)
    with engine.begin() as connection:
        connection.execute(
            closed_position_table.update()
            .where(closed_position_table.c.fingerprint == trade.closed_position_fingerprint)
            .values(payload='{"not": "a closed position"}')
        )

    with pytest.raises(DecisionJournalIntegrityError):
        reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)


def test_a_naive_now_is_refused_before_anything_is_read(
    journal: SqliteDecisionJournal,
) -> None:
    """Checked first, so a bad call reads nothing rather than reporting every
    trade in the journal as unreflectable."""
    _journal_trades(journal, 2)

    with pytest.raises(ReflectionError, match="timezone-aware"):
        reflect_on_recent_closed_positions(
            journal, SYMBOL, limit=10, now=datetime(2026, 8, 30, 12, 0)
        )


@pytest.mark.parametrize("limit", [0, -1, -100])
def test_a_non_positive_limit_is_refused(journal: SqliteDecisionJournal, limit: int) -> None:
    """`[-0:]` is the whole list, so a limit of zero would silently return
    everything rather than nothing. Refused instead of interpreted."""
    with pytest.raises(ReflectionError, match="at least 1"):
        reflect_on_recent_closed_positions(journal, SYMBOL, limit=limit, now=NOW)


# ------------------------------------------------------ still only the facts


def test_the_batch_carries_no_score_verdict_or_aggregate(
    journal: SqliteDecisionJournal,
) -> None:
    """Mission 5's load-bearing constraint, restated one layer up.

    A batch is where an aggregate would be tempting — a win rate, a total P&L,
    a "quality" average — and where it would do the most damage, because a
    number over many trades reads as evidence for changing strategy. The batch
    carries counts and facts only; the judgment stays with the reader.
    """
    _journal_trades(journal, 3)

    batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)

    fields = set(type(batch).model_fields)
    banned = {
        "score",
        "quality",
        "grade",
        "rating",
        "win_rate",
        "wins",
        "losses",
        "total_pnl",
        "net_pnl",
        "average_pnl",
        "expectancy",
        "recommendation",
        "adjustment",
        "verdict",
        "good",
        "bad",
    }
    assert not (fields & banned), f"ReflectionBatch must judge nothing: {fields & banned}"
