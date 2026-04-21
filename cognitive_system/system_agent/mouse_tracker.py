import math
import time
import logging
import threading
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from pynput import mouse
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    logger.warning("pynput not installed - mouse tracking disabled")


class MouseTracker:
    def __init__(self, on_event: Callable, aggregation_interval: float = 0.1):
        self.on_event = on_event
        self.aggregation_interval = aggregation_interval
        self._listener = None
        self._active = False
        self._lock = threading.Lock()
        self._last_pos: Optional[Tuple[int, int]] = None
        self._last_move_time: Optional[float] = None

    def start(self):
        if not PYNPUT_AVAILABLE:
            logger.warning("Mouse tracking unavailable (pynput missing)")
            return
        if self._active:
            return
        self._active = True
        self._listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._listener.start()
        logger.info("Mouse tracker started")

    def stop(self):
        self._active = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        logger.info("Mouse tracker stopped")

    def _on_move(self, x, y):
        if not self._active:
            return
        now = time.time()
        with self._lock:
            speed = 0.0
            if self._last_pos and self._last_move_time:
                dt = now - self._last_move_time
                if dt > 0:
                    dist = math.hypot(x - self._last_pos[0], y - self._last_pos[1])
                    speed = round(dist / dt, 2)
            self._last_pos = (x, y)
            self._last_move_time = now
        self.on_event({
            "timestamp": now,
            "event_type": "mouse_move",
            "x": x, "y": y, "speed": speed,
        })

    def _on_click(self, x, y, button, pressed):
        if not self._active:
            return
        self.on_event({
            "timestamp": time.time(),
            "event_type": "mouse_press" if pressed else "mouse_release",
            "x": x, "y": y,
            "button": str(button).replace("Button.", ""),
            "speed": 0.0,
        })

    def _on_scroll(self, x, y, dx, dy):
        if not self._active:
            return
        self.on_event({
            "timestamp": time.time(),
            "event_type": "mouse_scroll",
            "x": x, "y": y,
            "delta_x": dx, "delta_y": dy,
            "speed": 0.0,
        })
