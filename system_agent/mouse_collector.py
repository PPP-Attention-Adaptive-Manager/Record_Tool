from __future__ import annotations

import ctypes
import logging
import threading
import time

from server import SessionController
from window_tracker import ActiveWindowTracker


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


BUTTONS = {
    0x01: "left",
    0x02: "right",
    0x04: "middle",
    0x05: "x1",
    0x06: "x2",
}


class MouseCollector:
    def __init__(
        self,
        controller: SessionController,
        poll_interval: float = 0.01,
        move_interval_seconds: float = 0.05,
    ) -> None:
        self.controller = controller
        self.poll_interval = poll_interval
        self.move_interval_seconds = move_interval_seconds
        self._tracker = ActiveWindowTracker()
        self._user32 = ctypes.windll.user32
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_move_emit = 0.0
        self._last_position: tuple[int, int] | None = None
        self._button_state = {vk: False for vk in BUTTONS}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mouse-collector",
            daemon=True,
        )
        self._thread.start()
        logging.info("Mouse collector started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logging.info("Mouse collector stopped")

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._poll_pointer()
                self._poll_buttons()
                time.sleep(self.poll_interval)
        except Exception:
            logging.exception("Mouse collector crashed")

    def _poll_pointer(self) -> None:
        point = POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            return
        position = (int(point.x), int(point.y))
        if self._last_position is None:
            self._last_position = position
            return
        if position == self._last_position:
            return

        now = time.monotonic()
        if now - self._last_move_emit < self.move_interval_seconds:
            self._last_position = position
            return

        self._last_position = position
        self._last_move_emit = now
        self._emit(
            action="move",
            pointer_x=position[0],
            pointer_y=position[1],
        )

    def _poll_buttons(self) -> None:
        point = POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            return

        for vk_code, button_name in BUTTONS.items():
            is_down = bool(self._user32.GetAsyncKeyState(vk_code) & 0x8000)
            was_down = self._button_state[vk_code]
            if is_down == was_down:
                continue
            self._button_state[vk_code] = is_down
            self._emit(
                action="click",
                button=button_name,
                pressed=is_down,
                pointer_x=int(point.x),
                pointer_y=int(point.y),
            )

    def _emit(
        self,
        *,
        action: str,
        button: str | None = None,
        pressed: bool | None = None,
        pointer_x: int | None = None,
        pointer_y: int | None = None,
    ) -> None:
        try:
            app_name, window_title = self._tracker.get_active_window_info()
            event = self.controller.build_system_input_event(
                event_type="mouse_input",
                app_name=app_name,
                window_title=window_title,
                input_device="mouse",
                input_action=action,
                button=button,
                pressed=pressed,
                pointer_x=pointer_x,
                pointer_y=pointer_y,
            )
            if event is None:
                return
            self.controller.append_system_event(event)
        except Exception:
            logging.exception("Mouse event emit failed")
