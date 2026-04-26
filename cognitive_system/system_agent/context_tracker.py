"""Context state machine: tracks active app/tab and emits finalized duration events."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)


@dataclass
class ContextFrame:
    """One active context window (system app or browser tab/URL)."""

    session_id: str
    device_id: str
    app_name: str
    window_title: str
    url: str
    tab_id: str
    start_time: float = field(default_factory=time.time)


class ContextTracker:
    """
    Single active-context state machine.

    Guarantees:
    - At most ONE open context at any time.
    - Every closed context produces exactly ONE finalized event.
    - duration_ms is always >= 0 (monotonic timestamps assumed).
    - Thread-safe: lock is held only during frame swap, not during callback.
    """

    def __init__(self, on_finalized: Callable[[dict], None]) -> None:
        self._on_finalized = on_finalized
        self._lock = threading.Lock()
        self._frame: Optional[ContextFrame] = None

    # ── public API ────────────────────────────────────────────────────────────

    def open_context(self, frame: ContextFrame) -> None:
        """Start tracking a new context; does NOT close any previous frame."""
        with self._lock:
            self._frame = frame

    def close_context(self, end_time: float) -> None:
        """Close the current context and emit a finalized event."""
        with self._lock:
            finalized = self._pop(end_time)
        if finalized:
            self._emit(finalized)

    def switch_context(self, new_frame: ContextFrame) -> None:
        """Atomically close the previous context and open a new one.

        The end_time of the closing context equals new_frame.start_time,
        ensuring no gaps and no overlaps in the timeline.
        """
        end_time = new_frame.start_time
        with self._lock:
            finalized = self._pop(end_time)
            self._frame = new_frame
        if finalized:
            self._emit(finalized)

    def force_close(self, end_time: Optional[float] = None) -> None:
        """Force-close any open context (session end, crash, shutdown).

        Passes end_time explicitly so the finalized event's timestamp matches
        the moment the session stopped, not when the callback fires.
        """
        self.close_context(end_time if end_time is not None else time.time())

    # ── internals ─────────────────────────────────────────────────────────────

    def _pop(self, end_time: float) -> Optional[dict]:
        """Pop the current frame and build a finalized event dict.

        MUST be called with self._lock held.
        Sets self._frame to None; caller owns the returned dict.
        """
        frame = self._frame
        if frame is None:
            return None
        self._frame = None
        duration_ms = max(0.0, round((end_time - frame.start_time) * 1000, 2))
        return {
            "timestamp": end_time,           # event write time = context end
            "session_id": frame.session_id,
            "device_id": frame.device_id,
            "event_type": "context_end",
            "app_name": frame.app_name,
            "window_title": frame.window_title,
            "url": frame.url,
            "tab_id": frame.tab_id,
            "start_time": frame.start_time,
            "end_time": end_time,
            "duration_ms": duration_ms,
        }

    def _emit(self, event: dict) -> None:
        try:
            self._on_finalized(event)
        except Exception as exc:
            LOGGER.exception("ContextTracker.on_finalized raised: %s", exc)
