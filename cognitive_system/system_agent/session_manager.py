from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionState(str, Enum):
    INACTIVE = "inactive"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class SessionSnapshot:
    session_id: Optional[str]
    mode: str
    state: str
    elapsed_time: float
    remaining_time: float
    duration: int
    recording_active: bool

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "state": self.state,
            "elapsed_time": round(self.elapsed_time, 1),
            "remaining_time": round(self.remaining_time, 1),
            "duration": self.duration,
            "recording_active": self.recording_active,
        }


class SessionManager:
    """Single source of truth for session timing and recording state."""

    def __init__(self, mode: str, duration_minutes: int):
        self._mode = mode
        self._duration_seconds = max(1, int(duration_minutes * 60))
        self._session_id: Optional[str] = None
        self._start_monotonic: Optional[float] = None
        self._active = False

        self._browser_foreground = False
        self._recording_active = False
        self._recording_started_once = False

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def active(self) -> bool:
        return self._active

    def start_session(self) -> str:
        if self._active:
            raise RuntimeError("Session already active.")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_id = f"session_{ts}_{uuid.uuid4().hex[:6]}"
        self._start_monotonic = time.monotonic()
        self._active = True

        self._recording_active = False
        self._recording_started_once = False
        return self._session_id

    def stop_session(self) -> Optional[str]:
        if not self._active:
            return None
        session_id = self._session_id
        self._active = False
        self._session_id = None
        self._start_monotonic = None
        self._recording_active = False
        self._recording_started_once = False
        return session_id

    def set_browser_foreground(self, is_foreground: bool) -> Optional[str]:
        """
        Update browser foreground status.
        Returns an extension command when recording state must change:
        start_recording / resume_recording / pause_recording.
        """
        self._browser_foreground = bool(is_foreground)
        if not self._active:
            return None

        if self._browser_foreground and not self._recording_active:
            self._recording_active = True
            if not self._recording_started_once:
                self._recording_started_once = True
                return "start_recording"
            return "resume_recording"

        if not self._browser_foreground and self._recording_active:
            self._recording_active = False
            return "pause_recording"

        return None

    def get_elapsed(self) -> float:
        if not self._active or self._start_monotonic is None:
            return 0.0
        return max(0.0, time.monotonic() - self._start_monotonic)

    def get_remaining(self) -> float:
        return max(0.0, self._duration_seconds - self.get_elapsed())

    def is_expired(self) -> bool:
        return self._active and self.get_elapsed() >= self._duration_seconds

    def snapshot(self) -> SessionSnapshot:
        if not self._active:
            return SessionSnapshot(
                session_id=None,
                mode=self._mode,
                state=SessionState.INACTIVE.value,
                elapsed_time=0.0,
                remaining_time=0.0,
                duration=self._duration_seconds,
                recording_active=False,
            )

        state = SessionState.RUNNING.value if self._recording_active else SessionState.PAUSED.value
        return SessionSnapshot(
            session_id=self._session_id,
            mode=self._mode,
            state=state,
            elapsed_time=self.get_elapsed(),
            remaining_time=self.get_remaining(),
            duration=self._duration_seconds,
            recording_active=self._recording_active,
        )

