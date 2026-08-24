"""Response-provider boundary for Mission 9 Phase 3: `ResponseProvider.
respond(transcript: Transcript) -> str` is the one, narrow seam between
"the user said something" and "ORION has something to say back." It exists
so `orion.voice.runtime.VoiceRuntime` never talks to a specific response
strategy directly -- swapping in a real reasoning/orchestrator-backed
provider later means writing a new class here, matching every other
provider boundary in this codebase.

This phase deliberately does NOT build that real strategy. Connecting
`transcript.text` to trading, tools, execution, shell commands, memory, a
database, or full autonomous reasoning is explicit future orchestrator
work (`ORION_MASTER_ENGINEERING_BRIEF_V2.md` §15's `UNDERSTANDING`/
`ANALYZING` states are not designed here, matching Mission 9 Phase 1's
identical deferral) -- see `docs/DECISIONS.md`.

`StaticResponseProvider` is the one concrete implementation this phase
ships: a fixed response, completely independent of `transcript.text`'s
content. This is a deliberate security/simplicity choice, not a missing
feature -- `transcript.text` is untrusted data (see `orion.voice.schemas`'s
module docstring), and the strongest available guarantee that it "travels
through the runtime as inert data" is a response provider that never reads
it at all, rather than one that reads it carefully. A future
transcript-aware provider is welcome to inspect `transcript.text`, but it
will need its own explicit defense (fencing, bounded output, no tool
access) the same way `orion.analysts.claude_macro_provider` already does
for untrusted economic-calendar text -- this module does not attempt to
solve that problem preemptively.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orion.voice.schemas import Transcript


class ResponseProviderError(RuntimeError):
    """Raised when a response cannot be produced for a Transcript.

    Never caught and papered over with an empty or fabricated response --
    matching every provider error in this codebase's identical "failure is
    not a verdict" doctrine (see `orion.voice.provider_base`).
    """


class ResponseProvider(ABC):
    """A source of one response string for one Transcript."""

    @abstractmethod
    def respond(self, transcript: Transcript) -> str:
        """Turn `transcript` into one response string to be spoken.

        Must raise ResponseProviderError (never return an empty string) if
        no response can be produced.
        """
        raise NotImplementedError


DEFAULT_RESPONSE_TEXT = (
    "Hello. I'm ORION. I heard you, but full reasoning isn't connected in this build yet."
)


class StaticResponseProvider(ResponseProvider):
    """Always returns the same fixed response text, regardless of
    `transcript`'s content -- see the module docstring for why that is a
    deliberate security/simplicity choice for this phase, not a stub
    waiting to be filled in carelessly.

    Cannot fail: `respond` never raises, since it does no work that could
    fail. `ResponseProviderError` remains part of the ABC's contract for
    future, genuinely-fallible response providers.
    """

    def __init__(self, response_text: str = DEFAULT_RESPONSE_TEXT) -> None:
        if not response_text:
            raise ResponseProviderError("response_text must not be empty")
        self._response_text = response_text

    def respond(self, transcript: Transcript) -> str:
        # transcript is intentionally unread -- see the module/class
        # docstrings. Accepting it (rather than a no-arg method) keeps this
        # implementation's signature identical to every other
        # ResponseProvider, including a future one that does read it.
        del transcript
        return self._response_text
