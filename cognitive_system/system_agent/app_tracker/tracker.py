"""App tracker orchestrator — polls a backend and emits change notifications."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Set

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend

LOGGER = logging.getLogger(__name__)

# Minimum seconds between consecutive active_app_change emissions.
# Prevents high-frequency spam when window titles change rapidly (e.g. browser tabs).
_MIN_CHANGE_INTERVAL_SEC: float = 1.0


def _parse_app_context(process_name: str, window_title: str) -> str:
    """Return the most useful context string for an active-app event.

    VSCode  – title format "● filename.ext - folder - Visual Studio Code"
              → returns the filename (strips unsaved marker, folder, app suffix).
    Others  – returns the full window title unchanged.
    """
    title = (window_title or "").strip()
    if not title:
        return ""
    proc = process_name.lower()
    # code / code.exe is VSCode; guard also covers titles that explicitly say "Visual Studio Code"
    if proc in ("code.exe", "code") or ("code" in proc and "visual studio code" in title.lower()):
        parts = [p.strip() for p in title.split(" - ")]
        # First segment is the open file; strip the unsaved-file marker (●)
        first = parts[0].lstrip("●").strip()
        if first and first.lower() not in {"visual studio code"}:
            return first
        return ""
    return title


class AppTracker:
    """Polls active OS window and emits change notifications.

    Delegates platform-specific capture to an AppTrackerBackend instance.
    """

    def __init__(
        self,
        backend: AppTrackerBackend,
        poll_interval_sec: float,
        browser_processes: Set[str],
        on_change: Callable[[AppSnapshot], None],
    ) -> None:
        self._backend = backend
        self._poll_interval_sec = poll_interval_sec
        self._browser_processes = {name.lower() for name in browser_processes}
        self._on_change = on_change
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_snapshot: Optional[AppSnapshot] = None

    @property
    def current_snapshot(self) -> Optional[AppSnapshot]:
        return self._last_snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="app-tracker", daemon=True)
        self._thread.start()

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout_sec)
        self._backend.cleanup()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            snapshot = self._capture_snapshot()
            if self._has_changed(snapshot):
                self._last_snapshot = snapshot
                try:
                    self._on_change(snapshot)
                except Exception as exc:  # pragma: no cover - callback safety
                    LOGGER.exception("AppTracker callback failed: %s", exc)

            time.sleep(self._poll_interval_sec)

    def _has_changed(self, snapshot: AppSnapshot) -> bool:
        previous = self._last_snapshot
        if previous is None:
            return True
        elapsed_sec = (snapshot.timestamp_ns - previous.timestamp_ns) / 1_000_000_000
        if previous.process_name == snapshot.process_name:
            if (
                snapshot.is_browser
                and elapsed_sec >= _MIN_CHANGE_INTERVAL_SEC
                and (
                    (snapshot.url and snapshot.url != previous.url)
                    or (not snapshot.url and not previous.url and snapshot.window_title != previous.window_title)
                )
            ):
                return True
            return False
        # Debounce: drop events that arrive faster than the minimum interval
        # even if the process name did change (rapid alt-tab sequences).
        if elapsed_sec < _MIN_CHANGE_INTERVAL_SEC:
            return False
        return True

    def _capture_snapshot(self) -> AppSnapshot:
        return self._backend.capture_snapshot(self._browser_processes)
