"""Abstract interface for persisting/reconstructing paper-account state.

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


class PortfolioStore(ABC):
    """The current paper-account snapshot for a single symbol."""

    @abstractmethod
    def load(self) -> PortfolioState:
        """Return the current `PortfolioState`.

        Must raise PortfolioStoreError (never return a fabricated default)
        if no state has been initialized yet — a caller that wants a fresh
        account must call `initialize` explicitly.
        """
        raise NotImplementedError

    @abstractmethod
    def save(self, state: PortfolioState) -> None:
        """Persist `state` as the new current snapshot, replacing whatever
        was stored before. `orion.paper` is the only layer permitted to
        call this — `orion.risk` never writes to a store."""
        raise NotImplementedError

    @abstractmethod
    def initialize(self, state: PortfolioState) -> None:
        """Set the current snapshot for the first time.

        Must raise PortfolioStoreError if a snapshot already exists —
        re-initializing a live account is a distinct, deliberate operation
        this interface does not silently allow."""
        raise NotImplementedError


class InMemoryPortfolioStore(PortfolioStore):
    """The Mission 4 implementation: process-lifetime state only, held in a
    single private attribute. State does not survive a process restart, by
    design — a durable implementation is Mission 2's job, behind this same
    interface (see the module docstring).
    """

    def __init__(self) -> None:
        self._state: PortfolioState | None = None

    def load(self) -> PortfolioState:
        if self._state is None:
            raise PortfolioStoreError(
                "no PortfolioState has been initialized; call initialize() first"
            )
        return self._state

    def save(self, state: PortfolioState) -> None:
        if not isinstance(state, PortfolioState):
            raise PortfolioStoreError(
                f"state must be a PortfolioState instance, got {type(state)!r}"
            )
        self._state = state

    def initialize(self, state: PortfolioState) -> None:
        if self._state is not None:
            raise PortfolioStoreError(
                "a PortfolioState already exists; use save() to update it, not initialize()"
            )
        self.save(state)
