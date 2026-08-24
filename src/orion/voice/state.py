"""The Mission 9 Phase 3 voice-runtime state machine: the one place any code
in this codebase is allowed to change `VoiceState` (`orion.voice.schemas`).
No second `VoiceState`-like enum exists anywhere in `orion.voice` -- this
module only sequences the one defined in Phase 1.

`_ALLOWED_TRANSITIONS` is the complete state graph, deliberately narrower
than "any state to any state":

    IDLE -> LISTENING -> UNDERSTANDING -> ANALYZING -> SPEAKING -> IDLE

plus one extra edge out of every non-`IDLE` state, straight back to `IDLE`.
That second edge is not a shortcut through the pipeline -- it is the single,
uniform failure-recovery path: whatever stage of a turn fails (microphone,
STT, response generation, TTS, or playback), returning to `IDLE` is always
one valid transition away, which is what makes "no runtime path can leave
ORION permanently stuck in a non-`IDLE` state" a property of this graph's
shape rather than something every call site has to remember to enforce.
`orion.voice.runtime.VoiceRuntime.run_turn` relies on exactly this: its one
`except Exception: ... reset_to_idle()` handler works from any stage because
every stage's state already has an `IDLE` edge.

Forward progression only moves one step at a time -- `IDLE` cannot jump
straight to `SPEAKING`, and `LISTENING` cannot skip `UNDERSTANDING` -- any
other target raises `InvalidVoiceStateTransitionError` instead of silently
coercing or ignoring the request, matching the "fail-closed, no partial
recovery" doctrine already used throughout `orion.risk`/`orion.decision`.
"""

from __future__ import annotations

from orion.voice.schemas import VoiceState

_ALLOWED_TRANSITIONS: dict[VoiceState, frozenset[VoiceState]] = {
    VoiceState.IDLE: frozenset({VoiceState.LISTENING}),
    VoiceState.LISTENING: frozenset({VoiceState.UNDERSTANDING, VoiceState.IDLE}),
    VoiceState.UNDERSTANDING: frozenset({VoiceState.ANALYZING, VoiceState.IDLE}),
    VoiceState.ANALYZING: frozenset({VoiceState.SPEAKING, VoiceState.IDLE}),
    VoiceState.SPEAKING: frozenset({VoiceState.IDLE}),
}


class InvalidVoiceStateTransitionError(RuntimeError):
    """Raised when a requested transition is not in `_ALLOWED_TRANSITIONS`.

    `current`/`target` are kept as attributes (not just interpolated into
    the message) so a caller -- or a test -- can inspect exactly which edge
    was rejected without parsing a string.
    """

    def __init__(self, current: VoiceState, target: VoiceState) -> None:
        super().__init__(f"cannot transition from {current.value!r} to {target.value!r}")
        self.current = current
        self.target = target


class VoiceStateMachine:
    """Mutable holder of exactly one `VoiceState`, starting at `IDLE`.

    Deliberately not thread-safe and not reentrant -- `orion.voice.runtime.
    VoiceRuntime` is a single synchronous session object, and adding locking
    here with no concurrent caller yet would be exactly the kind of
    speculative complexity this phase's scope explicitly avoids.
    """

    def __init__(self) -> None:
        self._state = VoiceState.IDLE

    @property
    def state(self) -> VoiceState:
        return self._state

    def transition(self, target: VoiceState) -> VoiceState:
        """Move to `target` if `(state, target)` is an edge in the graph.

        Raises InvalidVoiceStateTransitionError otherwise -- the state is
        left unchanged on rejection, never partially applied.
        """
        allowed = _ALLOWED_TRANSITIONS[self._state]
        if target not in allowed:
            raise InvalidVoiceStateTransitionError(self._state, target)
        self._state = target
        return self._state

    def reset_to_idle(self) -> VoiceState:
        """Return to `IDLE`, the uniform recovery path from any state.

        Idempotent: a no-op when already `IDLE`, otherwise routed through
        `transition` like every other move -- recovery is not a special
        bypass of the graph, it is simply always a valid edge in it (see
        the module docstring).
        """
        if self._state is not VoiceState.IDLE:
            self.transition(VoiceState.IDLE)
        return self._state
