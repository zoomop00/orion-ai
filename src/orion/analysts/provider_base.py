"""Abstract interface the Macro Analyst provider must implement.

Mirrors `orion.reasoning.provider_base.ReasoningProvider` exactly: the rest
of ORION depends only on this interface, never a specific vendor's SDK —
swapping Claude for another model means writing a new class here, not
touching a caller. Macro Analyst output is advisory only and this package is
wired into nothing else: `orion.analysts` is never imported by `orion.
analysis`, `orion.decision`, `orion.risk`, `orion.paper`, `orion.memory`,
`orion.reflection`, or `orion.backtest`, and it never imports any of them
itself (see `tests/analysts/test_import_hygiene.py`). See `docs/DECISIONS.md`
for the full Mission 8 architecture.

**Failure is not a verdict**, matching `ReasoningProvider`'s identical
doctrine: when a provider cannot produce a validated `MacroAnalystOutput` it
raises `MacroAnalystProviderError`. It must never manufacture a plausible-
looking or "neutral" result instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orion.analysts.schemas import MacroAnalystOutput
from orion.research.models import EconomicCalendarSnapshot


class MacroAnalystProviderError(RuntimeError):
    """Raised when a provider cannot return a validated MacroAnalystOutput.

    Never caught and papered over with a fabricated or neutral result —
    callers must treat this as "no opinion available."
    """


class MacroAnalystProvider(ABC):
    """A source of structured macro-economic commentary over a batch of
    economic-calendar events. Advisory only."""

    @abstractmethod
    def analyze(self, snapshot: EconomicCalendarSnapshot) -> MacroAnalystOutput:
        """Turn one EconomicCalendarSnapshot into a single structured
        assessment.

        Callers are responsible for having already run `orion.research.
        validation.validate_calendar_snapshot` on `snapshot` — this method
        does not re-check data quality, mirroring `orion.analysis.evidence.
        build_evidence`'s identical precondition.

        Must raise `MacroAnalystProviderError` (never return a placeholder,
        a fabricated result, or a "neutral" default) if the provider cannot
        fulfil the request for any reason — empty input, oversized input,
        transport failure, refusal, or output that does not satisfy the
        schema.
        """
        raise NotImplementedError
