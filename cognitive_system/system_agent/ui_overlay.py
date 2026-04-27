from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)

_DOT_COLORS = {
    "running": "#4caf50",
    "paused": "#ff9800",
    "idle": "#9e9e9e",
    "stopped": "#f44336",
}

_STATUS_LABELS = {
    "running": "REC",
    "paused": "PAUSED",
    "idle": "IDLE",
    "stopped": "STOPPED",
}


class UIOverlay:
    """Floating always-on-top draggable status overlay.

    Shows elapsed time and a colored recording-state dot.
    Double-click to expand a panel with a Stop Session button.
    Thread-safe: call update() from any thread.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._queue: queue.Queue = queue.Queue()
        self._root = None
        self._started = threading.Event()
        self._on_stop: Optional[Callable] = None
        self._drag_x = 0
        self._drag_y = 0
        self._expanded = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, on_stop_requested: Optional[Callable] = None) -> None:
        self._on_stop = on_stop_requested
        self._thread = threading.Thread(target=self._run, name="ui-overlay", daemon=True)
        self._thread.start()
        self._started.wait(timeout=3.0)

    def stop(self) -> None:
        self._queue.put(("quit", None, None))
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def update(self, state: str, elapsed_sec: float) -> None:
        """Thread-safe status update."""
        self._queue.put(("update", state, elapsed_sec))

    # ── tkinter thread ────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            LOGGER.warning("tkinter not available — UI overlay disabled")
            self._started.set()
            return

        root = tk.Tk()
        self._root = root
        root.title("")
        root.overrideredirect(True)   # borderless
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.92)
        root.configure(bg="#1a2332")

        # Position: bottom-right corner
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"+{sw - 165}+{sh - 80}")

        # ── Compact row ───────────────────────────────────────────────
        compact = tk.Frame(root, bg="#1a2332", padx=8, pady=6)
        compact.pack(fill="both", expand=True)

        self._dot = tk.Label(
            compact, text="●", font=("Segoe UI", 14), bg="#1a2332", fg="#9e9e9e",
        )
        self._dot.pack(side="left", padx=(0, 4))

        self._time_label = tk.Label(
            compact, text="00:00", font=("Consolas", 13, "bold"),
            bg="#1a2332", fg="#e8f4fc",
        )
        self._time_label.pack(side="left", padx=(0, 5))

        self._status_label = tk.Label(
            compact, text="IDLE", font=("Segoe UI", 9),
            bg="#1a2332", fg="#6b8ea8",
        )
        self._status_label.pack(side="left")

        # ── Expanded panel (hidden until double-click) ────────────────
        self._expanded_frame = tk.Frame(root, bg="#1a2332", pady=4)

        stop_btn = tk.Button(
            self._expanded_frame, text="■  Stop Session",
            font=("Segoe UI", 9), bg="#c0392b", fg="white",
            activebackground="#a93226", activeforeground="white",
            relief="flat", padx=8, pady=4,
            command=self._request_stop,
        )
        stop_btn.pack(padx=8, pady=(0, 6))

        # ── Wire drag + double-click on every visible widget ──────────
        for w in (compact, self._dot, self._time_label, self._status_label):
            w.bind("<ButtonPress-1>", self._on_drag_start)
            w.bind("<B1-Motion>", self._on_drag_motion)
            w.bind("<Double-Button-1>", self._toggle_expand)

        self._started.set()
        self._poll_queue(root)
        root.mainloop()

    def _poll_queue(self, root) -> None:
        try:
            while True:
                cmd, arg1, arg2 = self._queue.get_nowait()
                if cmd == "quit":
                    root.destroy()
                    return
                elif cmd == "update":
                    self._apply_update(arg1, arg2)
        except queue.Empty:
            pass
        root.after(200, lambda: self._poll_queue(root))

    def _apply_update(self, state: str, elapsed_sec: float) -> None:
        elapsed = int(elapsed_sec)
        mins = elapsed // 60
        secs = elapsed % 60
        self._time_label.config(text=f"{mins:02d}:{secs:02d}")
        self._dot.config(fg=_DOT_COLORS.get(state, "#9e9e9e"))
        self._status_label.config(text=_STATUS_LABELS.get(state, state.upper()))

    def _toggle_expand(self, _event=None) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._expanded_frame.pack(fill="x")
        else:
            self._expanded_frame.pack_forget()

    def _request_stop(self) -> None:
        if self._on_stop:
            self._on_stop()

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _on_drag_start(self, event) -> None:
        self._drag_x = event.x_root - self._root.winfo_x()
        self._drag_y = event.y_root - self._root.winfo_y()

    def _on_drag_motion(self, event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self._root.geometry(f"+{x}+{y}")
