"""Linux notification tracker backend via dbus-monitor subprocess.

Spawns dbus-monitor to intercept Notify method calls on the session bus.
This catches notifications sent by any application to the notification
daemon without replacing the daemon itself.

The dbus-monitor output for a Notify call looks like:
  method call time=... sender=:1.X -> destination=:1.Y ... member=Notify
     string "notify-send"
     uint32 0
     string ""
     string "Test Title"
     string "Test Body"
     array [
     ]
     array [
        dict entry(
           string "urgency"
           variant             byte 1
        )
     ]
     int32 -1

We only capture app_name (as app_source) to match the existing notification
CSV schema used by the Windows backend and the feature engineering pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import subprocess
import time
from typing import Callable, Optional

from .base import NotificationTrackerBackend

LOGGER = logging.getLogger(__name__)

_NOTIFY_SERVICE = "org.freedesktop.Notifications"
_NOTIFY_PATH = "/org/freedesktop/Notifications"
_NOTIFY_INTERFACE = "org.freedesktop.Notifications"


class LinuxNotificationBackend(NotificationTrackerBackend):
    """Notification tracking via dbus-monitor subprocess."""

    @classmethod
    def backend_name(cls) -> str:
        return "linux_dbus"

    @classmethod
    def is_candidate(cls) -> bool:
        return platform.system().lower() == "linux"

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        if not cls.is_candidate():
            return False, "Not a Linux platform"
        if shutil.which("dbus-monitor") is None:
            return False, "dbus-monitor not found in PATH"
        try:
            from dbus_next.aio import MessageBus
            loop = asyncio.new_event_loop()
            try:
                async def _check():
                    bus = await MessageBus().connect()
                    await bus.introspect(_NOTIFY_SERVICE, _NOTIFY_PATH)
                    bus.disconnect()
                loop.run_until_complete(_check())
                return True, "Notification server found on session bus"
            finally:
                loop.close()
        except ImportError:
            return False, "dbus-next not installed"
        except Exception as exc:
            return False, f"No notification server on session bus: {exc}"

    def __init__(self) -> None:
        self._on_event: Optional[Callable[[dict], None]] = None
        self._proc: Optional[subprocess.Popen] = None
        self._task: Optional[asyncio.Task] = None

    def start(self, on_event: Callable[[dict], None]) -> None:
        self._on_event = on_event
        self._task = asyncio.create_task(self._run(), name="notification-tracker-linux")
        LOGGER.info("Linux notification tracker started (dbus-monitor)")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()
            self._proc = None
        LOGGER.info("Linux notification tracker stopped")

    async def _run(self) -> None:
        match_rule = (
            f"type='method_call',"
            f"interface='{_NOTIFY_INTERFACE}',"
            f"member='Notify',"
            f"destination='{_NOTIFY_SERVICE}'"
        )

        try:
            self._proc = subprocess.Popen(
                ["dbus-monitor", "--session", match_rule],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            loop = asyncio.get_event_loop()
            while True:
                line = await loop.run_in_executor(None, self._proc.stdout.readline)
                if not line:
                    break

                line = line.strip()

                # Detect the Notify method call header line
                if "member=Notify" in line:
                    # First string field after the header is app_name.
                    # We skip the remaining fields (replaces_id, icon, summary, body,
                    # arrays, int32) to stay in sync with the stream.
                    app_name = await self._read_next_string(loop)
                    await self._read_next_line(loop)   # uint32 replaces_id
                    await self._read_next_line(loop)   # string app_icon
                    await self._read_next_line(loop)   # string summary
                    await self._read_next_line(loop)   # string body
                    # Skip array blocks and int32 — just drain until next header
                    # or blank line. The match rule ensures we only see Notify calls,
                    # so we can safely skip to the next readline.

                    if self._on_event:
                        self._on_event(
                            {
                                "timestamp": time.time(),
                                "app_source": app_name,
                                "notif_id": 0,
                                "interaction_type": "added",
                                "response_latency_ms": None,
                            }
                        )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("Linux notification tracker error: %s", exc)

    async def _read_next_line(self, loop: asyncio.AbstractEventLoop) -> str:
        """Read and return the next line from dbus-monitor stdout."""
        return await loop.run_in_executor(None, self._proc.stdout.readline)

    async def _read_next_string(self, loop: asyncio.AbstractEventLoop) -> str:
        """Read the next line and extract the string value."""
        line = await self._read_next_line(loop)
        return self._extract_string(line)

    @staticmethod
    def _extract_string(line: str) -> str:
        """Extract the value from a dbus-monitor string line.

        Format:    string "value"  or  string 'value'
        """
        line = line.strip()
        if not line.startswith("string "):
            return ""
        value = line[7:].strip()
        if len(value) >= 2:
            if (value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"):
                return value[1:-1]
        return ""
