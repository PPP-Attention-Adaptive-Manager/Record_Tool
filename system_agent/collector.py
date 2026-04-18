from __future__ import annotations

import ctypes
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import psutil
from pynput import keyboard, mouse

from influx_client import InfluxBatchClient, now_ns


@dataclass
class InputStats:
    keystrokes: int = 0
    mouse_clicks: int = 0
    mouse_distance: float = 0.0


class BehaviorCollector:
    """Collects local desktop behavior and sends raw events to InfluxDB."""

    def __init__(
        self,
        influx_client: InfluxBatchClient,
        user_id: str = "u1",
        poll_interval: float = 0.5,
        emit_interval: float = 3.0,
    ) -> None:
        self.influx_client = influx_client
        self.user_id = user_id
        self.poll_interval = poll_interval
        self.emit_interval = emit_interval

        self._running = False
        self._current_app = "unknown"
        self._current_title = ""
        self._focus_started_monotonic = 0.0
        self._last_emit_monotonic = 0.0

        self._stats = InputStats()
        self._stats_lock = threading.Lock()
        self._last_mouse_position: Optional[Tuple[int, int]] = None

        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None

        self._user32 = ctypes.windll.user32

    def request_stop(self) -> None:
        self._running = False

    def run_forever(self) -> None:
        self._running = True
        self._start_input_listeners()

        app_name, window_title = self._get_active_window_info()
        now_monotonic = time.monotonic()

        self._current_app = app_name
        self._current_title = window_title
        self._focus_started_monotonic = now_monotonic
        self._last_emit_monotonic = now_monotonic

        self.influx_client.enqueue_event(
            self._build_event(
                event_type="focus",
                app_name=self._current_app,
                metrics={"duration": 0.0},
                window_title=self._current_title,
            )
        )

        logging.info("System collector started")

        try:
            while self._running:
                self._poll_active_window()

                now_monotonic = time.monotonic()
                if now_monotonic - self._last_emit_monotonic >= self.emit_interval:
                    self._emit_interval_events(now_monotonic)

                time.sleep(self.poll_interval)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        now_monotonic = time.monotonic()
        self._emit_interval_events(now_monotonic)

        if self._current_app:
            duration = max(0.0, now_monotonic - self._focus_started_monotonic)
            self.influx_client.enqueue_event(
                self._build_event(
                    event_type="switch",
                    app_name=self._current_app,
                    metrics={"duration": duration},
                    window_title=self._current_title,
                )
            )

        self._stop_input_listeners()
        logging.info("System collector stopped")

    def _poll_active_window(self) -> None:
        app_name, window_title = self._get_active_window_info()
        if not app_name:
            return

        now_monotonic = time.monotonic()
        if app_name != self._current_app:
            duration = max(0.0, now_monotonic - self._focus_started_monotonic)
            self.influx_client.enqueue_event(
                self._build_event(
                    event_type="switch",
                    app_name=self._current_app,
                    metrics={"duration": duration},
                    window_title=self._current_title,
                )
            )

            self._current_app = app_name
            self._current_title = window_title
            self._focus_started_monotonic = now_monotonic

            self.influx_client.enqueue_event(
                self._build_event(
                    event_type="focus",
                    app_name=self._current_app,
                    metrics={"duration": 0.0},
                    window_title=self._current_title,
                )
            )
            return

        self._current_title = window_title

    def _emit_interval_events(self, now_monotonic: float) -> None:
        elapsed = max(now_monotonic - self._last_emit_monotonic, 0.001)
        self._last_emit_monotonic = now_monotonic

        with self._stats_lock:
            stats = InputStats(
                keystrokes=self._stats.keystrokes,
                mouse_clicks=self._stats.mouse_clicks,
                mouse_distance=self._stats.mouse_distance,
            )
            self._stats = InputStats()

        mouse_speed = stats.mouse_distance / elapsed
        focus_duration = max(now_monotonic - self._focus_started_monotonic, 0.0)

        # Focus heartbeat keeps app duration visible without waiting for a switch.
        self.influx_client.enqueue_event(
            self._build_event(
                event_type="focus",
                app_name=self._current_app,
                metrics={"duration": focus_duration},
                window_title=self._current_title,
            )
        )

        self.influx_client.enqueue_event(
            self._build_event(
                event_type="input",
                app_name=self._current_app,
                metrics={
                    "duration": elapsed,
                    "keystrokes": stats.keystrokes,
                    "mouse_speed": mouse_speed,
                    "mouse_clicks": stats.mouse_clicks,
                },
                window_title=self._current_title,
            )
        )

    def _build_event(
        self,
        event_type: str,
        app_name: str,
        metrics: Dict[str, Any],
        window_title: str,
    ) -> Dict[str, Any]:
        normalized_metrics = {
            "duration": float(metrics.get("duration", 0.0)),
            "keystrokes": int(metrics.get("keystrokes", 0)),
            "mouse_speed": float(metrics.get("mouse_speed", 0.0)),
            "mouse_clicks": int(metrics.get("mouse_clicks", 0)),
            "window_title": (window_title or "")[:200],
        }

        return {
            "timestamp": now_ns(),
            "tags": {
                "user_id": self.user_id,
                "source_type": "app",
                "app_name": app_name or "unknown",
                "event_type": event_type,
            },
            "fields": normalized_metrics,
        }

    def _start_input_listeners(self) -> None:
        self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
        self._mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
        )
        self._keyboard_listener.start()
        self._mouse_listener.start()

    def _stop_input_listeners(self) -> None:
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    def _on_key_press(self, _key: keyboard.KeyCode) -> None:
        with self._stats_lock:
            self._stats.keystrokes += 1

    def _on_mouse_move(self, x: int, y: int) -> None:
        with self._stats_lock:
            if self._last_mouse_position is not None:
                dx = x - self._last_mouse_position[0]
                dy = y - self._last_mouse_position[1]
                self._stats.mouse_distance += math.hypot(dx, dy)
            self._last_mouse_position = (x, y)

    def _on_mouse_click(
        self,
        x: int,
        y: int,
        _button: mouse.Button,
        pressed: bool,
    ) -> None:
        if not pressed:
            return
        with self._stats_lock:
            self._stats.mouse_clicks += 1
            self._last_mouse_position = (x, y)

    def _get_active_window_info(self) -> Tuple[str, str]:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return "unknown", ""

        title_length = self._user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        self._user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
        window_title = title_buffer.value.strip()

        pid = ctypes.c_ulong(0)
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = "unknown"
        if pid.value:
            try:
                process_name = psutil.Process(pid.value).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process_name = "unknown"

        if "." in process_name:
            process_name = process_name.rsplit(".", maxsplit=1)[0]

        return process_name or "unknown", window_title

