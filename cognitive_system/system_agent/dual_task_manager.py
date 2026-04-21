"""Experimental dual-task prompt scheduler."""

from __future__ import annotations

import logging
import random
import threading
import time
import tkinter as tk
from typing import Callable, Optional

from shared.time_utils import now_ns, ns_to_iso8601

LOGGER = logging.getLogger(__name__)


class DualTaskManager:
    """Runs periodic reaction trials during session when experimental mode is enabled."""

    _STIMULI = ("F", "J")

    def __init__(
        self,
        enabled: bool,
        min_interval_sec: int,
        max_interval_sec: int,
        timeout_sec: int,
        on_result: Callable[[dict], None],
    ) -> None:
        self._enabled = enabled
        self._min_interval_sec = min_interval_sec
        self._max_interval_sec = max(max_interval_sec, min_interval_sec)
        self._timeout_sec = timeout_sec
        self._on_result = on_result
        self._session_id: Optional[str] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self, session_id: str) -> None:
        if not self._enabled:
            return
        self.stop()
        self._session_id = session_id
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dual-task")
        self._thread.start()
        LOGGER.info("Dual-task manager started for session=%s", session_id)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._session_id = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            wait_sec = random.uniform(self._min_interval_sec, self._max_interval_sec)
            if self._stop_event.wait(wait_sec):
                break

            result = self._run_trial()
            if result:
                try:
                    self._on_result(result)
                except Exception as exc:  # pragma: no cover - callback safety
                    LOGGER.exception("Dual-task callback failed: %s", exc)

    def _run_trial(self) -> dict:
        session_id = self._session_id or ""
        stimulus = random.choice(self._STIMULI)
        expected = stimulus.lower()
        timestamp_ns = now_ns()

        result = {
            "timestamp_ns": timestamp_ns,
            "timestamp": ns_to_iso8601(timestamp_ns),
            "session_id": session_id,
            "event_type": "dual_task",
            "stimulus": stimulus,
            "expected_response": expected,
            "response": "",
            "reaction_time_ms": 0.0,
            "miss": True,
            "error": False,
        }

        try:
            trial_start = time.perf_counter()
            response = self._show_popup(stimulus=stimulus, timeout_sec=self._timeout_sec)
            if response is None:
                result["miss"] = True
            else:
                reaction_ms = (time.perf_counter() - trial_start) * 1000
                result["response"] = response
                result["reaction_time_ms"] = round(reaction_ms, 3)
                result["miss"] = False
                result["error"] = response != expected
        except Exception as exc:  # pragma: no cover - GUI environment dependent
            LOGGER.exception("Dual-task popup failed: %s", exc)
            result["error"] = True
            result["miss"] = True
            result["ui_error"] = str(exc)

        return result

    @staticmethod
    def _show_popup(stimulus: str, timeout_sec: int) -> str | None:
        response_holder = {"value": None}
        finished = threading.Event()

        root = tk.Tk()
        root.title("Dual-Task Prompt")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.geometry("420x190")

        tk.Label(
            root,
            text="Press F or J as fast as possible",
            font=("Segoe UI", 14, "bold"),
            pady=12,
        ).pack()

        tk.Label(
            root,
            text=stimulus,
            font=("Segoe UI", 42, "bold"),
            fg="#1f4f8b",
            pady=10,
        ).pack()

        def on_key(event: tk.Event) -> None:
            key = (event.char or "").strip().lower()
            if key in {"f", "j"}:
                response_holder["value"] = key
                finished.set()
                root.destroy()

        def on_timeout() -> None:
            if not finished.is_set():
                finished.set()
                root.destroy()

        root.bind("<Key>", on_key)
        root.after(int(timeout_sec * 1000), on_timeout)
        root.mainloop()
        return response_holder["value"]

