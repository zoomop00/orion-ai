"""Abstract interface for persisting/reconstructing paper-account state,
with monotonic-revision optimistic-concurrency protection against stale
writes.

Mirrors `orion.data.provider_base.MarketDataProvider` and
`orion.reasoning.provider_base.ReasoningProvider`: the rest of ORION depends
only on this interface, never on a specific storage mechanism. Mission 4
ships only an in-memory implementation — no database, no filesystem, no
network. Mission 2 (Decision Memory), whenever it lands, can add a durable
implementation of the same interface without `orion.paper`'s execution
logic or `orion.risk` changing at all. This is the Mission 4 architecture
decision B1 in `docs/DECISIONS.md`: Paper Trading does not wait for
Mission 2, and does not build a large duplicate journal/database system to
work around it not existing yet.

Concurrency is a `revision: int`, not the `PortfolioState` content
fingerprint. An earlier version of this module used `orion.risk.portfolio.
portfolio_state_fingerprint` (a pure content hash) as the compare-and-swap
token; that has a real, empirically-demonstrated flaw — the classic ABA
problem: if the store's content genuinely passes through A -> B -> C where
C happens to be byte-identical to A (a legitimate round trip, e.g. a trade
opened and closed such that the account returns to a value it held before),
a caller still holding A's *content* fingerprint from before B ever
happened can successfully write against C, because C's fingerprint equals
A's. A monotonically-increasing revision never has this problem: even when
content repeats, the revision number issued for C is strictly greater than
the one issued for A, so a stale caller's belief about "the state after A"
can never be mistaken for "the state after C". `portfolio_state_fingerprint`
itself is untouched by this — it remains exactly what it always was, a pure
content-identity/provenance hash, used by `orion.risk.schemas.
PortfolioStateRef` and elsewhere. Concurrency identity and content identity
are deliberately two different mechanisms answering two different
questions ("is this write based on the actual current committed state?"
vs. "what did this state actually contain?"), and conflating them into one
hash is exactly what let the ABA gap in.

`load()`/`save()`/`initialize()` all return a `PortfolioRecord` — the
state paired with the store's own revision for it — atomically, in one
call, so a caller never has to make a second round trip (and risk a race
in a truly concurrent implementation) just to learn the revision it needs
to present on the next write. The revision is minted only by the store
itself; a caller never invents, guesses, or increments one on their own —
it only ever comes from a `PortfolioRecord` this store previously returned.
`orion.paper.execution.execute` does not import this module and knows
nothing about revisions at all — replay/concurrency protection lives
entirely at this boundary, not inside the pure execution function (Mission
4 architecture requirement: `orion.risk`/`orion.paper.execution` stay pure
and side-effect-free; account consistency belongs at the store).

A future durable Mission 2 implementation can enforce the identical
contract atomically with an ordinary
`UPDATE portfolio SET data = ?, revision = revision + 1
 WHERE revision = ? RETURNING data, revision`
(checking the row count / `RETURNING` result, exactly as `save()` below
checks `expected_revision` against its own counter before mutating) — no
distributed locking, no new kind of infrastructure, just the same
single-row transactional guarantee any SQL engine already provides.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orion.risk.portfolio import PortfolioState


class PortfolioStoreError(RuntimeError):
    """Raised when a store cannot load, save, or initialize portfolio
    state.

    Never caught and papered over with a fabricated/default state — a
    caller that cannot read the real account state must fail closed, not
    silently proceed as if the account were flat. Mirrors `orion.risk.
    guardian.RiskGuardianError`'s "I could not decide" vs. "I decided"
    distinction one layer further down.
    """


class PortfolioStoreConcurrencyError(PortfolioStoreError):
    """Raised by `save()` when `expected_revision` does not equal the
    store's actual current revision.

    A distinct subclass, not just a distinct message — mirrors this
    codebase's established pattern (`EvidenceLinkageError`,
    `PortfolioLinkageError`, `PortfolioIntegrityError`): a caller that needs
    to react specifically to "my snapshot went stale" (e.g. reload and
    retry) can catch this without also catching every other
    `PortfolioStoreError`. The write this error blocks is never partially
    applied — the store's state and revision are left exactly as they were.
    """


class PortfolioRecord:
    """A `PortfolioState` paired with this store's own revision for it.

    `revision` is an opaque, store-minted, monotonically-increasing token —
    it identifies *when* (in this store's own commit history) `state` was
    the current snapshot, which is a different question from what `state`
    *contains* (that question is `orion.risk.portfolio.
    portfolio_state_fingerprint`'s). A caller must never construct one of
    these itself or invent a revision number; the only legitimate way to
    hold a `PortfolioRecord` is to have received it from this store's own
    `load()`/`save()`/`initialize()`. Plain container, not a Pydantic
    model — mirrors `orion.paper.execution.ExecutionResult`'s exact style,
    for the same reason: it is never itself journaled or hashed as a unit.
    """

    __slots__ = ("state", "revision")

    def __init__(self, *, state: PortfolioState, revision: int) -> None:
        self.state = state
        self.revision = revision


class PortfolioStore(ABC):
    """The current paper-account snapshot for a single symbol, with
    monotonic-revision-protected writes."""

    @abstractmethod
    def load(self) -> PortfolioRecord:
        """Return the current `PortfolioState` together with its revision.

        Must raise PortfolioStoreError (never return a fabricated default)
        if no state has been initialized yet — a caller that wants a fresh
        account must call `initialize` explicitly.
        """
        raise NotImplementedError

    @abstractmethod
    def save(self, state: PortfolioState, *, expected_revision: int) -> PortfolioRecord:
        """Persist `state` as the new current snapshot, but only if
        `expected_revision` equals the store's actual current revision —
        the revision of the record `state` was built from (via `load()` or
        a prior `save()`/`initialize()`'s returned `PortfolioRecord`). On
        success, returns a new `PortfolioRecord` whose revision is strictly
        greater than `expected_revision` and every revision this store has
        ever issued before — never reused, so it is immune to the ABA
        problem a pure content fingerprint has (see the module docstring).

        Must raise PortfolioStoreConcurrencyError, and leave the stored
        state and revision completely unchanged, if `expected_revision`
        does not match (a stale write) or if no state has been initialized
        yet (there is no revision to match). `orion.paper` is the only
        layer permitted to call this — `orion.risk` never writes to a
        store.
        """
        raise NotImplementedError

    @abstractmethod
    def initialize(self, state: PortfolioState) -> PortfolioRecord:
        """Set the current snapshot for the first time, establishing its
        initial revision.

        Must raise PortfolioStoreError if a snapshot already exists —
        re-initializing a live account is a distinct, deliberate operation
        this interface does not silently allow."""
        raise NotImplementedError


class InMemoryPortfolioStore(PortfolioStore):
    """The Mission 4 implementation: process-lifetime state only, held in
    two private attributes. State does not survive a process restart, by
    design — a durable implementation is Mission 2's job, behind this same
    interface (see the module docstring). The revision check in `save()`
    is a plain integer comparison, not a lock — sufficient here because
    nothing in this process can observe or act on state between the check
    and the assignment (no `await`, no thread hand-off), the same
    single-writer assumption a database transaction gets from its own
    isolation guarantees.
    """

    def __init__(self) -> None:
        self._state: PortfolioState | None = None
        self._revision: int | None = None

    def load(self) -> PortfolioRecord:
        if self._state is None or self._revision is None:
            raise PortfolioStoreError(
                "no PortfolioState has been initialized; call initialize() first"
            )
        return PortfolioRecord(state=self._state, revision=self._revision)

    def save(self, state: PortfolioState, *, expected_revision: int) -> PortfolioRecord:
        if not isinstance(state, PortfolioState):
            raise PortfolioStoreError(
                f"state must be a PortfolioState instance, got {type(state)!r}"
            )
        if self._state is None or self._revision is None:
            raise PortfolioStoreConcurrencyError(
                "no PortfolioState has been initialized; call initialize() first, not save()"
            )
        if expected_revision != self._revision:
            raise PortfolioStoreConcurrencyError(
                f"expected_revision ({expected_revision}) does not match the store's actual "
                f"current revision ({self._revision}); this write is based on a stale "
                "snapshot and was not applied"
            )
        self._state = state
        self._revision += 1
        return PortfolioRecord(state=self._state, revision=self._revision)

    def initialize(self, state: PortfolioState) -> PortfolioRecord:
        if self._state is not None:
            raise PortfolioStoreError(
                "a PortfolioState already exists; use save() to update it, not initialize()"
            )
        if not isinstance(state, PortfolioState):
            raise PortfolioStoreError(
                f"state must be a PortfolioState instance, got {type(state)!r}"
            )
        self._state = state
        self._revision = 0
        return PortfolioRecord(state=self._state, revision=self._revision)
