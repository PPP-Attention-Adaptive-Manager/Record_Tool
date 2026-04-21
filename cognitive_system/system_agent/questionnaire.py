"""Session-end subjective questionnaire."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from typing import Dict, Optional

from shared.time_utils import now_ns, ns_to_iso8601

LOGGER = logging.getLogger(__name__)


class Questionnaire:
    """Collects NASA-TLX + stress + emotion at session end."""

    _FIELDS = [
        ("mental_demand", 0, 100, 50),
        ("physical_demand", 0, 100, 50),
        ("temporal_demand", 0, 100, 50),
        ("performance", 0, 100, 50),
        ("effort", 0, 100, 50),
        ("frustration", 0, 100, 50),
        ("stress_self_report", 0, 100, 50),
        ("valence", -50, 50, 0),
        ("arousal", 0, 100, 50),
    ]

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def collect(self, session_id: str, device_id: str) -> Optional[Dict[str, object]]:
        if not self._enabled:
            return None

        try:
            values = self._collect_gui()
        except Exception as exc:  # pragma: no cover - GUI environment dependent
            LOGGER.warning("Questionnaire GUI failed (%s). Falling back to CLI/defaults.", exc)
            values = self._collect_cli_or_defaults()

        timestamp_ns = now_ns()
        event = {
            "timestamp_ns": timestamp_ns,
            "timestamp": ns_to_iso8601(timestamp_ns),
            "session_id": session_id,
            "device_id": device_id,
            "event_type": "subjective_label",
        }
        event.update(values)
        return event

    def _collect_gui(self) -> Dict[str, int]:
        root = tk.Tk()
        root.title("Session Questionnaire")
        root.attributes("-topmost", True)
        root.geometry("620x690")

        container = tk.Frame(root, padx=15, pady=10)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text="NASA-TLX + Stress + Emotion",
            font=("Segoe UI", 16, "bold"),
            pady=10,
        ).pack(anchor="w")

        tk.Label(
            container,
            text="Move each slider, then click Submit.",
            font=("Segoe UI", 10),
            pady=4,
        ).pack(anchor="w")

        variables: Dict[str, tk.IntVar] = {}
        for field, minimum, maximum, default in self._FIELDS:
            frame = tk.Frame(container, pady=4)
            frame.pack(fill=tk.X)
            tk.Label(frame, text=field.replace("_", " ").title(), font=("Segoe UI", 10)).pack(anchor="w")
            var = tk.IntVar(value=default)
            scale = tk.Scale(
                frame,
                from_=minimum,
                to=maximum,
                orient=tk.HORIZONTAL,
                resolution=1,
                variable=var,
                length=560,
            )
            scale.pack(anchor="w")
            variables[field] = var

        submitted = {"value": False}

        def submit() -> None:
            submitted["value"] = True
            root.destroy()

        tk.Button(container, text="Submit", command=submit, width=14).pack(pady=10, anchor="e")
        root.mainloop()

        if not submitted["value"]:
            raise RuntimeError("Questionnaire closed without submit.")

        return {field: int(var.get()) for field, var in variables.items()}

    def _collect_cli_or_defaults(self) -> Dict[str, int]:
        if not sys.stdin.isatty():
            return {field: default for field, _, _, default in self._FIELDS}

        results: Dict[str, int] = {}
        for field, minimum, maximum, default in self._FIELDS:
            while True:
                prompt = f"{field} [{minimum}-{maximum}] (default {default}): "
                raw = input(prompt).strip()
                if not raw:
                    results[field] = default
                    break
                try:
                    value = int(raw)
                except ValueError:
                    print("Please enter an integer.")
                    continue
                if minimum <= value <= maximum:
                    results[field] = value
                    break
                print(f"Please enter value between {minimum} and {maximum}.")
        return results

