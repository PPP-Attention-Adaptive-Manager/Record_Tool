from __future__ import annotations

import ctypes

import psutil


class ActiveWindowTracker:
    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32

    def get_active_window_info(self) -> tuple[str, str]:
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
