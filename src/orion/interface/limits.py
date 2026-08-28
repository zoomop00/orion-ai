"""Process-local containment for the two expensive turn endpoints.

WHY THIS EXISTS. `orion.interface.app`'s own docstring has said since Mission
10 Phase 2 that this service has "no authentication, no authorization, no rate
limiting and no TLS", and that loopback is therefore the security boundary.
Loopback bounds *who* can call; it bounds nothing about *how often*. One turn
fans out to as many as four third-party calls — Deepgram speech-to-text, Twelve
Data bars, Anthropic reasoning, Deepgram speech synthesis — three of them to
metered vendors. Until this module, the only ceiling on that fan-out was
AnyIO's default worker-thread limiter, which is 40 and belongs to the framework
rather than to ORION. A single local process could therefore hold 40 turns in
flight and, on the fast path, drive roughly forty paid provider calls per
second. That is the Mission 12 audit's P0 finding, and this module is the whole
of Mission 12.1's answer to it.

TWO CEILINGS, TWO DIFFERENT REFUSALS, BOTH ALREADY IN THE VOCABULARY.
`MAX_CONCURRENT_TURNS` bounds how many turns may be *in flight at once*, and
saturating it is exactly what `VoiceInterfaceFault.RUNTIME_BUSY` already
describes — "ORION is already handling a turn", HTTP 409. `MAX_TURNS_PER_WINDOW`
bounds how many turns may *start* within `WINDOW_SECONDS`, and that needed one
new member: refusing a request because thirty turns have already run this
minute is not the same statement as "ORION is busy right now", and reusing the
busy fault would have made the interface say something untrue while handling
zero turns. `RATE_LIMITED` is that one addition — a fixed sentence and a 429,
built the same way every other fault is, leaving the vocabulary closed.

A FIXED WINDOW, NOT A SLIDING ONE OR A TOKEN BUCKET. A fixed window is two
integers and a float: it cannot grow, cannot leak, needs no background task, no
eviction policy and no per-client bookkeeping. A sliding window would need a
timestamp deque per client and a rule for trimming it; a token bucket would need
a refill schedule. Neither buys anything a single-user localhost service can
observe, and both would be more state to reason about. The known cost of a fixed
window is the boundary burst — up to `2 * MAX_TURNS_PER_WINDOW` turns can start
across two adjacent windows — which is accepted here because the concurrency
ceiling still bounds what those turns can do at any instant.

NO PER-CLIENT KEYING, AND THAT IS DELIBERATE. There is no IP map, no session, no
header inspection and no identity of any kind. The limits are per *process*.
On a loopback service with no authentication there is no trustworthy client
identity to key on, and inventing one — hashing a source address, minting a
cookie — would add tracking state to a service whose interface promises the user
that nothing is recorded. Per-process is the honest unit here, and it is also
the unit that actually bounds vendor spend.

EVERYTHING IS IN MEMORY, AND RESTARTING RESETS IT. Three counters and a
monotonic timestamp on one object owned by one `FastAPI` app. Nothing is
written to disk, no file is opened, no database is touched and no environment
variable is read. `orion.interface`'s zero-persistence guarantee is asserted
against the filesystem by `tests/interface/test_security.py`, and this module
must not be the thing that breaks it.

THE LIMITS ARE CONSTANTS, NOT CONFIGURATION. They are deliberately not read
from `Settings` or the environment. A security ceiling that can be raised by
editing `.env` on the machine being protected is not a ceiling; it is a default.
The constructor accepts them only so tests can drive small, fast limits, and
`create_app` passes none — a test asserts the app's limiter uses exactly the
constants below, and another asserts no request field can reach them.

FAIL CLOSED. Every rejection path raises `VoiceInterfaceError`, which the
existing handlers render as a fixed sentence with no detail. A slot is released
in a `finally`, so a provider blowing up mid-turn frees capacity rather than
leaking it — a limiter that leaks slots under failure eventually refuses
everything, which is a denial of service wearing a safety feature's clothes.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import monotonic

from orion.interface.service import VoiceInterfaceError, VoiceInterfaceFault

#: How many turns may execute at once in this process.
#:
#: Two, not one: the frontend already refuses to start a turn while one is
#: running, so the realistic steady state is a single turn, and a ceiling of one
#: would turn an ordinary overlap — a previous request still draining while the
#: user starts the next — into a spurious refusal. Two is the smallest number
#: that tolerates that overlap, and it is twenty times tighter than the AnyIO
#: default it replaces.
MAX_CONCURRENT_TURNS = 2

#: How many turns may *start* within one window.
#:
#: A spoken turn takes several seconds of real vendor latency, so thirty a
#: minute is far beyond any human pace while still capping the worst case at a
#: number an operator can afford to be surprised by: at most 30 turns/minute
#: times four provider calls, against roughly 2,400 calls/minute before.
MAX_TURNS_PER_WINDOW = 30

#: The window length, in seconds.
WINDOW_SECONDS = 60.0


class TurnLimiter:
    """Bounds concurrent and per-window turn starts for one process.

    Not a general-purpose rate limiter and deliberately not written as one:
    there is no client identity, no storage backend, no distribution and no
    configuration surface. It is a lock, three counters and a monotonic clock.
    """

    def __init__(
        self,
        *,
        max_concurrent: int = MAX_CONCURRENT_TURNS,
        max_per_window: int = MAX_TURNS_PER_WINDOW,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        # Fail closed on a nonsensical ceiling rather than constructing a
        # limiter that permits everything. A zero or negative limit is far more
        # likely to be a mistake than an intent to disable protection, and this
        # is the only place such a value could enter.
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if max_per_window < 1:
            raise ValueError("max_per_window must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self._max_concurrent = max_concurrent
        self._max_per_window = max_per_window
        self._window_seconds = window_seconds
        # `monotonic`, never `time()`: a wall clock that steps backwards over an
        # NTP correction or a daylight-saving change would silently widen the
        # window. Monotonic time cannot go backwards.
        self._clock = clock

        self._lock = threading.Lock()
        self._active = 0
        self._window_started = clock()
        self._window_count = 0

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Hold a turn slot for the duration of the block, or refuse.

        Raises `VoiceInterfaceError` *before* yielding, so a refused request
        never enters the block and therefore never reaches a provider. The
        release is in a `finally`, so an exception raised inside the block —
        a vendor timeout, a malformed provider response, anything — still
        returns the slot.
        """
        self._acquire()
        try:
            yield
        finally:
            self._release()

    def _acquire(self) -> None:
        with self._lock:
            now = self._clock()
            if now - self._window_started >= self._window_seconds:
                self._window_started = now
                self._window_count = 0

            # Window first: if the minute's budget is spent, that is the true
            # reason, even when the process also happens to be busy.
            if self._window_count >= self._max_per_window:
                raise VoiceInterfaceError(VoiceInterfaceFault.RATE_LIMITED)
            if self._active >= self._max_concurrent:
                raise VoiceInterfaceError(VoiceInterfaceFault.RUNTIME_BUSY)

            # Both counters move only once both checks have passed, so a
            # request refused for concurrency does not also spend a window
            # token it never used.
            self._window_count += 1
            self._active += 1

    def _release(self) -> None:
        with self._lock:
            if self._active > 0:
                self._active -= 1
            # `_window_count` is deliberately *not* decremented. The window
            # counts turns started, not turns running; decrementing it here
            # would turn the whole mechanism back into a second concurrency
            # limit and remove the burst ceiling entirely.
