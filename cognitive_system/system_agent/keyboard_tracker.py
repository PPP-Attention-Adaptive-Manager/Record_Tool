from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)


class KeyboardTracker:
    def __init__(self, on_event: Callable[[dict], None], enabled: bool = True):
        self._on_event = on_event
        self._enabled = enabled
        self._listener = None
        self._active = False
        self._last_press_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled:
            return
        if self._active:
            return
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise RuntimeError(
                "Keyboard tracking is enabled but `pynput` is not installed. "
                "Install with `pip install pynput` or disable keyboard tracking."
            ) from exc

        self._active = True
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
        )
        self._listener.start()
        LOGGER.info("Keyboard tracker started")

    def stop(self) -> None:
        self._active = False
        if self._listener:
            self._listener.stop()
            self._listener = None
        LOGGER.info("Keyboard tracker stopped")

    @staticmethod
    def _key_name(key) -> str:
        try:
            return key.char or ""
        except AttributeError:
            return str(key).replace("Key.", "")

    def _on_press(self, key) -> None:
        if not self._active:
            return
        now = time.time()
        with self._lock:
            interval_ms = (now - self._last_press_time) * 1000 if self._last_press_time else 0.0
            self._last_press_time = now

        self._on_event(
            {
                "timestamp": now,
                "event_type": "key_press",
                "key": self._key_name(key),
                "interval_ms": round(interval_ms, 2),
            }
        )

    def _on_release(self, key) -> None:
        if not self._active:
            return
        self._on_event(
            {
                "timestamp": time.time(),
                "event_type": "key_release",
                "key": self._key_name(key),
                "interval_ms": 0.0,
            }
        )

