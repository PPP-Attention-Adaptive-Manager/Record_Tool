import uuid
import time
import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class SessionState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ENDED = "ended"


class SessionManager:
    def __init__(self, config, broadcast_callback: Callable):
        self.config = config
        self.broadcast_callback = broadcast_callback

        self.session_id: Optional[str] = None
        self.state = SessionState.IDLE
        self.start_time: Optional[float] = None
        self.pause_time: Optional[float] = None
        self.paused_duration: float = 0.0
        self.duration_seconds: float = config.session_duration_minutes * 60.0

        self._broadcast_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> str:
        if self.state != SessionState.IDLE:
            raise RuntimeError(f"Cannot start: current state is {self.state.value}")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = f"session_{ts}_{uuid.uuid4().hex[:6]}"
        self.start_time = time.monotonic()
        self.paused_duration = 0.0
        self.pause_time = None
        self.state = SessionState.RUNNING
        logger.info(f"Session started: {self.session_id}")
        return self.session_id

    def pause(self):
        if self.state != SessionState.RUNNING:
            raise RuntimeError(f"Cannot pause: current state is {self.state.value}")
        self.pause_time = time.monotonic()
        self.state = SessionState.PAUSED
        logger.info(f"Session paused: {self.session_id}")

    def resume(self):
        if self.state != SessionState.PAUSED:
            raise RuntimeError(f"Cannot resume: current state is {self.state.value}")
        self.paused_duration += time.monotonic() - self.pause_time
        self.pause_time = None
        self.state = SessionState.RUNNING
        logger.info(f"Session resumed: {self.session_id}")

    def stop(self):
        if self.state not in (SessionState.RUNNING, SessionState.PAUSED):
            raise RuntimeError(f"Cannot stop: current state is {self.state.value}")
        self.state = SessionState.ENDED
        logger.info(f"Session ended: {self.session_id}")

    # ------------------------------------------------------------------
    # Timing helpers (single source of truth)
    # ------------------------------------------------------------------

    def get_elapsed(self) -> float:
        if self.start_time is None:
            return 0.0
        now = time.monotonic()
        if self.state == SessionState.PAUSED:
            active = self.pause_time - self.start_time - self.paused_duration
        else:
            active = now - self.start_time - self.paused_duration
        return max(0.0, active)

    def get_remaining(self) -> float:
        return max(0.0, self.duration_seconds - self.get_elapsed())

    def is_expired(self) -> bool:
        return self.state == SessionState.RUNNING and self.get_remaining() <= 0.0

    def get_status_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "elapsed_time": round(self.get_elapsed(), 1),
            "remaining_time": round(self.get_remaining(), 1),
            "duration": self.duration_seconds,
        }

    # ------------------------------------------------------------------
    # Broadcast loop (sends session_update to extension every second)
    # ------------------------------------------------------------------

    async def _broadcast_loop(self):
        while True:
            if self.state in (SessionState.RUNNING, SessionState.PAUSED):
                await self.broadcast_callback({
                    "type": "session_update",
                    **self.get_status_dict(),
                })
            await asyncio.sleep(self.config.session_broadcast_interval)

    def start_broadcast(self, loop: asyncio.AbstractEventLoop):
        self._broadcast_task = loop.create_task(self._broadcast_loop())

    def stop_broadcast(self):
        if self._broadcast_task and not self._broadcast_task.done():
            self._broadcast_task.cancel()
        self._broadcast_task = None
