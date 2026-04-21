from __future__ import annotations

import ctypes
import logging
import time
import requests
import uuid
from typing import Optional, Tuple

import psutil

from filters import classify_event_type, should_drop_event

class BehaviorCollector:
    """Collects app-level focus/switch events and sends them to the core server."""

    def __init__(
        self,
        server_url: str = "http://localhost:8765",
        poll_interval: float = 0.5,
        emit_interval: float = 30.0,
        merge_flush_threshold: float = 30.0,
    ) -> None:
        self.server_url = server_url
        self.poll_interval = poll_interval
        self.emit_interval = emit_interval
        self.merge_flush_threshold = merge_flush_threshold

        self._running = False
        self._current_app = "unknown"
        self._current_title = ""
        self._last_event_monotonic = 0.0
        self._next_emit_deadline = 0.0
        self._pending_event: Optional[dict] = None

        # Detect OS
        self._is_windows = hasattr(ctypes, "windll")
        if self._is_windows:
            self._user32 = ctypes.windll.user32
        else:
            logging.warning("Non-Windows OS detected. Collector will use mock window info.")

    def request_stop(self) -> None:
        self._running = False

    def _get_session_status(self) -> dict:
        try:
            response = requests.get(f"{self.server_url}/session/status", timeout=2)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.error(f"Failed to get session status: {e}")
        return {"active": False}

    def run_forever(self, session_minutes: Optional[float] = None) -> None:
        self._running = True

        self._current_app, self._current_title = self._get_active_window_info()
        self._last_event_monotonic = time.monotonic()
        self._next_emit_deadline = self._last_event_monotonic + self.emit_interval
        
        logging.info("System collector started. Waiting for active session...")

        try:
            while self._running:
                status = self._get_session_status()
                if status.get("active"):
                    self._poll_tick(status["session_id"])
                else:
                    if self._pending_event:
                        self._flush_pending_event(force=True)
                
                time.sleep(self.poll_interval)
        finally:
            self._shutdown()

    def _poll_tick(self, session_id: str) -> None:
        now_monotonic = time.monotonic()
        active_app, active_title = self._get_active_window_info()
        app_changed = active_app != self._current_app
        elapsed = max(0.0, now_monotonic - self._last_event_monotonic)

        if app_changed:
            self._handle_event_window(
                session_id=session_id,
                app_name=self._current_app,
                window_title=self._current_title,
                duration=elapsed,
                app_changed=True,
            )
            self._current_app = active_app
            self._current_title = active_title
            self._last_event_monotonic = now_monotonic
            self._next_emit_deadline = now_monotonic + self.emit_interval
            return

        self._current_title = active_title
        if now_monotonic >= self._next_emit_deadline:
            self._handle_event_window(
                session_id=session_id,
                app_name=self._current_app,
                window_title=self._current_title,
                duration=elapsed,
                app_changed=False,
            )
            self._last_event_monotonic = now_monotonic
            self._next_emit_deadline = now_monotonic + self.emit_interval

    def _handle_event_window(
        self,
        session_id: str,
        app_name: str,
        window_title: str,
        duration: float,
        app_changed: bool,
    ) -> None:
        if should_drop_event(duration):
            return

        event_type = classify_event_type(duration, app_changed)
        event = {
            "session_id": session_id,
            "event_id": f"evt_{uuid.uuid4().hex[:8]}",
            "event_type": "app_focus" if event_type == "focus" else "switch",
            "timestamp": int(time.time() * 1000),
            "app_name": app_name or "unknown",
            "duration": duration,
            "window_title": window_title or "",
            "source": "system",
        }

        if event["event_type"] == "switch":
            self._flush_pending_event(force=True)
            self._send_event(event)
            return

        if (
            self._pending_event
            and self._pending_event["app_name"] == event["app_name"]
            and self._pending_event["event_type"] == "app_focus"
        ):
            self._pending_event["duration"] += event["duration"]
            self._pending_event["timestamp"] = event["timestamp"]
        else:
            self._flush_pending_event(force=True)
            self._pending_event = event

        self._flush_pending_event(force=False)

    def _flush_pending_event(self, force: bool) -> None:
        if self._pending_event is None:
            return
        if not force and self._pending_event["duration"] < self.merge_flush_threshold:
            return

        self._send_event(self._pending_event)
        self._pending_event = None

    def _send_event(self, event: dict) -> None:
        logging.info(
            "event=%s app=%s duration=%.2fs title=%s",
            event["event_type"],
            event["app_name"],
            event["duration"],
            (event["window_title"] or "")[:180],
        )
        try:
            requests.post(
                f"{self.server_url}/events",
                json={"session_id": event["session_id"], "events": [event]},
                timeout=2
            )
        except Exception as e:
            logging.error(f"Failed to send event to core: {e}")

    def _shutdown(self) -> None:
        status = self._get_session_status()
        if status.get("active"):
            now_monotonic = time.monotonic()
            elapsed = max(0.0, now_monotonic - self._last_event_monotonic)
            self._handle_event_window(
                session_id=status["session_id"],
                app_name=self._current_app,
                window_title=self._current_title,
                duration=elapsed,
                app_changed=False,
            )
        self._flush_pending_event(force=True)
        logging.info("System collector stopped")

    def _get_active_window_info(self) -> Tuple[str, str]:
        if not self._is_windows:
            return "mock_app", "Mock Window Title"

        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return "unknown", ""

        title_length = self._user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        self._user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
        window_title = title_buffer.value.strip()

        pid = ctypes.c_ulong(0)
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return "unknown", window_title

        process_name = "unknown"
        try:
            process_name = psutil.Process(pid.value).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "unknown"

        if "." in process_name:
            process_name = process_name.rsplit(".", maxsplit=1)[0]
        return process_name or "unknown", window_title
