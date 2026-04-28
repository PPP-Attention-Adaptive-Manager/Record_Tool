from __future__ import annotations

import asyncio
import io
import logging
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

# HRESULT E_NOTIMPL (0x80004001) — returned when the API requires a packaged
# Windows app identity (UWP / MSIX), which a plain Python process lacks.
_E_NOTIMPL_STR = "-2147467263"


class NotificationTracker:
    """Drives the existing Notification_collection/notif-module listener.

    Calls poll_notifications() from the existing listener module (which
    maintains the SQLite log) and additionally emits each new DB row as an
    event dict via the on_event callback so it can reach CSV / InfluxDB.

    If the Windows notification API returns E_NOTIMPL (requires packaged-app
    identity), the tracker disables itself after the first failed poll and
    logs a single clear warning instead of spamming the console.
    """

    POLL_INTERVAL = 2.0

    def __init__(self, on_event: Callable[[dict], None], enabled: bool = True):
        self._on_event = on_event
        self._enabled = enabled
        self._task: Optional[asyncio.Task] = None
        self._last_row_id = 0

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
        notif_listener.db_conn = conn  # inject into the module's global

        try:
            from winsdk.windows.ui.notifications.management import (  # type: ignore[import]
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

        # Poll loop.
        # poll_notifications() swallows its own exceptions via `print(error)`.
        # We capture stdout so we can detect E_NOTIMPL from that print output
        # and stop cleanly on the first failure instead of looping forever.
        while True:
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                await notif_listener.poll_notifications()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Rare: exception escaped poll_notifications() — handle it too.
                if _E_NOTIMPL_STR in str(exc):
                    _log_not_impl()
                    return
                LOGGER.warning("Notification poll error: %s", exc)
            finally:
                sys.stdout = old_stdout  # always restore, even on exception

            output = captured.getvalue()
            if _E_NOTIMPL_STR in output:
                _log_not_impl()
                return

            self._emit_new_rows(conn)
            await asyncio.sleep(self.POLL_INTERVAL)

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


def _log_not_impl() -> None:
    LOGGER.warning(
        "Windows notification API returned E_NOTIMPL — this API requires a "
        "packaged/registered Windows app identity (MSIX/UWP). "
        "Notification tracking is disabled for this session."
    )
