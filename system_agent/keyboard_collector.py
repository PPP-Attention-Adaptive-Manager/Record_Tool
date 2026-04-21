from __future__ import annotations

import ctypes
import logging
import threading
import time

from server import SessionController
from window_tracker import ActiveWindowTracker


VK_NAME_MAP = {
    0x08: "backspace",
    0x09: "tab",
    0x0D: "enter",
    0x10: "shift",
    0x11: "ctrl",
    0x12: "alt",
    0x14: "caps_lock",
    0x1B: "escape",
    0x20: "space",
    0x21: "page_up",
    0x22: "page_down",
    0x23: "end",
    0x24: "home",
    0x25: "left",
    0x26: "up",
    0x27: "right",
    0x28: "down",
    0x2D: "insert",
    0x2E: "delete",
    0x5B: "left_win",
    0x5C: "right_win",
}

for code in range(0x30, 0x3A):
    VK_NAME_MAP[code] = chr(code)
for code in range(0x41, 0x5B):
    VK_NAME_MAP[code] = chr(code).lower()
for offset in range(1, 13):
    VK_NAME_MAP[0x6F + offset] = f"f{offset}"


class KeyboardCollector:
    def __init__(
        self,
        controller: SessionController,
        poll_interval: float = 0.02,
    ) -> None:
        self.controller = controller
        self.poll_interval = poll_interval
        self._tracker = ActiveWindowTracker()
        self._user32 = ctypes.windll.user32
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._key_state = {vk: False for vk in range(1, 256)}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="keyboard-collector",
            daemon=True,
        )
        self._thread.start()
        logging.info("Keyboard collector started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logging.info("Keyboard collector stopped")

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._poll_keys()
                time.sleep(self.poll_interval)
        except Exception:
            logging.exception("Keyboard collector crashed")

    def _poll_keys(self) -> None:
        for vk_code in range(1, 256):
            is_down = bool(self._user32.GetAsyncKeyState(vk_code) & 0x8000)
            was_down = self._key_state[vk_code]
            if is_down == was_down:
                continue
            self._key_state[vk_code] = is_down
            self._emit("press" if is_down else "release", vk_code, is_down)

    def _emit(self, action: str, vk_code: int, pressed: bool) -> None:
        try:
            app_name, window_title = self._tracker.get_active_window_info()
            event = self.controller.build_system_input_event(
                event_type="keyboard_input",
                app_name=app_name,
                window_title=window_title,
                input_device="keyboard",
                input_action=action,
                key_value=self._vk_to_string(vk_code),
                pressed=pressed,
            )
            if event is None:
                return
            self.controller.append_system_event(event)
        except Exception:
            logging.exception("Keyboard event emit failed")

    def _vk_to_string(self, vk_code: int) -> str:
        return VK_NAME_MAP.get(vk_code, f"vk_0x{vk_code:02x}")
