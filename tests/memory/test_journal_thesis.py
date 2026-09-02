"""The seventh journal record: a `MarketThesis`, stored like every other.

WHAT THIS SUITE PROTECTS. Mission 8 v2.1 refused to persist a thesis and named
two blockers; v2.2 resolves both and adds the record. The guarantees that make
that safe are the ones the other six already have — content-addressed identity,
idempotent replay, integrity checked on read — plus one that is specific to
this record: `analysis_id` is a *sibling* key, deliberately not unique, so the
list method returns a tuple and a caller that needs exactly one has to say what
it does about more.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from orion.memory.journal import DecisionJournalIntegrityError
from orion.memory.schema import market_thesis_table
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.thesis.schemas import MarketThesis, StructureBias, market_thesis_fingerprint

from ..thesis.builders import ANALYSIS_ID, THESIS_TIME, build_thesis

RECORDED_AT = datetime(2026, 8, 31, 13, 0, tzinfo=UTC)


class TestItRoundTrips:
    def test_a_thesis_is_stored_and_read_back_unchanged(
        self, journal: SqliteDecisionJournal
    ) -> None:
        thesis = build_thesis()

        fingerprint = journal.record_thesis(thesis, recorded_at=RECORDED_AT)

        assert journal.get_thesis(fingerprint) == thesis

    def test_the_full_macro_half_survives_the_round_trip(
        self, journal: SqliteDecisionJournal
    ) -> None:
        """Provenance is the reason to store this at all: the model, prompt
        version, input fingerprint and the analyst's own rationale must come
        back exactly as they went in."""
        thesis = build_thesis(with_macro=True)

        stored = journal.get_thesis(journal.record_thesis(thesis, recorded_at=RECORDED_AT))

        assert stored is not None
        assert stored.macro_provenance is not None
        assert stored.macro_provenance == thesis.macro_provenance
        assert stored.macro_policy_pressure is thesis.macro_policy_pressure
        assert stored.agreements == thesis.agreements

    def test_an_unknown_fingerprint_reads_as_absent(self, journal: SqliteDecisionJournal) -> None:
        assert journal.get_thesis("f" * 64) is None

    def test_the_fingerprint_is_the_shared_hashing_mechanism(
        self, journal: SqliteDecisionJournal
    ) -> None:
        """Never a second implementation: the key the journal stores under is
        what `market_thesis_fingerprint` computes, which is
        `orion.provenance.canonical_hash`."""
        thesis = build_thesis()

        fingerprint = journal.record_thesis(thesis, recorded_at=RECORDED_AT)

        assert fingerprint == market_thesis_fingerprint(thesis)


class TestReplayIsSafe:
    def test_recording_the_same_thesis_twice_is_a_no_op(
        self, engine: Engine, journal: SqliteDecisionJournal
    ) -> None:
        """The crash window in `orion.paper.session._persist` is only
        recoverable if replay creates no duplicate."""
        thesis = build_thesis()

        first = journal.record_thesis(thesis, recorded_at=RECORDED_AT)
        second = journal.record_thesis(thesis, recorded_at=RECORDED_AT + timedelta(hours=1))

        assert first == second
        with engine.begin() as connection:
            rows = list(connection.execute(market_thesis_table.select()))
        assert len(rows) == 1

    def test_a_different_thesis_gets_its_own_row(
        self, engine: Engine, journal: SqliteDecisionJournal
    ) -> None:
        journal.record_thesis(build_thesis(), recorded_at=RECORDED_AT)
        journal.record_thesis(build_thesis(bias=StructureBias.BEARISH), recorded_at=RECORDED_AT)

        with engine.begin() as connection:
            rows = list(connection.execute(market_thesis_table.select()))
        assert len(rows) == 2

    def test_a_corrupted_payload_fails_closed_on_read(
        self, engine: Engine, journal: SqliteDecisionJournal
    ) -> None:
        """A journal whose contents do not verify is never presented as data."""
        fingerprint = journal.record_thesis(build_thesis(), recorded_at=RECORDED_AT)
        with engine.begin() as connection:
            connection.execute(
                market_thesis_table.update()
                .where(market_thesis_table.c.fingerprint == fingerprint)
                .values(payload='{"not": "a thesis"}')
            )

        with pytest.raises(DecisionJournalIntegrityError):
            journal.get_thesis(fingerprint)

    def test_a_tampered_but_valid_payload_still_fails_closed(
        self, engine: Engine, journal: SqliteDecisionJournal
    ) -> None:
        """The subtler corruption: a payload that still parses as a
        `MarketThesis` but no longer hashes to the row's own key."""
        fingerprint = journal.record_thesis(build_thesis(), recorded_at=RECORDED_AT)
        other = build_thesis(bias=StructureBias.BEARISH)
        with engine.begin() as connection:
            connection.execute(
                market_thesis_table.update()
                .where(market_thesis_table.c.fingerprint == fingerprint)
                .values(payload=other.model_dump_json())
            )

        with pytest.raises(DecisionJournalIntegrityError, match="no longer hashes"):
            journal.get_thesis(fingerprint)


class TestTheAnalysisIdIsASiblingKey:
    def test_a_thesis_is_found_by_the_id_its_decision_also_carries(
        self, journal: SqliteDecisionJournal
    ) -> None:
        """THE WHOLE POINT OF THE RECORD. The thesis holds no fingerprint of a
        decision or a verdict; it is findable because all three carry the same
        `analysis_id`, which the orchestrator minted before any of them
        existed."""
        thesis = build_thesis()
        journal.record_thesis(thesis, recorded_at=RECORDED_AT)

        assert journal.list_theses_by_analysis_id(ANALYSIS_ID) == (thesis,)

    def test_a_thesis_carries_no_reference_to_the_decision_chain(self) -> None:
        """A back-reference would couple advisory evidence to the authority
        that reviewed it. Asserted on the model, where it has to hold."""
        fields = set(MarketThesis.model_fields)

        assert "analysis_id" in fields
        assert not {f for f in fields if "decision" in f or "verdict" in f or "fill" in f}

    def test_an_unknown_analysis_id_returns_nothing(self, journal: SqliteDecisionJournal) -> None:
        journal.record_thesis(build_thesis(), recorded_at=RECORDED_AT)

        assert journal.list_theses_by_analysis_id("analysis-nothing") == ()

    def test_two_theses_can_share_one_analysis_id(self, journal: SqliteDecisionJournal) -> None:
        """THE STORE DOES NOT ENFORCE WHAT THE MINTER PROMISES, AND THAT IS
        WHY THE METHOD RETURNS A TUPLE.

        Mission 8 v2.2 made `orion.orchestrator.analysis.build_analysis_id`
        name one analysis rather than one bar, so this build no longer *writes*
        a collision. It must still be able to *read* one: this journal is
        append-only and holds rows minted under the older rule, and nothing
        stops another caller from choosing its own id. A UNIQUE constraint here
        would turn those permanent rows into a database that fails to open.
        `list_verdicts_by_analysis_id` and `list_decisions_by_analysis_id`
        behave this way for the same reason, and `orion.reflection` is where
        the ambiguity is refused rather than guessed at.
        """
        first = build_thesis(formed_at=THESIS_TIME)
        second = build_thesis(formed_at=THESIS_TIME + timedelta(minutes=30))
        assert market_thesis_fingerprint(first) != market_thesis_fingerprint(second)

        journal.record_thesis(first, recorded_at=RECORDED_AT)
        journal.record_thesis(second, recorded_at=RECORDED_AT + timedelta(minutes=30))

        found = journal.list_theses_by_analysis_id(ANALYSIS_ID)
        assert set(found) == {first, second}

    def test_theses_come_back_oldest_recorded_first(self, journal: SqliteDecisionJournal) -> None:
        first = build_thesis(formed_at=THESIS_TIME)
        second = build_thesis(formed_at=THESIS_TIME + timedelta(minutes=30))
        journal.record_thesis(first, recorded_at=RECORDED_AT)
        journal.record_thesis(second, recorded_at=RECORDED_AT + timedelta(hours=1))

        assert journal.list_theses_by_analysis_id(ANALYSIS_ID) == (first, second)

    def test_another_analysis_sees_none_of_it(self, journal: SqliteDecisionJournal) -> None:
        journal.record_thesis(build_thesis(), recorded_at=RECORDED_AT)
        other = build_thesis(
            analysis_id="analysis-XAUUSD-4h-2026-08-31T08:00:00+00:00-2026-08-31T12:00:00+00:00"
        )
        journal.record_thesis(other, recorded_at=RECORDED_AT)

        assert journal.list_theses_by_analysis_id(ANALYSIS_ID) == (build_thesis(),)


def test_the_journal_still_declares_no_update_or_delete() -> None:
    """The seventh record did not bring a seventh way to rewrite history."""
    from orion.memory.journal import DecisionJournal

    names = {name for name in vars(DecisionJournal) if not name.startswith("_")}

    assert not {name for name in names if name.startswith(("update_", "delete_"))}
    assert "record_thesis" in names
    assert {"get_thesis", "list_theses_by_analysis_id"} <= names
