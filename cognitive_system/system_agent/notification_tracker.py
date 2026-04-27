from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, Optional, Set

LOGGER = logging.getLogger(__name__)


class NotificationTracker:
    """Polls Windows toast notifications via winsdk and emits events via callback.

    Runs as an asyncio Task (create with start() from within a running event loop).
    Emits two event shapes:
      - interaction_type="added"    — notification appeared
      - interaction_type="dismissed" — notification disappeared (response_latency_ms computed)
    """

    POLL_INTERVAL = 2.0

    def __init__(self, on_event: Callable[[dict], None], enabled: bool = True):
        self._on_event = on_event
        self._enabled = enabled
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Call from within a running asyncio event loop (e.g. inside an async method)."""
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
        try:
            from winsdk.windows.ui.notifications import NotificationKinds  # type: ignore[import]
            from winsdk.windows.ui.notifications.management import UserNotificationListener  # type: ignore[import]
        except ImportError:
            LOGGER.warning("winsdk not available — notification tracking disabled")
            return

        listener = UserNotificationListener.current
        try:
            access = await listener.request_access_async()
            LOGGER.info("NotificationListener access status: %s", access)
        except Exception as exc:
            LOGGER.warning("Could not acquire notification listener access: %s", exc)
            return

        previous_ids: Set[int] = set()
        arrivals: Dict[int, float] = {}  # notif_id → arrival epoch seconds

        while True:
            try:
                notifications = await listener.get_notifications_async(NotificationKinds.TOAST)
                current: Dict[int, str] = {}
                for n in notifications:
                    try:
                        current[n.id] = n.app_info.display_info.display_name
                    except Exception:
                        current[n.id] = "unknown"

                current_ids = set(current.keys())
                added_ids = current_ids - previous_ids
                removed_ids = previous_ids - current_ids

                for notif_id in added_ids:
                    now = time.time()
                    arrivals[notif_id] = now
                    self._on_event({
                        "timestamp": now,
                        "app_source": current[notif_id],
                        "notif_id": notif_id,
                        "interaction_type": "added",
                        "response_latency_ms": None,
                    })

                for notif_id in removed_ids:
                    now = time.time()
                    arrival = arrivals.pop(notif_id, None)
                    latency = round((now - arrival) * 1000, 2) if arrival is not None else None
                    self._on_event({
                        "timestamp": now,
                        "app_source": None,
                        "notif_id": notif_id,
                        "interaction_type": "dismissed",
                        "response_latency_ms": latency,
                    })

                previous_ids = current_ids

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("Notification poll error: %s", exc)

            await asyncio.sleep(self.POLL_INTERVAL)
