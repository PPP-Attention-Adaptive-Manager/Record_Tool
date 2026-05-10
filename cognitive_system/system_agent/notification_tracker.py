from __future__ import annotations

import asyncio
import io
import logging
import platform
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)

_NOTIF_MODULE_DIR = (
    Path(__file__).resolve().parent
    / "Notification_collection"
    / "notif-module"
)

# HRESULT E_NOTIMPL (0x80004001), returned when the Windows API requires a
# packaged app identity (UWP/MSIX), which a plain Python process may not have.
_E_NOTIMPL_STR = "-2147467263"

_DBUS_UINT32_RE = re.compile(r"^\s*uint32\s+(\d+)")
_DBUS_STRING_RE = re.compile(r'^\s*string "(.*)"')


class NotificationTracker:
    """Collect desktop notifications on Windows and Linux.

    Windows uses the existing winsdk listener module. Linux uses the standard
    org.freedesktop.Notifications DBus traffic exposed by dbus-monitor. DBus
    monitoring is best-effort: notification arrival is broadly supported, while
    close/expiry events depend on the desktop notification daemon.
    """

    POLL_INTERVAL = 2.0

    def __init__(self, on_event: Callable[[dict], None], enabled: bool = True):
        self._on_event = on_event
        self._enabled = enabled
        self._task: Optional[asyncio.Task] = None
        self._last_row_id = 0
        self._platform = platform.system().lower()

    def start(self) -> None:
        """Call from within a running asyncio event loop."""
        if not self._enabled:
            return
        self._task = asyncio.create_task(self._run(), name="notification-tracker")
        LOGGER.info("Notification tracker started")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        LOGGER.info("Notification tracker stopped")

    async def _run(self) -> None:
        if self._platform == "windows":
            await self._run_windows()
        elif self._platform == "linux":
            await self._run_linux()
        else:
            LOGGER.warning("Notification tracking is not supported on %s", self._platform)

    async def _run_windows(self) -> None:
        notif_dir = str(_NOTIF_MODULE_DIR)
        if notif_dir not in sys.path:
            sys.path.insert(0, notif_dir)

        try:
            import listener as notif_listener  # type: ignore[import]
        except ImportError as exc:
            LOGGER.warning("Could not import notification listener module: %s", exc)
            return

        # Point the module's global db_conn at an absolute path so it works
        # regardless of the process working directory.
        db_path = _NOTIF_MODULE_DIR / "data" / "notif_log.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_arrival REAL,
                timestamp_action REAL,
                app_name TEXT,
                notif_id INTEGER,
                interaction_type TEXT
                    CHECK (interaction_type IN ('added', 'dismissed', 'expired')),
                response_time REAL
            )
            """
        )
        conn.commit()
        notif_listener.db_conn = conn

        try:
            from winsdk.windows.ui.notifications.management import (  # type: ignore[import]
                UserNotificationListener,
            )

            access = await UserNotificationListener.current.request_access_async()
            LOGGER.info("NotificationListener access: %s", access)
        except ImportError:
            LOGGER.warning("winsdk not available; notification tracking disabled")
            return
        except Exception as exc:
            LOGGER.warning("Could not get notification access: %s", exc)
            return

        while True:
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                await notif_listener.poll_notifications()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if _E_NOTIMPL_STR in str(exc):
                    _log_not_impl()
                    return
                LOGGER.warning("Notification poll error: %s", exc)
            finally:
                sys.stdout = old_stdout

            output = captured.getvalue()
            if _E_NOTIMPL_STR in output:
                _log_not_impl()
                return

            self._emit_new_rows(conn)
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _run_linux(self) -> None:
        monitor = shutil.which("dbus-monitor")
        if not monitor:
            LOGGER.warning(
                "Linux notification tracking requires `dbus-monitor`. "
                "Install dbus-x11/dbus-tools or disable notification tracking."
            )
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                monitor,
                "interface='org.freedesktop.Notifications'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            LOGGER.warning("Could not start dbus-monitor for notifications: %s", exc)
            return

        parser = _LinuxNotificationParser(self._on_event)
        LOGGER.info("Linux notification tracker listening on org.freedesktop.Notifications")
        try:
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                parser.feed(raw.decode("utf-8", errors="replace"))
        except asyncio.CancelledError:
            raise
        finally:
            parser.flush()
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()

    def _emit_new_rows(self, conn: sqlite3.Connection) -> None:
        """Query the SQLite log for rows added since the last call and emit them."""
        try:
            cursor = conn.execute(
                """
                SELECT id, timestamp_arrival, timestamp_action,
                       app_name, notif_id, interaction_type, response_time
                FROM notifications
                WHERE id > ?
                ORDER BY id ASC
                """,
                (self._last_row_id,),
            )
            for row in cursor.fetchall():
                row_id, ts_arrival, ts_action, app_name, notif_id, itype, resp_time = row
                self._last_row_id = row_id
                timestamp = ts_action if ts_action else (ts_arrival or time.time())
                latency_ms = round(resp_time * 1000, 2) if resp_time is not None else None
                self._on_event(
                    {
                        "timestamp": timestamp,
                        "app_source": app_name,
                        "notif_id": notif_id,
                        "interaction_type": itype,
                        "response_latency_ms": latency_ms,
                    }
                )
        except Exception as exc:
            LOGGER.warning("Could not emit notification rows: %s", exc)


class _LinuxNotificationParser:
    def __init__(self, emit: Callable[[dict], None]) -> None:
        self._emit = emit
        self._next_local_id = 1
        self._active: dict[int, tuple[float, str]] = {}
        self._current_notify: dict[str, object] | None = None
        self._current_closed: list[int] | None = None

    def feed(self, line: str) -> None:
        if line.startswith(("method call", "method return", "signal")):
            self.flush()
            self._current_closed = None
            if "member=Notify" in line:
                self._current_notify = {
                    "timestamp": time.time(),
                    "strings": [],
                    "uint32s": [],
                }
            elif "member=NotificationClosed" in line:
                self._current_closed = []
            return

        if self._current_notify is not None:
            string_match = _DBUS_STRING_RE.match(line)
            if string_match:
                self._current_notify["strings"].append(_dbus_unescape(string_match.group(1)))  # type: ignore[index,union-attr]
                return
            uint_match = _DBUS_UINT32_RE.match(line)
            if uint_match:
                self._current_notify["uint32s"].append(int(uint_match.group(1)))  # type: ignore[index,union-attr]
                return

        if self._current_closed is not None:
            uint_match = _DBUS_UINT32_RE.match(line)
            if not uint_match:
                return
            self._current_closed.append(int(uint_match.group(1)))
            if len(self._current_closed) >= 2:
                notif_id, reason = self._current_closed[:2]
                self._emit_closed(notif_id, reason)
                self._current_closed = None

    def flush(self) -> None:
        if self._current_notify is None:
            return

        timestamp = float(self._current_notify.get("timestamp") or time.time())
        strings = self._current_notify.get("strings") or []
        uint32s = self._current_notify.get("uint32s") or []
        app_name = str(strings[0]).strip() if strings else "unknown"
        replaces_id = int(uint32s[0]) if uint32s else 0
        notif_id = replaces_id if replaces_id > 0 else self._allocate_id()
        self._active[notif_id] = (timestamp, app_name)
        self._emit(
            {
                "timestamp": timestamp,
                "app_source": app_name or "unknown",
                "notif_id": notif_id,
                "interaction_type": "added",
                "response_latency_ms": None,
            }
        )
        self._current_notify = None

    def _emit_closed(self, notif_id: int, reason: int) -> None:
        timestamp = time.time()
        arrival, app_name = self._active.pop(notif_id, (timestamp, "unknown"))
        if reason == 1:
            interaction_type = "expired"
        else:
            interaction_type = "dismissed"
        self._emit(
            {
                "timestamp": timestamp,
                "app_source": app_name,
                "notif_id": notif_id,
                "interaction_type": interaction_type,
                "response_latency_ms": round((timestamp - arrival) * 1000, 2) if arrival else None,
            }
        )

    def _allocate_id(self) -> int:
        value = self._next_local_id
        self._next_local_id += 1
        return value


def _dbus_unescape(value: str) -> str:
    return value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")


def _log_not_impl() -> None:
    LOGGER.warning(
        "Windows notification API returned E_NOTIMPL; this API requires a "
        "packaged/registered Windows app identity (MSIX/UWP). "
        "Notification tracking is disabled for this session."
    )
