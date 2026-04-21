"""Deterministic session state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Dict, Set


class SessionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class InvalidTransitionError(RuntimeError):
    """Raised when an invalid state transition is requested."""


@dataclass
class TransitionResult:
    previous: SessionState
    current: SessionState


class SessionStateMachine:
    """Single deterministic state machine used by the system agent."""

    _TRANSITIONS: Dict[SessionState, Set[SessionState]] = {
        SessionState.IDLE: {SessionState.RUNNING},
        SessionState.RUNNING: {SessionState.PAUSED, SessionState.STOPPED},
        SessionState.PAUSED: {SessionState.RUNNING, SessionState.STOPPED},
        SessionState.STOPPED: {SessionState.IDLE},
    }

    def __init__(self) -> None:
        self._state = SessionState.IDLE
        self._lock = Lock()

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    def transition(self, target: SessionState) -> TransitionResult:
        with self._lock:
            current = self._state
            allowed = self._TRANSITIONS.get(current, set())
            if target not in allowed:
                raise InvalidTransitionError(f"Invalid transition: {current.value} -> {target.value}")
            self._state = target
            return TransitionResult(previous=current, current=target)

    def reset(self) -> TransitionResult:
        with self._lock:
            current = self._state
            if current != SessionState.STOPPED:
                raise InvalidTransitionError(
                    f"Reset only allowed from stopped state, current={current.value}"
                )
            self._state = SessionState.IDLE
            return TransitionResult(previous=current, current=self._state)

