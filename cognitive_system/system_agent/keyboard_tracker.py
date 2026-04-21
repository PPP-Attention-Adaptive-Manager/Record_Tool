import math
import time
import logging
import threading
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from pynput import keyboard
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    logger.warning("pynput not installed - keyboard tracking disabled")


class KeyboardTracker:
    def __init__(self, on_event: Callable):
        self.on_event = on_event
        self._listener = None
        self._active = False
        self._last_press_time: Optional[float] = None
        self._lock = threading.Lock()

    def start(self):
        if not PYNPUT_AVAILABLE:
            logger.warning("Keyboard tracking unavailable (pynput missing)")
            return
        if self._active:
            return
        self._active = True
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
        )
        self._listener.start()
        logger.info("Keyboard tracker started")

    def stop(self):
        self._active = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        logger.info("Keyboard tracker stopped")

    @staticmethod
    def _key_name(key) -> str:
        try:
            return key.char or ""
        except AttributeError:
            return str(key).replace("Key.", "")

    def _on_press(self, key):
        if not self._active:
            return
        now = time.time()
        with self._lock:
            interval_ms = (now - self._last_press_time) * 1000 if self._last_press_time else 0.0
            self._last_press_time = now
        self.on_event({
            "timestamp": now,
            "event_type": "key_press",
            "key": self._key_name(key),
            "interval_ms": round(interval_ms, 2),
        })

    def _on_release(self, key):
        if not self._active:
            return
        self.on_event({
            "timestamp": time.time(),
            "event_type": "key_release",
            "key": self._key_name(key),
            "interval_ms": 0.0,
        })
