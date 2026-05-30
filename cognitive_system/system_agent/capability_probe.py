"""System capability probe — checks all subsystems before session start."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from system_agent.app_tracker.factory import probe_all_backends as probe_app_backends
from system_agent.config import RuntimeConfig

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilityStatus:
    feature: str
    backend: str
    available: bool
    detail: str
    severity: str  # "ok", "warning", "error"
    guidance: str  # user-facing hint, empty if none


class SystemCapabilityProbe:
    """Probes all subsystems and returns a status report."""

    def __init__(self, config: RuntimeConfig):
        self.config = config

    def probe(self) -> list[CapabilityStatus]:
        results = []
        results.append(self._probe_app_tracking())
        if self.config.notification_tracking_enabled:
            results.append(self._probe_notifications())
        results.append(self._probe_browser_extension())
        return results

    def _probe_app_tracking(self) -> CapabilityStatus:
        results = probe_app_backends()

        for r in results:
            if r.available:
                return CapabilityStatus(
                    feature="App tracking",
                    backend=r.backend_name,
                    available=True,
                    detail=r.detail,
                    severity="ok",
                    guidance="",
                )

        # Nothing available — report the best attempt
        best = results[0] if results else None
        if best and best.guidance:
            return CapabilityStatus(
                feature="App tracking",
                backend="none",
                available=False,
                detail=best.detail,
                severity="warning",
                guidance=best.guidance,
            )

        return CapabilityStatus(
            feature="App tracking",
            backend="none",
            available=False,
            detail="No backend available",
            severity="error",
            guidance="Install python-xlib (X11) or dasbus (Wayland)",
        )

    def _probe_notifications(self) -> CapabilityStatus:
        import platform

        system = platform.system().lower()
        if system == "windows":
            try:
                from system_agent.notification_tracker.windows import WindowsNotificationBackend
                available, detail = WindowsNotificationBackend.probe()
                if available:
                    return CapabilityStatus(
                        feature="Notifications",
                        backend="windows",
                        available=True,
                        detail=detail,
                        severity="ok",
                        guidance="",
                    )
                return CapabilityStatus(
                    feature="Notifications",
                    backend="none",
                    available=False,
                    detail=detail,
                    severity="warning",
                    guidance="Notifications will not be tracked on this system",
                )
            except ImportError:
                return CapabilityStatus(
                    feature="Notifications",
                    backend="none",
                    available=False,
                    detail="winsdk not available",
                    severity="warning",
                    guidance="Notifications will not be tracked on this system",
                )

        elif system == "linux":
            try:
                from system_agent.notification_tracker.linux_dbus import LinuxNotificationBackend
                available, detail = LinuxNotificationBackend.probe()
                if available:
                    return CapabilityStatus(
                        feature="Notifications",
                        backend="linux_dbus",
                        available=True,
                        detail=detail,
                        severity="ok",
                        guidance="",
                    )
                return CapabilityStatus(
                    feature="Notifications",
                    backend="none",
                    available=False,
                    detail=detail,
                    severity="warning",
                    guidance="Notifications will not be tracked on this system",
                )
            except ImportError:
                return CapabilityStatus(
                    feature="Notifications",
                    backend="none",
                    available=False,
                    detail="dbus-next not installed",
                    severity="warning",
                    guidance="pip install dbus-next",
                )

        return CapabilityStatus(
            feature="Notifications",
            backend="none",
            available=False,
            detail=f"Unsupported platform: {system}",
            severity="warning",
            guidance="",
        )

    def _probe_browser_extension(self) -> CapabilityStatus:
        """Check if the browser extension is expected to be available.

        We can't actually test the WebSocket connection here (the server
        isn't running yet), so we just check that the extension directory
        exists and has a manifest.
        """
        repo_root = Path(__file__).resolve().parent.parent
        ext_path = repo_root / "browser_agent_v2"
        manifest = ext_path / "manifest.json"

        if manifest.exists():
            return CapabilityStatus(
                feature="Browser extension",
                backend="chrome_extension",
                available=True,
                detail="Extension folder found — load in Chrome/Chromium",
                severity="ok",
                guidance="",
            )

        return CapabilityStatus(
            feature="Browser extension",
            backend="none",
            available=False,
            detail="Extension folder not found",
            severity="warning",
            guidance="Load browser_agent_v2/ as an unpacked extension in Chrome",
        )
