"""`orion reflect`: the first caller `orion.reflection` has ever had.

WHAT MISSION 4 PHASE 2c ACTUALLY CHANGED. `reflect_on_closed_position` has
existed since Mission 5 and had no caller anywhere in `src/`, and the forward
cycle journaled a `Fill` and a `ClosedPosition` without the `RiskVerdict` and
`ProposedDecision` they reference — so Reflection could not have run on a real
trade even if something had asked it to. 2c writes that chain and adds this
verb, and these are the tests for the verb.

THE ASSERTIONS THAT MATTER MOST ARE THE READ-ONLY ONES. `create_memory_engine`
creates a database and its tables merely by being called, so "did a file
appear?" is the real specification for a command that claims to read. Every
path below is checked against an isolated working directory with `rglob`, and a
refusal asserts zero files — a command that answers "no history" by creating an
empty journal has not answered anything.

NOTHING HERE CONTACTS A VENDOR. `reflect` runs no analysis and needs no
credential; the suite proves it by running with the environment stripped of
both API keys.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from orion.analysis.evidence import build_evidence
from orion.cli import (
    DEFAULT_REFLECTION_LIMIT,
    EXIT_NO_ACCOUNT,
    EXIT_OK,
    EXIT_REFLECTION_FAILED,
    EXIT_USAGE,
    NO_HISTORY_MESSAGE,
    NO_THESIS_RECORDED,
    REFLECTION_FAILED_MESSAGE,
    build_parser,
    main,
    render_reflections,
)
from orion.decision.engine import decide
from orion.memory.engine import create_memory_engine
from orion.memory.journal import DecisionJournal
from orion.memory.portfolio_store import SqlitePortfolioStore
from orion.memory.schema import closed_position_table
from orion.memory.sqlite_journal import SqliteDecisionJournal
from orion.paper.policy import PaperExecutionPolicy
from orion.paper.schemas import CloseReason
from orion.paper.session import CycleOutcome, run_cycle
from orion.reflection.analysis import reflect_on_recent_closed_positions
from orion.risk.guardian import review
from orion.risk.policy import KillSwitchState, RiskPolicy
from orion.risk.portfolio import initial_portfolio_state, mark_to_market
from orion.thesis.schemas import ALIGNMENT_SUMMARY

from .orchestrator.conftest import (
    LAST_BAR_TIME,
    NOW,
    FakeReasoningProvider,
    make_bars,
    make_snapshot,
)
from .thesis.builders import build_thesis

FAKE_KEY = "sk-live-supersecret-1234567890"
SYMBOL = "XAUUSD"
POLICY = PaperExecutionPolicy()


@pytest.fixture
def account_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated working directory and an `ORION_DB_PATH` inside it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORION_DB_PATH", str(tmp_path / "orion.db"))
    return tmp_path


def files_in(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def databases_in(root: Path) -> set[str]:
    """Distinct SQLite databases, `-wal`/`-shm` folded into their database.

    See `tests/test_cli_account.py::databases_in`: the sidecars come and go with
    connection lifetime, so comparing raw file lists across a command asserts
    garbage-collection timing rather than behaviour.
    """
    names = set()
    for name in files_in(root):
        for suffix in ("-wal", "-shm"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        names.add(name)
    return names


def seed_history(
    db_path: Path, *, trades: int = 1, journal_chain: bool = True, theses: bool = False
) -> None:
    """Run `trades` real open-then-close paper cycles into a real database.

    Not fixtures and not doubles: the real `decide`/`review`/`run_cycle`, so the
    journal this verb reads holds exactly what a real `orion paper` would have
    written. `journal_chain=False` writes the fill and the close but not the
    records that authorised them -- the state every position opened before this
    phase is permanently in, and the one `reflect` must report rather than hide.

    `theses=True` hands each cycle a `MarketThesis` under that trade's own
    `analysis_id`, exactly as `orion paper` hands one off the report. The whole
    Mission 8 v2.2 path is then real end to end for the rendering tests: the
    session journals it, `orion.reflection` recovers it by the sibling key, and
    `render_reflections` prints what came back -- no hand-built batch anywhere.
    """
    engine = create_memory_engine(str(db_path))
    try:
        store = SqlitePortfolioStore(engine)
        journal: DecisionJournal = SqliteDecisionJournal(engine)
        store.initialize(
            initial_portfolio_state(symbol=SYMBOL, starting_balance=100_000.0, now=LAST_BAR_TIME)
        )
        writer = journal if journal_chain else _FillsOnlyJournal(journal)

        for index in range(trades):
            # The whole series advances with the clock, not just `now`: the Risk
            # Guardian's freshness controls reject evidence that has gone stale,
            # so a second trade two hours later needs two more hours of bars.
            bar_end = LAST_BAR_TIME + timedelta(hours=index * 2)
            opened_at = NOW + timedelta(hours=index * 2)
            evidence = build_evidence(
                make_snapshot(bars=make_bars(end=bar_end), retrieved_at=opened_at)
            )
            reasoning = FakeReasoningProvider().reason(evidence)
            decision = decide(evidence, reasoning, analysis_id=f"a-{index}", now=opened_at)
            marked = mark_to_market(
                store.load().state, now=opened_at, last_price=evidence.last_price
            )
            verdict = review(
                decision,
                evidence,
                policy=RiskPolicy(kill_switch=KillSwitchState.DISENGAGED),
                portfolio=marked,
                now=opened_at,
            )
            opened = run_cycle(
                decision=decision,
                evidence=evidence,
                reasoning=reasoning,
                thesis=(
                    build_thesis(analysis_id=f"a-{index}", with_macro=index % 2 == 0)
                    if theses
                    else None
                ),
                verdict=verdict,
                store=store,
                journal=writer,
                policy=POLICY,
                risk_per_trade_fraction=0.01,
                now=opened_at,
            )
            assert opened.outcome is CycleOutcome.EXECUTED
            assert opened.portfolio is not None
            held = opened.portfolio.open_position
            assert held is not None

            closed_at = opened_at + timedelta(hours=1)
            bars = make_bars(end=bar_end + timedelta(hours=1))
            final = bars[-1]
            hit = held.take_profit + 1.0
            bars[-1] = final.model_copy(update={"close": hit, "high": max(final.high, hit)})
            exit_evidence = build_evidence(make_snapshot(bars=bars, retrieved_at=closed_at))
            closed = run_cycle(
                decision=decision,
                evidence=exit_evidence,
                reasoning=reasoning,
                verdict=None,
                store=store,
                journal=writer,
                policy=POLICY,
                risk_per_trade_fraction=0.01,
                now=closed_at,
            )
            assert closed.outcome is CycleOutcome.CLOSED
    finally:
        engine.dispose()


class _FillsOnlyJournal:
    """Phase 2b's journal, restored: fills and closes, no authorising chain."""

    def __init__(self, inner: DecisionJournal) -> None:
        self._inner = inner

    def record_evidence(self, evidence, *, recorded_at):  # type: ignore[no-untyped-def]
        return "0" * 64

    def record_reasoning(self, reasoning, *, recorded_at):  # type: ignore[no-untyped-def]
        return "0" * 64

    def record_decision(self, decision, *, recorded_at):  # type: ignore[no-untyped-def]
        return "0" * 64

    def record_verdict(self, verdict, *, recorded_at):  # type: ignore[no-untyped-def]
        return "0" * 64

    def record_thesis(self, thesis, *, recorded_at):  # type: ignore[no-untyped-def]
        return "0" * 64

    def record_fill(self, fill, *, recorded_at):  # type: ignore[no-untyped-def]
        return self._inner.record_fill(fill, recorded_at=recorded_at)

    def record_closed_position(self, closed, *, recorded_at):  # type: ignore[no-untyped-def]
        return self._inner.record_closed_position(closed, recorded_at=recorded_at)


# ------------------------------------------------------------- the happy path


class TestReflectReadsRealHistory:
    def test_it_reports_a_real_closed_trade(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """THE MILESTONE, END TO END. A real cycle wrote this journal; the real
        reflection layer read it back; nothing in between was a fixture."""
        seed_history(account_home / "orion.db")

        assert main(["reflect", SYMBOL]) == EXIT_OK

        out = capsys.readouterr().out
        assert "XAUUSD reflection" in out
        assert "closed trades    1" in out
        assert "reflected        1" in out
        assert "not reflectable  0" in out
        assert "take_profit_hit" in out
        assert "the predicted take-profit" in out

    def test_it_names_every_fingerprint_the_reflection_came_from(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Provenance is the point: a reader must be able to go and fetch each
        record for themselves rather than take the rendering on trust."""
        seed_history(account_home / "orion.db")

        assert main(["reflect", SYMBOL]) == EXIT_OK

        out = capsys.readouterr().out
        engine = create_memory_engine(str(account_home / "orion.db"))
        try:
            journal = SqliteDecisionJournal(engine)
            batch = reflect_on_recent_closed_positions(journal, SYMBOL, limit=10, now=NOW)
        finally:
            engine.dispose()
        item = batch.reflections[0]
        assert item.closed_position_fingerprint in out
        assert item.opened_by_verdict_fingerprint in out
        assert item.decision_fingerprint in out

    def test_an_empty_journal_is_an_answer_not_a_failure(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An account with nothing closed yet exits 0 and says so."""
        assert main(["account", "open", "--balance", "100000"]) == EXIT_OK
        capsys.readouterr()

        assert main(["reflect", SYMBOL]) == EXIT_OK

        out = capsys.readouterr().out
        assert "closed trades    0" in out
        assert "reflected        0" in out

    def test_the_limit_is_honoured(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        seed_history(account_home / "orion.db", trades=3)

        assert main(["reflect", SYMBOL, "--limit", "1"]) == EXIT_OK

        out = capsys.readouterr().out
        assert "closed trades    3" in out
        assert "reflected        1" in out

    def test_the_default_limit_is_the_declared_one(self) -> None:
        parsed = build_parser().parse_args(["reflect", SYMBOL])
        assert parsed.limit == DEFAULT_REFLECTION_LIMIT

    def test_a_trade_with_no_journaled_chain_is_named_not_hidden(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A position opened by Phase 2b has no verdict in the journal. ORION
        must say it cannot explain that trade, not quietly omit it."""
        seed_history(account_home / "orion.db", journal_chain=False)

        assert main(["reflect", SYMBOL]) == EXIT_OK

        out = capsys.readouterr().out
        assert "closed trades    1" in out
        assert "reflected        0" in out
        assert "not reflectable  1" in out
        assert "cannot say what it predicted" in out


# ----------------------------------------------------------- it writes nothing


class TestReflectWritesNothing:
    def test_no_history_is_refused_without_creating_a_database(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The rule `account show` follows, applied here: a command that
        answers by first creating the thing it is reading has not answered."""
        code = main(["reflect", SYMBOL])

        assert code == EXIT_NO_ACCOUNT
        assert capsys.readouterr().err.strip() == NO_HISTORY_MESSAGE
        assert files_in(account_home) == []

    def test_reading_a_real_journal_adds_no_database(self, account_home: Path) -> None:
        seed_history(account_home / "orion.db")
        before = databases_in(account_home)

        assert main(["reflect", SYMBOL]) == EXIT_OK

        assert databases_in(account_home) == before
        assert not (account_home.parent / "orion.db").exists()

    def test_it_does_not_change_the_account(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Reflection is typed against `DecisionJournal` and never a
        `PortfolioStore`, so there is no path through it to account state. This
        proves the composition root did not open one on its behalf either."""
        seed_history(account_home / "orion.db")
        assert main(["account", "show"]) == EXIT_OK
        before = capsys.readouterr().out

        assert main(["reflect", SYMBOL]) == EXIT_OK
        capsys.readouterr()
        assert main(["account", "show"]) == EXIT_OK

        assert capsys.readouterr().out == before

    def test_the_journal_is_unchanged_by_being_read(self, account_home: Path) -> None:
        """Row-level, not file-level: reading must add no record either."""
        seed_history(account_home / "orion.db", trades=2)

        def closed_rows() -> int:
            engine = create_memory_engine(str(account_home / "orion.db"))
            try:
                with engine.begin() as connection:
                    return len(list(connection.execute(closed_position_table.select())))
            finally:
                engine.dispose()

        before = closed_rows()
        assert main(["reflect", SYMBOL]) == EXIT_OK
        assert closed_rows() == before == 2


# --------------------------------------------------------------- no vendor


class TestReflectNeedsNothingLive:
    def test_it_runs_with_no_credentials_at_all(
        self, account_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`reflect` runs no analysis, so a deployment with no API keys can
        still read its own history."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        seed_history(account_home / "orion.db")

        assert main(["reflect", SYMBOL]) == EXIT_OK

    def test_it_never_builds_an_analysis_service(self, account_home: Path) -> None:
        """The seam a wrong wiring would show up in: `reflect` is dispatched
        without any service factory, so passing one must change nothing."""
        seed_history(account_home / "orion.db")
        built = 0

        def factory():  # type: ignore[no-untyped-def]
            nonlocal built
            built += 1
            return None

        assert main(["reflect", SYMBOL], service_factory=factory) == EXIT_OK
        assert built == 0


# --------------------------------------------------------------- refusals


class TestReflectRefusesSafely:
    @pytest.mark.parametrize(
        "argument", ["../../etc/passwd", "/etc/passwd", ":memory:", "orion.db", "$(whoami)"]
    )
    def test_a_hostile_symbol_never_becomes_a_path(self, account_home: Path, argument: str) -> None:
        """`--limit` and `SYMBOL` are the only inputs, and neither reaches a
        filesystem path: the symbol goes through the closed alias table and the
        database location is `ORION_DB_PATH` alone."""
        seed_history(account_home / "orion.db")
        before = databases_in(account_home)

        assert main(["reflect", argument]) == EXIT_USAGE

        assert databases_in(account_home) == before
        assert not (account_home.parent / "orion.db").exists()

    def test_the_rejected_argument_is_not_echoed_back(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["reflect", "EURUSD"]) == EXIT_USAGE
        captured = capsys.readouterr()
        assert "EURUSD" not in captured.err
        assert "XAUUSD" in captured.err

    @pytest.mark.parametrize("limit", ["0", "-1", "1.5", "nan", "inf", "lots"])
    def test_a_bad_limit_exits_two_without_a_traceback(
        self, account_home: Path, limit: str
    ) -> None:
        with pytest.raises(SystemExit) as exit_info:
            build_parser().parse_args(["reflect", SYMBOL, "--limit", limit])
        assert exit_info.value.code == 2

    def test_an_unreadable_journal_prints_one_fixed_sentence(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`DecisionJournalError` embeds SQLAlchemy text naming the database and
        its path. The exception is neither bound nor formatted."""
        seed_history(account_home / "orion.db")

        @contextmanager
        def broken(db_path: str) -> Iterator[DecisionJournal]:
            raise RuntimeError(f"cannot open {db_path} with key {FAKE_KEY}")
            yield  # pragma: no cover

        code = main(["reflect", SYMBOL], reflection_resources=broken)

        assert code == EXIT_REFLECTION_FAILED
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == REFLECTION_FAILED_MESSAGE
        assert FAKE_KEY not in captured.err
        assert str(account_home) not in captured.err

    def test_a_corrupted_journal_is_reported_as_unreadable_not_as_empty(
        self, account_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """FAIL CLOSED. A journal whose contents do not verify must never be
        presented as a journal with nothing in it."""
        seed_history(account_home / "orion.db")
        engine = create_memory_engine(str(account_home / "orion.db"))
        try:
            with engine.begin() as connection:
                connection.execute(
                    closed_position_table.update().values(payload='{"not": "a position"}')
                )
        finally:
            engine.dispose()

        code = main(["reflect", SYMBOL])

        assert code == EXIT_REFLECTION_FAILED
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip() == REFLECTION_FAILED_MESSAGE


# --------------------------------------------------------- the rendering


class TestTheRenderingJudgesNothing:
    def _batch(self, account_home: Path, *, theses: bool = False):  # type: ignore[no-untyped-def]
        seed_history(account_home / "orion.db", trades=2, theses=theses)
        engine = create_memory_engine(str(account_home / "orion.db"))
        try:
            return reflect_on_recent_closed_positions(
                SqliteDecisionJournal(engine), SYMBOL, limit=10, now=NOW
            )
        finally:
            engine.dispose()

    def test_the_rendering_is_deterministic(self, account_home: Path) -> None:
        batch = self._batch(account_home)
        assert render_reflections(batch) == render_reflections(batch)

    def test_it_prints_no_score_grade_or_aggregate(self, account_home: Path) -> None:
        """Mission 5's constraint reaches the terminal too. A win rate or a
        total P&L printed over several trades reads as a reason to change
        strategy, which is exactly what one closed trade must never be."""
        text = render_reflections(self._batch(account_home)).lower()
        for banned in (
            "score",
            "grade",
            "rating",
            "win rate",
            "winrate",
            "total pnl",
            "net pnl",
            "average",
            "expectancy",
            "recommend",
            "good decision",
            "bad decision",
        ):
            assert banned not in text, f"the rendering must judge nothing: {banned!r}"

    def test_it_prints_the_margin_beside_the_matched_level(self, account_home: Path) -> None:
        """The two facts that let a human tell a bad outcome from a bad
        decision, side by side, with the judgment left to them."""
        batch = self._batch(account_home)
        text = render_reflections(batch)
        item = batch.reflections[0]
        assert f"{item.approval_margin:+.2f}" in text
        assert f"{item.policy_min_risk_reward:.2f} minimum" in text
        assert "exit matched" in text

    def test_it_invents_no_number_when_the_opening_fill_is_absent(self, account_home: Path) -> None:
        """`opening_fee` is optional enrichment. An absent one is said out
        loud, never rendered as 0.00."""
        batch = self._batch(account_home)
        item = batch.reflections[0].model_copy(update={"opening_fee": None})
        text = render_reflections(batch.model_copy(update={"reflections": (item,)}))
        assert "unknown opening" in text


class TestTheThesisReachesTheReader:
    """Mission 8 v2.2's last mile: the journaled thesis is printed.

    THE WHOLE PATH IS REAL HERE. `seed_history(theses=True)` hands each cycle a
    `MarketThesis`, `run_cycle` journals it, `reflect_on_recent_closed_
    positions` recovers it by `analysis_id`, and these tests read what
    `render_reflections` did with it. A thesis that was stored and never shown
    is the state Mission 8 v2 was criticised for, and it is what this section
    exists to prevent recurring on the reflection path.
    """

    def _batch(self, account_home: Path, *, theses: bool = True):  # type: ignore[no-untyped-def]
        seed_history(account_home / "orion.db", trades=2, theses=theses)
        engine = create_memory_engine(str(account_home / "orion.db"))
        try:
            return reflect_on_recent_closed_positions(
                SqliteDecisionJournal(engine), SYMBOL, limit=10, now=NOW
            )
        finally:
            engine.dispose()

    def test_the_specialists_reading_is_printed_for_a_trade_that_has_one(
        self, account_home: Path
    ) -> None:
        batch = self._batch(account_home)
        item = batch.reflections[0]
        assert item.thesis is not None

        text = render_reflections(batch)

        assert item.thesis.alignment.value in text
        assert item.thesis.technical_bias.value in text
        assert item.thesis.summary in text

    def test_every_printed_sentence_is_the_thesis_own_canonical_one(
        self, account_home: Path
    ) -> None:
        """AUTHORED BY THE REGISTRY, NOT BY THE RENDERER. `MarketThesis`
        validates that `summary` is the canonical sentence for its alignment
        and that every `ThesisPoint.detail` is the committed one, so printing
        those verbatim is the only way this command shows market commentary --
        and it is commentary ORION committed, not composed at render time."""
        batch = self._batch(account_home)
        text = render_reflections(batch)

        for item in batch.reflections:
            assert item.thesis is not None
            assert item.thesis.summary in ALIGNMENT_SUMMARY.values()
            assert item.thesis.summary in text
            for point in item.thesis.agreements + item.thesis.conflicts:
                assert point.topic.value in text

    def test_the_macro_half_is_printed_only_when_the_thesis_carries_one(
        self, account_home: Path
    ) -> None:
        """`seed_history` alternates: the first trade's thesis has the macro
        half, the second's does not. A rendering that printed a macro *reading*
        for the technical-only thesis would be inventing one.

        The check is for the reading rather than the word: a technical-only
        thesis is `macro_absent` and its canonical sentence says the macro half
        is missing, so "macro" appears in that block and should -- absence is
        stated here, exactly as it is everywhere else in this codebase.
        """
        batch = self._batch(account_home)
        blocks = render_reflections(batch).split("\n\n")
        with_macro = next(b for b in blocks if "1. " in b)
        without_macro = next(b for b in blocks if "2. " in b)

        first, second = batch.reflections[0].thesis, batch.reflections[1].thesis
        assert first is not None and first.macro_condition is not None
        assert second is not None and second.macro_condition is None
        assert f", macro {first.macro_condition.value}" in with_macro
        assert ", macro " not in without_macro
        assert second.alignment.value in without_macro

    def test_a_trade_with_no_thesis_says_so_rather_than_printing_nothing(
        self, account_home: Path
    ) -> None:
        """Absence is reported, never left blank. Every trade opened before
        v2.2 is permanently in this state, and a silent gap would read as "the
        specialists had no opinion" rather than "nothing was recorded"."""
        batch = self._batch(account_home, theses=False)

        text = render_reflections(batch)

        assert batch.reflections[0].thesis is None
        assert NO_THESIS_RECORDED in text

    def test_the_thesis_is_printed_between_the_prediction_and_the_outcome(
        self, account_home: Path
    ) -> None:
        """Placement is the point: what the specialists were reading sits
        between what ORION predicted and how the trade ended, with no line
        drawn between them."""
        text = render_reflections(self._batch(account_home))

        assert text.index("risk/reward") < text.index("specialists") < text.index("exit matched")

    def test_the_rendering_still_judges_nothing_with_a_thesis_attached(
        self, account_home: Path
    ) -> None:
        """The thesis is evidence printed beside an outcome, and the renderer
        draws no conclusion from the pair -- no "confirmed", no "wrong", no
        agreement score between the reading and the result."""
        text = render_reflections(self._batch(account_home)).lower()

        for banned in (
            "score",
            "grade",
            "correct",
            "incorrect",
            "wrong",
            "right call",
            "confirmed by",
            "as predicted by",
            "thesis was",
        ):
            assert banned not in text, f"the rendering must judge nothing: {banned!r}"

    def test_it_is_still_deterministic(self, account_home: Path) -> None:
        batch = self._batch(account_home)
        assert render_reflections(batch) == render_reflections(batch)


class TestTheClosedMappingsAreComplete:
    """`render_reflections` and `render_cycle` index closed mappings by enum.

    Both are looked up outside the runner's `try`, so a missing member would be
    a `KeyError` traceback rather than one of this file's fixed sentences. These
    two tests make that unreachable: adding a `CloseReason` or a `CycleOutcome`
    fails here, at the point where the words are chosen, rather than in front of
    a user.
    """

    def test_every_close_reason_has_words(self) -> None:
        from orion.cli import _EXIT_MATCH

        assert set(_EXIT_MATCH) == set(CloseReason)

    def test_every_cycle_outcome_has_words(self) -> None:
        from orion.cli import _CYCLE_SUMMARY

        assert set(_CYCLE_SUMMARY) == set(CycleOutcome)
