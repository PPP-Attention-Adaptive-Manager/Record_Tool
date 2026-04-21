"""Active application tracking (OS-level)."""

from __future__ import annotations

import ctypes
import logging
import platform
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional, Set

try:
    import psutil
except ImportError:  # handled by startup validation
    psutil = None  # type: ignore[assignment]

from shared.time_utils import now_ns

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSnapshot:
    timestamp_ns: int
    process_name: str
    window_title: str
    pid: int | None
    is_browser: bool

    @property
    def app_name(self) -> str:
        return self.process_name or "unknown"


class AppTracker:
    """Polls active OS window and emits change notifications."""

    def __init__(
        self,
        poll_interval_sec: float,
        browser_processes: Set[str],
        on_change: Callable[[AppSnapshot], None],
    ) -> None:
        self._poll_interval_sec = poll_interval_sec
        self._browser_processes = {name.lower() for name in browser_processes}
        self._on_change = on_change
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_snapshot: Optional[AppSnapshot] = None

        self._is_windows = platform.system().lower() == "windows"
        if not self._is_windows:
            LOGGER.warning("AppTracker currently supports Windows best; fallback will be 'unknown'.")

    @property
    def current_snapshot(self) -> Optional[AppSnapshot]:
        return self._last_snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="app-tracker", daemon=True)
        self._thread.start()

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout_sec)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            snapshot = self._capture_snapshot()
            if self._has_changed(snapshot):
                self._last_snapshot = snapshot
                try:
                    self._on_change(snapshot)
                except Exception as exc:  # pragma: no cover - callback safety
                    LOGGER.exception("AppTracker callback failed: %s", exc)

            time.sleep(self._poll_interval_sec)

    def _has_changed(self, snapshot: AppSnapshot) -> bool:
        previous = self._last_snapshot
        if previous is None:
            return True
        return (previous.process_name, previous.window_title, previous.pid) != (
            snapshot.process_name,
            snapshot.window_title,
            snapshot.pid,
        )

    def _capture_snapshot(self) -> AppSnapshot:
        if self._is_windows:
            return self._capture_windows()
        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name="unknown",
            window_title="unsupported-platform",
            pid=None,
            is_browser=False,
        )

    def _capture_windows(self) -> AppSnapshot:
        if psutil is None:
            raise RuntimeError(
                "AppTracker requires `psutil` for foreground process detection. "
                "Install with `pip install psutil`."
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
        proc_pid = int(pid.value) if pid.value else None
        if proc_pid:
            try:
                process_name = psutil.Process(proc_pid).name().lower()
            except Exception:
                process_name = "unknown"

        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name=process_name,
            window_title=window_title,
            pid=proc_pid,
            is_browser=process_name in self._browser_processes,
        )
