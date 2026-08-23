"""Abstract interface every AI reasoning provider must implement.

The rest of ORION depends only on this interface, never on a specific vendor's
SDK or response shape — swapping Claude for another model means writing a new
class here, not touching the pipeline.

Two invariants this module exists to enforce:

**A ReasoningOutput is a proposal, never a decision.** It is one opinion about
deterministic evidence. An independent, deterministic Risk Guardian gates every
proposal before it counts for anything, and may reject it outright. No caller of
this interface may act on a ReasoningOutput directly, treat it as final, or
route around that gate. There is no execution code path in this package.

**Failure is not a verdict.** When a provider cannot produce a validated
ReasoningOutput it raises ReasoningProviderError. It must never manufacture a
plausible-looking result instead — in particular, never a synthetic WAIT.
"I could not reason" and "I reasoned, and the answer is wait" are different
facts, and collapsing them would hide provider outages behind a stream of
sensible-looking abstentions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orion.analysis.evidence import Evidence
from orion.reasoning.schemas import ReasoningOutput


class ReasoningProviderError(RuntimeError):
    """Raised when a provider cannot return a validated ReasoningOutput.

    Never caught and papered over with a fabricated verdict — callers must treat
    this as "no opinion available" and let the decision layer handle it.
    """


class ReasoningProvider(ABC):
    """A source of structured reasoning over a single Evidence snapshot."""

    @abstractmethod
    def reason(self, evidence: Evidence) -> ReasoningOutput:
        """Turn one Evidence snapshot into a single structured proposal.

        The returned value is fully validated: enums are closed, confidence is
        within [0.0, 1.0], and `insufficient_data` implies `action=WAIT`. No raw
        model text escapes an implementation of this method.

        Must raise ReasoningProviderError (not return a placeholder, a default
        WAIT, or partially validated data) if the provider cannot fulfil the
        request for any reason — transport failure, refusal, or output that does
        not satisfy the schema.
        """
        raise NotImplementedError
