"""Abstract interface for the immutable Decision Memory journal.

Mirrors `orion.data.provider_base.MarketDataProvider`, `orion.reasoning.
provider_base.ReasoningProvider`, and `orion.paper.store.PortfolioStore`:
the rest of ORION depends only on this interface, never on a specific
storage mechanism. This is the durable implementation of the "immutable
decision journal" named in the master engineering brief's Mission 2 section
and pre-committed in `docs/DECISIONS.md`'s Mission 0 entry ("the journal's
access layer only exposes create/read operations — no update or delete").

That is a structural fact about this class, not a convention a future
engineer has to remember: `DecisionJournal` declares no `update_*`/
`delete_*` method at all, for any record type, ever. Mission 5 (Reflection),
whenever it is built, can only ever call the read methods declared here —
there is no method on this interface it could call to mutate history even
if it tried.

Every `record_*` method takes the already-validated domain object plus a
required, caller-supplied `recorded_at: datetime` — never `datetime.now()` —
matching this codebase's determinism discipline everywhere else (`orion.
decision.engine.decide`, `orion.risk.guardian.review`, `orion.paper.
execution.execute` all take `now` the same way). `recorded_at` answers "when
did Memory learn this," which is a different question from the record's own
domain timestamp (`Evidence.as_of`, `ProposedDecision.decided_at`, `RiskVerdict.
reviewed_at`) — conflating the two would make it impossible to later
distinguish "this was journaled late" from "this decision was made late."

Every `record_*` method returns the record's own content fingerprint,
computed internally from the object's existing fingerprint function
(`evidence_fingerprint`, `canonical_hash`, `decision_fingerprint`,
`risk_verdict_fingerprint`) — never accepted as a parameter. This is a
structural anti-forgery property, not a convention: there is no argument
through which a caller could pass a fingerprint that disagrees with the
object's actual content, because the interface never asks for one.

A duplicate `record_*` call with byte-identical content to a
previously-recorded object is a safe no-op (idempotent replay protection),
because every journal table's primary key is that object's own content
fingerprint — see `orion.memory.schema`'s module docstring. A duplicate call
whose fingerprint collides but whose content differs (which should be
cryptographically impossible, but is checked rather than assumed) raises
`DecisionJournalIntegrityError` rather than silently keeping whichever
version happened to be written first.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from orion.analysis.evidence import Evidence
from orion.decision.schemas import ProposedDecision
from orion.paper.schemas import ClosedPosition, Fill
from orion.reasoning.schemas import ReasoningOutput
from orion.risk.schemas import RiskVerdict
from orion.thesis.schemas import MarketThesis


class DecisionJournalError(RuntimeError):
    """Raised when the journal cannot record or retrieve a record.

    Never caught and papered over with a fabricated/empty result — a caller
    that cannot read or write the real journal must fail closed. Mirrors
    `orion.paper.store.PortfolioStoreError`'s "I could not persist" vs. "I
    persisted, and here is what happened" distinction one layer further
    down.
    """


class DecisionJournalIntegrityError(DecisionJournalError):
    """Raised when stored content does not match its own fingerprint —
    either a hash collision on write (a different object with the same
    content fingerprint already exists) or corrupted/tampered data on read
    (the stored payload no longer hashes to the row's own primary key).

    A distinct subclass, not just a distinct message, matching this
    codebase's established pattern (`EvidenceLinkageError`,
    `PortfolioStoreConcurrencyError`): a caller that needs to react
    specifically to "the journal's own data is inconsistent" can catch this
    without also catching an ordinary connection failure.
    """


class DecisionJournal(ABC):
    """The append-only, content-addressed record of every Evidence /
    ReasoningOutput / ProposedDecision / RiskVerdict / MarketThesis / Fill /
    ClosedPosition ORION has ever produced.

    Deliberately does not attempt to record `PortfolioState` — that is
    `orion.paper.store.PortfolioStore`'s job (see `orion.memory.
    portfolio_store.SqlitePortfolioStore`), a different contract (a current
    value with monotonic-revision CAS, not a write-once fact).
    """

    @abstractmethod
    def record_evidence(self, evidence: Evidence, *, recorded_at: datetime) -> str:
        """Persist `evidence`, returning its content fingerprint. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def record_reasoning(self, reasoning: ReasoningOutput, *, recorded_at: datetime) -> str:
        """Persist `reasoning`, returning its content fingerprint. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def record_decision(self, decision: ProposedDecision, *, recorded_at: datetime) -> str:
        """Persist `decision`, returning its content fingerprint. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def record_verdict(self, verdict: RiskVerdict, *, recorded_at: datetime) -> str:
        """Persist `verdict`, returning its content fingerprint. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def record_thesis(self, thesis: MarketThesis, *, recorded_at: datetime) -> str:
        """Persist `thesis`, returning its content fingerprint. Idempotent.

        The seventh record, and the only one that references nothing else in
        the journal. A `MarketThesis` is advisory evidence: it carries the
        `analysis_id` the decision and the verdict also carry, which is what
        makes it findable from them, but it holds no fingerprint of either and
        so can be written before any of them exist.

        `orion.thesis.schemas` is a deliberately separate package from
        `orion.analysts`, which this one may not import — see that module's
        docstring for why the schema moved rather than the wall.
        """
        raise NotImplementedError

    @abstractmethod
    def record_fill(self, fill: Fill, *, recorded_at: datetime) -> str:
        """Persist `fill`, returning its content fingerprint. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def record_closed_position(self, closed: ClosedPosition, *, recorded_at: datetime) -> str:
        """Persist `closed`, returning its content fingerprint. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def get_evidence(self, fingerprint: str) -> Evidence | None:
        """Return the Evidence stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def get_reasoning(self, fingerprint: str) -> ReasoningOutput | None:
        """Return the ReasoningOutput stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def get_decision(self, fingerprint: str) -> ProposedDecision | None:
        """Return the ProposedDecision stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def get_verdict(self, fingerprint: str) -> RiskVerdict | None:
        """Return the RiskVerdict stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def get_thesis(self, fingerprint: str) -> MarketThesis | None:
        """Return the MarketThesis stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def get_fill(self, fingerprint: str) -> Fill | None:
        """Return the Fill stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def get_closed_position(self, fingerprint: str) -> ClosedPosition | None:
        """Return the ClosedPosition stored under `fingerprint`, or None if absent."""
        raise NotImplementedError

    @abstractmethod
    def list_decisions_by_analysis_id(self, analysis_id: str) -> tuple[ProposedDecision, ...]:
        """All ProposedDecisions recorded under `analysis_id`, oldest-recorded first."""
        raise NotImplementedError

    @abstractmethod
    def list_verdicts_by_analysis_id(self, analysis_id: str) -> tuple[RiskVerdict, ...]:
        """All RiskVerdicts recorded under `analysis_id`, oldest-recorded first."""
        raise NotImplementedError

    @abstractmethod
    def list_verdicts_by_symbol(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[RiskVerdict, ...]:
        """RiskVerdicts for `symbol` with `reviewed_at` in `[start, end]`
        (either bound optional), ordered by `reviewed_at` ascending, capped
        at `limit` if given. The mechanical "historical search" capability —
        no semantic/similarity search; see `docs/DECISIONS.md`."""
        raise NotImplementedError

    @abstractmethod
    def list_theses_by_analysis_id(self, analysis_id: str) -> tuple[MarketThesis, ...]:
        """All MarketTheses recorded under `analysis_id`, oldest-recorded first.

        A tuple, not `MarketThesis | None`, and it stays one even though
        Mission 8 v2.2 made the orchestrator's `analysis_id` name a single
        analysis. THE JOURNAL DOES NOT ENFORCE WHAT THE MINTER PROMISES.
        Rows written before that change carry ids minted from the bar alone
        and can genuinely collide; nothing stops another caller from writing
        its own id; and this store is append-only, so a collision that exists
        is permanent and must remain readable. `list_verdicts_by_analysis_id`
        and `list_decisions_by_analysis_id` return tuples for the same reason.
        Nothing in this interface assumes one record per analysis, and a caller
        that needs exactly one has to say what it does about more —
        `orion.reflection` refuses to guess.
        """
        raise NotImplementedError

    @abstractmethod
    def list_fills_by_verdict_fingerprint(self, risk_verdict_fingerprint: str) -> tuple[Fill, ...]:
        """All Fills recorded under `risk_verdict_fingerprint`, oldest-
        recorded first. Mission 4 v1 opens at most one position per approved
        verdict, so this is normally empty or a single-element tuple — a
        tuple, not `Fill | None`, because nothing in this interface assumes
        exactly one (a replayed/retried `execute()` call at a different
        `now` produces a second, distinct fingerprint; see `docs/
        DECISIONS.md`'s writer-trust-boundary note)."""
        raise NotImplementedError

    @abstractmethod
    def list_fills_by_symbol(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[Fill, ...]:
        """Fills for `symbol` with `filled_at` in `[start, end]` (either
        bound optional), ordered by `filled_at` ascending, capped at `limit`
        if given. Mirrors `list_verdicts_by_symbol` exactly."""
        raise NotImplementedError

    @abstractmethod
    def list_closed_positions_by_verdict_fingerprint(
        self, opened_by_verdict_fingerprint: str
    ) -> tuple[ClosedPosition, ...]:
        """All ClosedPositions recorded under `opened_by_verdict_fingerprint`,
        oldest-recorded first. A tuple, not `ClosedPosition | None`, for the
        same reason as `list_fills_by_verdict_fingerprint`."""
        raise NotImplementedError

    @abstractmethod
    def list_closed_positions_by_symbol(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[ClosedPosition, ...]:
        """ClosedPositions for `symbol` with `closed_at` in `[start, end]`
        (either bound optional), ordered by `closed_at` ascending, capped at
        `limit` if given. Mirrors `list_verdicts_by_symbol` exactly. This is
        the query Mission 5's "actual outcome" step reads from: realized
        P&L, close reason, entry/exit price, fee — everything `orion.paper.
        execution.resolve_stop_take_profit` computed for a closed trade."""
        raise NotImplementedError
