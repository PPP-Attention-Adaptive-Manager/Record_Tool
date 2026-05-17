"""Hyprland app tracker backend via hyprctl IPC."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from typing import Optional

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)


class HyprlandAppBackend(AppTrackerBackend):
    """Foreground window detection via hyprctl IPC.

    Parses the output of `hyprctl activewindow` which returns key-value pairs:

        monitor: 0
        workspace: 1
        class: firefox
        title: Google - Mozilla Firefox
        pid: 1234
        ...
    """

    @classmethod
    def backend_name(cls) -> str:
        return "hyprland"

    @classmethod
    def is_candidate(cls) -> bool:
        return (
            platform.system().lower() == "linux"
            and shutil.which("hyprctl") is not None
        )

    @classmethod
    def probe(cls) -> ProbeResult:
        if not cls.is_candidate():
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="hyprctl not found in PATH",
            )
        try:
            result = subprocess.run(
                ["hyprctl", "activewindow"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return ProbeResult(
                    available=True,
                    backend_name=cls.backend_name(),
                    detail="hyprctl IPC accessible",
                )
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="hyprctl returned empty or error",
            )
        except Exception as exc:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail=f"hyprctl error: {exc}",
            )

    def _parse_activewindow(self, output: str) -> dict:
        data = {}
        for line in output.strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip().lower()] = value.strip()
        return data

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        try:
            result = subprocess.run(
                ["hyprctl", "activewindow"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise subprocess.CalledProcessError(
                    result.returncode, "hyprctl", result.stdout, result.stderr
                )

            data = self._parse_activewindow(result.stdout)

            window_title = data.get("title", "") or ""
            app_class = data.get("class", "") or ""
            pid_str = data.get("pid", "")
            pid_int = int(pid_str) if pid_str.isdigit() else None

            process_name = self._get_process_name(pid_int, app_class)
            is_browser = process_name in browser_processes

            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name=process_name,
                window_title=window_title,
                pid=pid_int,
                is_browser=is_browser,
            )
        except Exception as exc:
            LOGGER.debug("Hyprland capture error: %s", exc)
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

    @staticmethod
    def _get_process_name(pid: Optional[int], app_class: str) -> str:
        if pid is not None:
            try:
                import psutil
                return psutil.Process(pid).name().lower()
            except Exception:
                pass
        if app_class:
            return app_class.lower()
        return "unknown"
