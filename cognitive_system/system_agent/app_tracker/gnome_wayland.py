"""GNOME Wayland app tracker backend via focused-window-dbus extension.

Requires the GNOME Shell extension "Focused Window D-Bus" to be installed:
    https://extensions.gnome.org/extension/5592/focused-window-d-bus/

The extension exposes a DBus interface at:
    org.gnome.Shell /org/gnome/shell/extensions/FocusedWindow
    org.gnome.shell.extensions.FocusedWindow.Get()

Uses dbus-next (pure Python, no system library dependencies).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
from typing import Optional

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)

_DBUS_SERVICE = "org.gnome.Shell"
_DBUS_PATH = "/org/gnome/shell/extensions/FocusedWindow"
_DBUS_INTERFACE = "org.gnome.shell.extensions.FocusedWindow"
_DBUS_METHOD = "Get"


class GnomeWaylandAppBackend(AppTrackerBackend):
    """Foreground window detection via GNOME Shell extension over DBus."""

    @classmethod
    def backend_name(cls) -> str:
        return "gnome_wayland"

    @classmethod
    def is_candidate(cls) -> bool:
        return (
            platform.system().lower() == "linux"
            and os.environ.get("WAYLAND_DISPLAY") is not None
            and "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        )

    @classmethod
    def probe(cls) -> ProbeResult:
        if not cls.is_candidate():
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="Not a GNOME Wayland session",
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
                    obj = bus.get_proxy_object(_DBUS_SERVICE, _DBUS_PATH)
                    iface = obj.get_interface(_DBUS_INTERFACE)
                    result = await iface.call(_DBUS_METHOD)
                    json.loads(result)
                    bus.disconnect()
                loop.run_until_complete(_check())
                return ProbeResult(
                    available=True,
                    backend_name=cls.backend_name(),
                    detail="GNOME focused-window-dbus extension responding",
                )
            finally:
                loop.close()
        except Exception as exc:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="GNOME extension not responding — install it from:\n"
                       "https://extensions.gnome.org/extension/5592/focused-window-d-bus/",
                guidance="Install the 'Focused Window D-Bus' GNOME Shell extension",
            )

    def __init__(self) -> None:
        from dbus_next.aio import MessageBus
        self._loop = asyncio.new_event_loop()
        self._bus = self._loop.run_until_complete(MessageBus().connect())
        self._obj = self._bus.get_proxy_object(_DBUS_SERVICE, _DBUS_PATH)
        self._iface = self._obj.get_interface(_DBUS_INTERFACE)

    def _call_get(self) -> dict:
        try:
            result = self._loop.run_until_complete(self._iface.call(_DBUS_METHOD))
            return json.loads(result)
        except (json.JSONDecodeError, TypeError, Exception):
            return {}

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        try:
            data = self._call_get()

            if not data:
                return AppSnapshot(
                    timestamp_ns=now_ns(),
                    process_name="unknown",
                    window_title="",
                    pid=None,
                    is_browser=False,
                )

            window_title = data.get("title", "") or ""
            wm_class = data.get("wm_class", "") or ""
            pid = data.get("pid")
            pid_int = int(pid) if pid is not None else None

            process_name = self._get_process_name(pid_int, wm_class)
            is_browser = process_name in browser_processes

            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name=process_name,
                window_title=window_title,
                pid=pid_int,
                is_browser=is_browser,
            )
        except Exception as exc:
            LOGGER.debug("GNOME Wayland capture error: %s", exc)
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

    @staticmethod
    def _get_process_name(pid: Optional[int], wm_class: str) -> str:
        if pid is not None:
            try:
                import psutil
                return psutil.Process(pid).name().lower()
            except Exception:
                pass
        if wm_class:
            return wm_class.lower()
        return "unknown"

    def cleanup(self) -> None:
        try:
            self._bus.disconnect()
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass
