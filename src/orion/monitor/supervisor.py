"""The recurring supervisor: the only loop and the only sleep in `src/orion/`.

WHY IT KNOWS NOTHING ABOUT TRADING. `run_monitor` takes a callable that returns
`True` when an iteration made progress and `False` when it did not, and that is
the entire contract. It never sees a `PortfolioStore`, a `CycleResult`, a price
or a position, so there is no field on this side of the seam for state to hide
in — "stateless between iterations" is a property of the type signature, not a
discipline someone has to keep. It also makes the loop testable without a
database, a vendor or a clock.

THERE IS NO RETRY IN HERE, AND THE DIFFERENCE MATTERS. A failed iteration is not
attempted again; the next scheduled iteration does fresh work from the
authoritative store. That is what lets one counter tell two very different
things apart without inspecting a single exception: a lost race against another
writer *heals itself* on the next pass (the position is simply gone, and the
pass reports nothing to resolve), while a broken disk or a dead vendor does not.
So consecutive failures are the signal, a single success clears them, and the
ceiling is reached only by something that genuinely is not getting better.

SHUTDOWN NEVER INTERRUPTS AN ITERATION. `orion.paper.session._persist` writes
the account and then the journal, and a process torn down between those two
leaves a real trade with no audit row. So the shutdown flag is only ever read
*between* iterations and during the wait — never inside one — and a signal
arriving mid-iteration is honoured the moment that iteration returns. This is
also why the handler installed below sets a flag instead of letting Python's
default `SIGINT` raise `KeyboardInterrupt`, which could land anywhere, including
between those two writes.

NOTHING IS SCHEDULED, DAEMONISED OR BACKGROUNDED. This is a foreground loop in
the caller's own process, entered only because someone typed `orion monitor`.
It starts no thread, forks nothing, and returns a value like any other function.
Process supervision -- restarts, log capture, health -- belongs to whatever runs
it, and to Mission 13.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from enum import StrEnum
from types import FrameType

#: Consecutive failed iterations before the supervisor stops. Three, because a
#: single failure is ordinary (a vendor blip, a lost race) and a run of them is
#: not. A success anywhere in between clears the count.
MAX_CONSECUTIVE_FAILURES = 3

#: How the supervisor waits, so tests never sleep in real time. Returns `True`
#: if shutdown was requested during the wait, `False` if the full interval
#: elapsed -- the same contract `ShutdownSignal.wait` has.
Sleeper = Callable[[float], bool]


class MonitorStop(StrEnum):
    """Why the loop ended. Mapped to an exit code by the caller, which owns the
    exit-code vocabulary -- this module deliberately does not import it."""

    #: A signal asked it to stop, or the flag was set before it began.
    SHUTDOWN = "shutdown"
    #: A caller-imposed iteration bound was reached. Not a failure.
    ITERATION_LIMIT = "iteration_limit"
    #: `max_consecutive_failures` iterations failed in a row.
    FAILURE_CEILING = "failure_ceiling"


class ShutdownSignal:
    """A flag a signal handler can set, and a wait that wakes when it is.

    Built on `threading.Event` for its interruptible wait, not to start a
    thread -- none is started anywhere in this module. `Event.set()` releases
    anything blocked in `wait()`, so a handler running in the main thread wakes
    the same thread's sleep immediately rather than after the full interval.
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        """Ask the loop to stop after the current iteration. Idempotent."""
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Wait up to `seconds`. `True` if shutdown was requested during it."""
        return self._event.wait(seconds)


def install_shutdown_handlers(shutdown: ShutdownSignal) -> None:
    """Route SIGINT and SIGTERM to `shutdown` instead of the default behaviour.

    SIGINT's default raises `KeyboardInterrupt` at whatever line is executing,
    which for this loop could be between the account write and the journal
    write. Setting a flag instead means the interrupt is honoured at the next
    boundary and never inside an iteration -- the difference between a clean
    stop and a trade with no audit row.

    Only callable from the main thread of the main interpreter, which is where
    a console entrypoint runs; `signal.signal` raises otherwise, and that is
    left to propagate rather than being hidden.
    """

    def _handle(signum: int, frame: FrameType | None) -> None:
        shutdown.request()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def run_monitor(
    *,
    iteration: Callable[[], bool],
    interval_seconds: float,
    shutdown: ShutdownSignal,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    max_iterations: int | None = None,
    sleeper: Sleeper | None = None,
) -> MonitorStop:
    """Run `iteration` every `interval_seconds` until something says stop.

    `iteration` returns `True` for progress and `False` for a failure. It is
    called with no arguments and its result is the only thing read from it, so
    everything about *what* an iteration does -- and every object it touches --
    stays on the caller's side of this seam.

    THE ORDER OF THE CHECKS IS THE CONTRACT:

        stop if shutdown was already requested   (no iteration begins)
        run one iteration                        (never interrupted)
        stop if the failure ceiling is reached   (before sleeping)
        stop if the iteration bound is reached   (before sleeping)
        stop if shutdown was requested during it (before sleeping)
        sleep, and stop if shutdown arrives      (waking immediately)

    Every exit is therefore taken *before* a sleep, so a loop that is stopping
    never waits an interval first, and a signal is never sat on for the length
    of one.

    UNEXPECTED EXCEPTIONS ARE NOT CAUGHT. An operational failure is a `False`
    return; anything that raises out of `iteration` is a bug, and it propagates
    with its traceback intact rather than being counted as a failure and
    retried forever. Nothing here swallows an exception.
    """
    wait = sleeper if sleeper is not None else shutdown.wait
    completed = 0
    consecutive_failures = 0

    while True:
        if shutdown.requested:
            return MonitorStop.SHUTDOWN

        # Never wrapped in `try`: see the docstring. An operational failure is
        # already a `False`, so an exception here is a defect, not an outcome.
        progressed = iteration()
        completed += 1

        consecutive_failures = 0 if progressed else consecutive_failures + 1
        if consecutive_failures >= max_consecutive_failures:
            return MonitorStop.FAILURE_CEILING

        if max_iterations is not None and completed >= max_iterations:
            return MonitorStop.ITERATION_LIMIT

        if shutdown.requested:
            return MonitorStop.SHUTDOWN

        if wait(interval_seconds):
            return MonitorStop.SHUTDOWN
