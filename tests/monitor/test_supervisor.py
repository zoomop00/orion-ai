"""The recurring supervisor: iteration count, sleeping, shutdown, failures.

NOT ONE OF THESE TESTS SLEEPS. `run_monitor` takes its wait as a parameter, so
the clock is driven by a list-appending fake and the whole file runs in
milliseconds. That is also why the supervisor takes a `Sleeper` at all: a loop
whose timing can only be observed by waiting for it is a loop nobody tests.

NOT ONE OF THEM TOUCHES A STORE, A JOURNAL OR A POSITION EITHER. The supervisor
sees a callable returning a bool and nothing else, which is what makes
"stateless between iterations" checkable here rather than argued about.
"""

from __future__ import annotations

import signal
from collections.abc import Callable

import pytest

from orion.monitor.supervisor import (
    MAX_CONSECUTIVE_FAILURES,
    MonitorStop,
    ShutdownSignal,
    install_shutdown_handlers,
    run_monitor,
)

INTERVAL = 60.0


class Sleeps:
    """A fake wait that records what it was asked for and never waits.

    `wake_on` names the call number (1-based) that should report a shutdown, so
    a test can put a signal *inside* the wait without any real timing.
    """

    def __init__(self, *, wake_on: int | None = None) -> None:
        self.seconds: list[float] = []
        self._wake_on = wake_on

    def __call__(self, seconds: float) -> bool:
        self.seconds.append(seconds)
        return self._wake_on is not None and len(self.seconds) == self._wake_on


def counting(results: list[bool]) -> Callable[[], bool]:
    """An iteration that returns each of `results` in turn, then keeps
    returning the last one. Records nothing else -- there is nothing else."""
    calls = iter(results)
    last = results[-1]

    def iteration() -> bool:
        nonlocal last
        last = next(calls, last)
        return last

    return iteration


class TestIterationCountAndSleeping:
    def test_it_runs_exactly_the_requested_number_of_iterations(self) -> None:
        runs = 0

        def iteration() -> bool:
            nonlocal runs
            runs += 1
            return True

        sleeps = Sleeps()
        stop = run_monitor(
            iteration=iteration,
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            max_iterations=3,
            sleeper=sleeps,
        )

        assert stop is MonitorStop.ITERATION_LIMIT
        assert runs == 3

    def test_it_sleeps_between_iterations_and_not_after_the_last(self) -> None:
        """Three iterations means two waits. A loop that slept after deciding to
        stop would delay every shutdown and every failure report by an
        interval."""
        sleeps = Sleeps()
        run_monitor(
            iteration=lambda: True,
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            max_iterations=3,
            sleeper=sleeps,
        )
        assert sleeps.seconds == [INTERVAL, INTERVAL]

    def test_a_single_iteration_never_sleeps(self) -> None:
        sleeps = Sleeps()
        run_monitor(
            iteration=lambda: True,
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            max_iterations=1,
            sleeper=sleeps,
        )
        assert sleeps.seconds == []

    def test_it_waits_the_configured_interval_exactly(self) -> None:
        sleeps = Sleeps()
        run_monitor(
            iteration=lambda: True,
            interval_seconds=137.5,
            shutdown=ShutdownSignal(),
            max_iterations=2,
            sleeper=sleeps,
        )
        assert sleeps.seconds == [137.5]


class TestShutdown:
    def test_a_flag_set_before_the_loop_runs_no_iteration_at_all(self) -> None:
        shutdown = ShutdownSignal()
        shutdown.request()
        runs = 0

        def iteration() -> bool:
            nonlocal runs
            runs += 1
            return True

        sleeps = Sleeps()
        stop = run_monitor(
            iteration=iteration,
            interval_seconds=INTERVAL,
            shutdown=shutdown,
            sleeper=sleeps,
        )

        assert stop is MonitorStop.SHUTDOWN
        assert runs == 0
        assert sleeps.seconds == []

    def test_the_current_iteration_completes_before_shutdown_is_honoured(self) -> None:
        """A signal arriving mid-iteration must not tear one in half: the
        account is written before the journal, and a process killed between
        those two leaves a real trade with no audit row."""
        shutdown = ShutdownSignal()
        finished: list[str] = []

        def iteration() -> bool:
            # The signal lands here, in the middle of the work.
            shutdown.request()
            finished.append("completed")
            return True

        sleeps = Sleeps()
        stop = run_monitor(
            iteration=iteration,
            interval_seconds=INTERVAL,
            shutdown=shutdown,
            sleeper=sleeps,
        )

        assert stop is MonitorStop.SHUTDOWN
        assert finished == ["completed"]
        # Stopped before waiting: a shutdown is never delayed by an interval.
        assert sleeps.seconds == []

    def test_shutdown_during_the_wait_stops_without_another_iteration(self) -> None:
        runs = 0

        def iteration() -> bool:
            nonlocal runs
            runs += 1
            return True

        sleeps = Sleeps(wake_on=2)
        stop = run_monitor(
            iteration=iteration,
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            sleeper=sleeps,
        )

        assert stop is MonitorStop.SHUTDOWN
        # Two iterations, two waits, and the second wait ended the loop.
        assert runs == 2
        assert len(sleeps.seconds) == 2

    def test_the_signal_is_idempotent(self) -> None:
        shutdown = ShutdownSignal()
        assert shutdown.requested is False
        shutdown.request()
        shutdown.request()
        assert shutdown.requested is True

    def test_installed_handlers_set_the_flag_rather_than_raising(self) -> None:
        """SIGINT's default raises `KeyboardInterrupt` at whatever line is
        executing, which could be between the account write and the journal
        write. The handler must set a flag instead."""
        shutdown = ShutdownSignal()
        previous_int = signal.getsignal(signal.SIGINT)
        previous_term = signal.getsignal(signal.SIGTERM)
        try:
            install_shutdown_handlers(shutdown)
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            handler(signal.SIGINT, None)  # no KeyboardInterrupt
            assert shutdown.requested is True
            assert signal.getsignal(signal.SIGTERM) is handler
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)


class TestFailureCeiling:
    def test_one_failure_does_not_stop_the_loop(self) -> None:
        sleeps = Sleeps()
        stop = run_monitor(
            iteration=counting([False, True, True]),
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            max_iterations=3,
            sleeper=sleeps,
        )
        assert stop is MonitorStop.ITERATION_LIMIT

    def test_three_consecutive_failures_stop_the_loop(self) -> None:
        runs = 0

        def iteration() -> bool:
            nonlocal runs
            runs += 1
            return False

        sleeps = Sleeps()
        stop = run_monitor(
            iteration=iteration,
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            sleeper=sleeps,
        )

        assert stop is MonitorStop.FAILURE_CEILING
        assert runs == MAX_CONSECUTIVE_FAILURES == 3
        # Two waits, not three: the loop stops before sleeping again.
        assert len(sleeps.seconds) == 2

    def test_a_success_resets_the_consecutive_count(self) -> None:
        """A lost race against another writer heals itself on the next pass, so
        a run of failures broken by a success must not accumulate towards the
        ceiling -- only an unbroken run means something is really wrong."""
        sleeps = Sleeps()
        stop = run_monitor(
            iteration=counting([False, False, True, False, False, True]),
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            max_iterations=6,
            sleeper=sleeps,
        )
        assert stop is MonitorStop.ITERATION_LIMIT

    def test_the_ceiling_is_reached_only_by_an_unbroken_run(self) -> None:
        sleeps = Sleeps()
        stop = run_monitor(
            iteration=counting([False, False, True, False, False, False]),
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            sleeper=sleeps,
        )
        assert stop is MonitorStop.FAILURE_CEILING

    def test_the_ceiling_is_configurable_for_a_caller_that_needs_it(self) -> None:
        stop = run_monitor(
            iteration=lambda: False,
            interval_seconds=INTERVAL,
            shutdown=ShutdownSignal(),
            max_consecutive_failures=1,
            sleeper=Sleeps(),
        )
        assert stop is MonitorStop.FAILURE_CEILING


class TestUnexpectedExceptions:
    def test_an_exception_from_an_iteration_propagates(self) -> None:
        """An operational failure is already a `False`. Anything that raises is
        a defect, and swallowing it would turn a bug into an infinite loop that
        looks healthy."""

        def iteration() -> bool:
            raise RuntimeError("a defect, not an outcome")

        with pytest.raises(RuntimeError, match="a defect, not an outcome"):
            run_monitor(
                iteration=iteration,
                interval_seconds=INTERVAL,
                shutdown=ShutdownSignal(),
                sleeper=Sleeps(),
            )

    def test_an_exception_stops_immediately_rather_than_counting_as_a_failure(self) -> None:
        runs = 0

        def iteration() -> bool:
            nonlocal runs
            runs += 1
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            run_monitor(
                iteration=iteration,
                interval_seconds=INTERVAL,
                shutdown=ShutdownSignal(),
                sleeper=Sleeps(),
            )
        assert runs == 1


def test_the_supervisor_retains_no_domain_state_between_iterations() -> None:
    """Structural. The loop's whole contract is `Callable[[], bool]`, so there
    is no parameter, attribute or closure on this side of the seam through
    which a store, a journal, a portfolio or a revision could persist."""
    import ast
    import inspect

    import orion.monitor.supervisor as supervisor_module

    source = inspect.getsource(supervisor_module)
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_monitor"
    )
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    for forbidden in ("store", "journal", "record", "portfolio", "revision", "price", "cycle"):
        assert forbidden not in names
