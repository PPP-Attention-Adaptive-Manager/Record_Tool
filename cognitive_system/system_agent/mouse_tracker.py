from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional, Tuple

LOGGER = logging.getLogger(__name__)


class MouseTracker:
    def __init__(self, on_event: Callable[[dict], None], enabled: bool = True):
        self._on_event = on_event
        self._enabled = enabled
        self._listener = None
        self._active = False

        self._lock = threading.Lock()
        self._last_pos: Optional[Tuple[int, int]] = None
        self._last_move_time: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled:
            return
        if self._active:
            return
        try:
            from pynput import mouse
        except ImportError as exc:
            raise RuntimeError(
                "Mouse tracking is enabled but `pynput` is not installed. "
                "Install with `pip install pynput` or disable mouse tracking."
            ) from exc

        self._active = True
        self._listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._listener.start()
        LOGGER.info("Mouse tracker started")

    def stop(self) -> None:
        self._active = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        LOGGER.info("Mouse tracker stopped")

    def _on_move(self, x: int, y: int) -> None:
        if not self._active:
            return
        now = time.time()
        with self._lock:
            speed = 0.0
            if self._last_pos and self._last_move_time:
                elapsed = now - self._last_move_time
                if elapsed > 0:
                    distance = math.hypot(x - self._last_pos[0], y - self._last_pos[1])
                    speed = round(distance / elapsed, 2)
            self._last_pos = (x, y)
            self._last_move_time = now

        self._on_event(
            {
                "timestamp": now,
                "event_type": "mouse_move",
                "x": x,
                "y": y,
                "speed": speed,
            }
        )

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not self._active:
            return
        self._on_event(
            {
                "timestamp": time.time(),
                "event_type": "mouse_press" if pressed else "mouse_release",
                "x": x,
                "y": y,
                "button": str(button).replace("Button.", ""),
                "speed": 0.0,
            }
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._active:
            return
        self._on_event(
            {
                "timestamp": time.time(),
                "event_type": "mouse_scroll",
                "x": x,
                "y": y,
                "delta_x": dx,
                "delta_y": dy,
                "speed": 0.0,
            }
        )

