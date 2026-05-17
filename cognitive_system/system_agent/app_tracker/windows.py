"""Windows app tracker backend using Win32 APIs."""

from __future__ import annotations

import ctypes
import logging
import platform
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from ctypes import wintypes

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)


class WindowsAppBackend(AppTrackerBackend):
    """Foreground window detection via Win32 user32.dll."""

    @classmethod
    def backend_name(cls) -> str:
        return "windows"

    @classmethod
    def is_candidate(cls) -> bool:
        return platform.system().lower() == "windows"

    @classmethod
    def probe(cls) -> ProbeResult:
        if not cls.is_candidate():
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="Not a Windows platform",
            )
        if psutil is None:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="psutil not installed",
                guidance="pip install psutil",
            )
        try:
            user32 = ctypes.windll.user32  # noqa: F841
            return ProbeResult(
                available=True,
                backend_name=cls.backend_name(),
                detail="Win32 user32.dll accessible",
            )
        except Exception as exc:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail=f"Win32 API unavailable: {exc}",
            )

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        if psutil is None:
            raise RuntimeError(
                "WindowsAppBackend requires `psutil`. Install with `pip install psutil`."
            )

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        title_buffer = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, title_buffer, 1024)
        window_title = title_buffer.value

        process_name = "unknown"
        proc_pid: Optional[int] = int(pid.value) if pid.value else None
        if proc_pid:
            try:
                process_name = psutil.Process(proc_pid).name().lower()
            except Exception:
                process_name = "unknown"

        is_browser = process_name in browser_processes

        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name=process_name,
            window_title=window_title or "",
            pid=proc_pid,
            is_browser=is_browser,
        )
