"""Sway app tracker backend via swaymsg IPC."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from typing import Optional

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)


class SwayAppBackend(AppTrackerBackend):
    """Foreground window detection via swaymsg IPC."""

    @classmethod
    def backend_name(cls) -> str:
        return "sway"

    @classmethod
    def is_candidate(cls) -> bool:
        return (
            platform.system().lower() == "linux"
            and shutil.which("swaymsg") is not None
        )

    @classmethod
    def probe(cls) -> ProbeResult:
        if not cls.is_candidate():
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="swaymsg not found in PATH",
            )
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_tree"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                return ProbeResult(
                    available=True,
                    backend_name=cls.backend_name(),
                    detail="swaymsg IPC accessible",
                )
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail=f"swaymsg returned {result.returncode}: {result.stderr.strip()}",
            )
        except Exception as exc:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail=f"swaymsg error: {exc}",
            )

    def _find_focused(self, node: dict) -> Optional[dict]:
        if node.get("focused"):
            return node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            result = self._find_focused(child)
            if result:
                return result
        return None

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_tree"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, "swaymsg")

            tree = json.loads(result.stdout)
            focused = self._find_focused(tree)

            if not focused:
                return AppSnapshot(
                    timestamp_ns=now_ns(),
                    process_name="unknown",
                    window_title="",
                    pid=None,
                    is_browser=False,
                )

            window_title = focused.get("name", "") or ""
            app_id = focused.get("app_id", "") or ""
            pid = focused.get("pid")
            pid_int = int(pid) if pid is not None else None

            process_name = self._get_process_name(pid_int, app_id)
            is_browser = process_name in browser_processes

            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name=process_name,
                window_title=window_title,
                pid=pid_int,
                is_browser=is_browser,
            )
        except Exception as exc:
            LOGGER.debug("Sway capture error: %s", exc)
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

    @staticmethod
    def _get_process_name(pid: Optional[int], app_id: str) -> str:
        if pid is not None:
            try:
                import psutil
                return psutil.Process(pid).name().lower()
            except Exception:
                pass
        if app_id:
            return app_id.lower()
        return "unknown"
