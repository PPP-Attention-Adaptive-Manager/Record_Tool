"""Fallback app tracker backend using /proc polling via psutil.

This is the last-resort backend when no display-server-specific backend is
available. It polls the foreground process by checking which process has the
most recent activity or by reading /proc entries. Since Linux does not expose
a universal "foreground window" concept without a display server, this backend
uses a heuristic: it tracks the process that owns the active TTY or falls back
to the process with the most recent CPU activity.

In practice, on a desktop session this will usually return the wrong process
for window-level tracking, but it at least gives us a process name rather than
"unknown".
"""

from __future__ import annotations

import logging
import os
import platform
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)


class ProcFallbackBackend(AppTrackerBackend):
    """Last-resort foreground detection via /proc polling."""

    @classmethod
    def backend_name(cls) -> str:
        return "proc_fallback"

    @classmethod
    def is_candidate(cls) -> bool:
        return platform.system().lower() == "linux"

    @classmethod
    def probe(cls) -> ProbeResult:
        if psutil is None:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="psutil not installed",
                guidance="pip install psutil",
            )
        return ProbeResult(
            available=True,
            backend_name=cls.backend_name(),
            detail="/proc polling available (process name only, no window title)",
        )

    def __init__(self) -> None:
        self._last_pid: Optional[int] = None

    def _get_foreground_pid(self) -> Optional[int]:
        """Best-effort foreground PID detection.

        Tries several heuristics:
        1. Read /proc/[pid]/stat for processes with a controlling TTY
        2. Fall back to the process with highest recent CPU time
        """
        if psutil is None:
            return None

        # Try to find the process with the most recent CPU activity
        # that has a visible window (has a DISPLAY or WAYLAND_DISPLAY env)
        best_pid = None
        best_cpu = -1.0

        try:
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "environ"]):
                try:
                    env = proc.info.get("environ") or {}
                    has_display = "DISPLAY" in env or "WAYLAND_DISPLAY" in env
                    if has_display:
                        cpu = proc.info.get("cpu_percent") or 0.0
                        if cpu > best_cpu:
                            best_cpu = cpu
                            best_pid = proc.info["pid"]
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            pass

        return best_pid

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        if psutil is None:
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

        pid = self._get_foreground_pid()
        process_name = "unknown"

        if pid is not None:
            try:
                proc = psutil.Process(pid)
                process_name = proc.name().lower()
                self._last_pid = pid
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process_name = "unknown"

        is_browser = process_name in browser_processes

        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name=process_name,
            window_title="",
            pid=pid,
            is_browser=is_browser,
        )
