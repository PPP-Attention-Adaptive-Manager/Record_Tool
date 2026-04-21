from __future__ import annotations

import logging
import time

from filters import should_drop_event
from server import SessionController
from window_tracker import ActiveWindowTracker


class BehaviorCollector:
    """Collect app focus windows and forward them into the active session."""

    def __init__(
        self,
        controller: SessionController,
        poll_interval: float = 0.5,
        emit_interval: float = 30.0,
    ) -> None:
        self.controller = controller
        self.poll_interval = poll_interval
        self.emit_interval = emit_interval

        self._running = False
        self._current_app = "unknown"
        self._current_title = ""
        self._last_event_monotonic = time.monotonic()
        self._next_emit_deadline = self._last_event_monotonic + emit_interval
        self._tracker = ActiveWindowTracker()

    def request_stop(self) -> None:
        self._running = False

    def run_forever(self) -> None:
        self._running = True
        self._current_app, self._current_title = self._tracker.get_active_window_info()
        self._last_event_monotonic = time.monotonic()
        self._next_emit_deadline = self._last_event_monotonic + self.emit_interval
        logging.info("System collector started")

        try:
            while self._running:
                self._poll_tick()
                time.sleep(self.poll_interval)
        finally:
            self._flush_current_window()
            logging.info("System collector stopped")

    def _poll_tick(self) -> None:
        now_monotonic = time.monotonic()
        active_app, active_title = self._tracker.get_active_window_info()
        app_changed = active_app != self._current_app
        elapsed = max(0.0, now_monotonic - self._last_event_monotonic)

        if app_changed:
            self._emit_window(self._current_app, self._current_title, elapsed)
            self._current_app = active_app
            self._current_title = active_title
            self._last_event_monotonic = now_monotonic
            self._next_emit_deadline = now_monotonic + self.emit_interval
            return

        self._current_title = active_title
        if now_monotonic >= self._next_emit_deadline:
            self._emit_window(self._current_app, self._current_title, elapsed)
            self._last_event_monotonic = now_monotonic
            self._next_emit_deadline = now_monotonic + self.emit_interval

    def _flush_current_window(self) -> None:
        elapsed = max(0.0, time.monotonic() - self._last_event_monotonic)
        self._emit_window(self._current_app, self._current_title, elapsed)

    def _emit_window(self, app_name: str, window_title: str, duration: float) -> None:
        if should_drop_event(duration):
            return
        event = self.controller.build_system_focus_event(
            app_name=app_name,
            window_title=window_title,
            duration=duration,
        )
        if event is None:
            return
        self.controller.append_system_event(event)
        logging.info(
            "system_event=app_focus app=%s duration=%.2fs title=%s",
            app_name,
            duration,
            (window_title or "")[:180],
        )
