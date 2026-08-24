from __future__ import annotations

import pytest

from orion.voice.schemas import VoiceState
from orion.voice.state import InvalidVoiceStateTransitionError, VoiceStateMachine

FORWARD_SEQUENCE = [
    VoiceState.LISTENING,
    VoiceState.UNDERSTANDING,
    VoiceState.ANALYZING,
    VoiceState.SPEAKING,
    VoiceState.IDLE,
]


def test_initial_state_is_idle() -> None:
    assert VoiceStateMachine().state is VoiceState.IDLE


def test_complete_lifecycle_forward_sequence() -> None:
    machine = VoiceStateMachine()
    for target in FORWARD_SEQUENCE:
        assert machine.transition(target) is target
    assert machine.state is VoiceState.IDLE


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (VoiceState.IDLE, VoiceState.UNDERSTANDING),
        (VoiceState.IDLE, VoiceState.ANALYZING),
        (VoiceState.IDLE, VoiceState.SPEAKING),
        (VoiceState.LISTENING, VoiceState.ANALYZING),
        (VoiceState.LISTENING, VoiceState.SPEAKING),
        (VoiceState.UNDERSTANDING, VoiceState.LISTENING),
        (VoiceState.UNDERSTANDING, VoiceState.SPEAKING),
        (VoiceState.ANALYZING, VoiceState.LISTENING),
        (VoiceState.ANALYZING, VoiceState.UNDERSTANDING),
        (VoiceState.SPEAKING, VoiceState.LISTENING),
        (VoiceState.SPEAKING, VoiceState.UNDERSTANDING),
        (VoiceState.SPEAKING, VoiceState.ANALYZING),
    ],
)
def test_invalid_transitions_fail_closed(current: VoiceState, target: VoiceState) -> None:
    machine = VoiceStateMachine()
    if current is not VoiceState.IDLE:
        # Walk forward to `current` (inclusive) via valid edges first.
        for step in FORWARD_SEQUENCE:
            machine.transition(step)
            if step is current:
                break

    with pytest.raises(InvalidVoiceStateTransitionError) as exc_info:
        machine.transition(target)

    assert exc_info.value.current is current
    assert exc_info.value.target is target
    assert machine.state is current  # rejected transition leaves state unchanged


def test_idle_cannot_transition_to_idle() -> None:
    machine = VoiceStateMachine()
    with pytest.raises(InvalidVoiceStateTransitionError):
        machine.transition(VoiceState.IDLE)


@pytest.mark.parametrize(
    "reachable_state",
    [VoiceState.LISTENING, VoiceState.UNDERSTANDING, VoiceState.ANALYZING, VoiceState.SPEAKING],
)
def test_every_non_idle_state_can_recover_to_idle(reachable_state: VoiceState) -> None:
    machine = VoiceStateMachine()
    for step in FORWARD_SEQUENCE:
        machine.transition(step)
        if step is reachable_state:
            break

    assert machine.reset_to_idle() is VoiceState.IDLE
    assert machine.state is VoiceState.IDLE


def test_reset_to_idle_is_idempotent_when_already_idle() -> None:
    machine = VoiceStateMachine()
    assert machine.reset_to_idle() is VoiceState.IDLE
    assert machine.state is VoiceState.IDLE


def test_repeated_full_lifecycles_are_independent() -> None:
    machine = VoiceStateMachine()
    for _ in range(5):
        assert machine.state is VoiceState.IDLE
        for target in FORWARD_SEQUENCE:
            machine.transition(target)
        assert machine.state is VoiceState.IDLE


def test_error_message_names_both_states() -> None:
    machine = VoiceStateMachine()
    with pytest.raises(InvalidVoiceStateTransitionError, match="idle.*speaking"):
        machine.transition(VoiceState.SPEAKING)
