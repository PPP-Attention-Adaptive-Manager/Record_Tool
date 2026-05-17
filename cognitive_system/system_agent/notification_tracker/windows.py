"""Windows notification tracker backend using winsdk."""

from __future__ import annotations

import asyncio
import io
import logging
import platform
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .base import NotificationTrackerBackend

LOGGER = logging.getLogger(__name__)

_NOTIF_MODULE_DIR = (
    Path(__file__).resolve().parent.parent
    / "Notification_collection"
    / "notif-module"
)

_E_NOTIMPL_STR = "-2147467263"


class WindowsNotificationBackend(NotificationTrackerBackend):
    """Notification tracking via Windows UserNotificationListener (winsdk)."""

    POLL_INTERVAL = 2.0

    @classmethod
    def backend_name(cls) -> str:
        return "windows"

    @classmethod
    def is_candidate(cls) -> bool:
        return platform.system().lower() == "windows"

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        if not cls.is_candidate():
            return False, "Not a Windows platform"
        try:
            from winsdk.windows.ui.notifications.management import (  # noqa: F401
                UserNotificationListener,
            )
            return True, "winsdk available"
        except ImportError:
            return False, "winsdk not installed"
        except Exception as exc:
            return False, f"winsdk error: {exc}"

    def __init__(self) -> None:
        self._on_event: Optional[Callable[[dict], None]] = None
        self._task: Optional[asyncio.Task] = None
        self._last_row_id = 0

    def start(self, on_event: Callable[[dict], None]) -> None:
        self._on_event = on_event
        self._task = asyncio.create_task(self._run(), name="notification-tracker-windows")
        LOGGER.info("Windows notification tracker started")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        LOGGER.info("Windows notification tracker stopped")

    async def _run(self) -> None:
        notif_dir = str(_NOTIF_MODULE_DIR)
        if notif_dir not in sys.path:
            sys.path.insert(0, notif_dir)

        try:
            import listener as notif_listener  # type: ignore[import]
        except ImportError as exc:
            LOGGER.warning("Could not import notification listener module: %s", exc)
            return

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
            from winsdk.windows.ui.notifications.management import (
                UserNotificationListener,
            )
            access = await UserNotificationListener.current.request_access_async()
            LOGGER.info("NotificationListener access: %s", access)
        except ImportError:
            LOGGER.warning("winsdk not available — notification tracking disabled")
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
                    self._log_not_impl()
                    return
                LOGGER.warning("Notification poll error: %s", exc)
            finally:
                sys.stdout = old_stdout

            output = captured.getvalue()
            if _E_NOTIMPL_STR in output:
                self._log_not_impl()
                return

            self._emit_new_rows(conn)
            await asyncio.sleep(self.POLL_INTERVAL)

    def _emit_new_rows(self, conn: sqlite3.Connection) -> None:
        if self._on_event is None:
            return
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

    @staticmethod
    def _log_not_impl() -> None:
        LOGGER.warning(
            "Windows notification API returned E_NOTIMPL — this API requires a "
            "packaged/registered Windows app identity (MSIX/UWP). "
            "Notification tracking is disabled for this session."
        )
