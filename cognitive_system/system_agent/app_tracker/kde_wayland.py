"""KDE Wayland app tracker backend via KWin scripting + DBus.

Loads a KWin script at runtime that pushes window activation events to our
DBus service. No user installation required — the script is loaded/unloaded
via the KWin Scripting DBus API.

If the app crashes without unloading the script, the KWin script's callDBus
calls simply become no-ops (target service doesn't exist).

Uses dbus-next (pure Python, no system library dependencies).
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import threading
from pathlib import Path
from typing import Optional

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)

_KWIN_DBUS_SERVICE = "org.kde.KWin"
_KWIN_SCRIPTING_PATH = "/Scripting"
_KWIN_SCRIPTING_INTERFACE = "org.kde.kwin.Scripting"

_OUR_DBUS_SERVICE = "org.cognitive_system.agent"
_OUR_DBUS_PATH = "/AppTracker"
_OUR_DBUS_INTERFACE = "org.cognitive_system.AppTracker"
_OUR_DBUS_METHOD = "WindowActivated"


class KdeWaylandAppBackend(AppTrackerBackend):
    """Foreground window detection via KWin script + DBus signal reception."""

    @classmethod
    def backend_name(cls) -> str:
        return "kde_wayland"

    @classmethod
    def is_candidate(cls) -> bool:
        return (
            platform.system().lower() == "linux"
            and os.environ.get("WAYLAND_DISPLAY") is not None
            and (
                "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
                or "plasma" in os.environ.get("DESKTOP_SESSION", "").lower()
            )
        )

    @classmethod
    def probe(cls) -> ProbeResult:
        if not cls.is_candidate():
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="Not a KDE Wayland session",
            )
        try:
            import dbus_next
        except ImportError:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="dbus-next not installed",
                guidance="pip install dbus-next",
            )
        try:
            from dbus_next.aio import MessageBus
            loop = asyncio.new_event_loop()
            try:
                async def _check():
                    bus = await MessageBus().connect()
                    obj = bus.get_proxy_object(_KWIN_DBUS_SERVICE, _KWIN_SCRIPTING_PATH)
                    obj.get_interface(_KWIN_SCRIPTING_INTERFACE)
                    bus.disconnect()
                loop.run_until_complete(_check())
                return ProbeResult(
                    available=True,
                    backend_name=cls.backend_name(),
                    detail="KWin scripting DBus API accessible",
                )
            finally:
                loop.close()
        except Exception as exc:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail=f"KWin DBus error: {exc}",
            )

    def __init__(self) -> None:
        from dbus_next.aio import MessageBus
        from dbus_next import BusType

        self._loop = asyncio.new_event_loop()
        self._bus = self._loop.run_until_complete(
            MessageBus(bus_type=BusType.SESSION).connect()
        )

        self._lock = threading.Lock()
        self._last_caption = ""
        self._last_resource_class = ""
        self._last_pid: Optional[int] = None
        self._last_timestamp_ns = now_ns()
        self._script_id = None

        self._load_kwin_script()

    def _get_script_path(self) -> str:
        """Locate the bundled KWin script."""
        return str(
            Path(__file__).resolve().parent.parent.parent
            / "kwin_scripts"
            / "window_notifier.js"
        )

    def _load_kwin_script(self) -> None:
        """Load the KWin script via DBus. Script ID is stored for later unload."""
        script_path = self._get_script_path()
        try:
            obj = self._bus.get_proxy_object(_KWIN_DBUS_SERVICE, _KWIN_SCRIPTING_PATH)
            iface = obj.get_interface(_KWIN_SCRIPTING_INTERFACE)

            async def _load():
                script_id = await iface.call("loadScript", "s", script_path, "s", "cognitive_system_notifier")
                return script_id

            self._script_id = self._loop.run_until_complete(_load())
            LOGGER.info("KWin script loaded with ID: %s", self._script_id)
        except Exception as exc:
            LOGGER.warning("Failed to load KWin script: %s", exc)
            self._script_id = None

    def _get_process_name(self, pid: Optional[int], resource_class: str) -> str:
        if pid and pid > 0:
            try:
                import psutil
                return psutil.Process(pid).name().lower()
            except Exception:
                pass
        if resource_class:
            return resource_class.lower()
        return "unknown"

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        with self._lock:
            resource_class = self._last_resource_class
            caption = self._last_caption
            pid = self._last_pid
            timestamp_ns = self._last_timestamp_ns

        process_name = self._get_process_name(pid, resource_class)
        is_browser = process_name in browser_processes

        return AppSnapshot(
            timestamp_ns=timestamp_ns,
            process_name=process_name,
            window_title=caption,
            pid=pid,
            is_browser=is_browser,
        )

    def cleanup(self) -> None:
        if self._script_id is not None:
            try:
                obj = self._bus.get_proxy_object(_KWIN_DBUS_SERVICE, _KWIN_SCRIPTING_PATH)
                iface = obj.get_interface(_KWIN_SCRIPTING_INTERFACE)

                async def _unload():
                    await iface.call("unloadScript", "s", self._script_id)

                self._loop.run_until_complete(_unload())
                LOGGER.info("KWin script unloaded: %s", self._script_id)
            except Exception as exc:
                LOGGER.debug("Failed to unload KWin script (harmless): %s", exc)
        try:
            self._bus.disconnect()
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass
