import random
import time
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import tkinter as tk
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    logger.warning("tkinter unavailable - dual-task UI disabled")


class DualTask:
    """Visual 2-back task displayed in a floating window during experimental sessions."""

    def __init__(self, on_response: Callable, n_back: int = 2):
        self.on_response = on_response
        self.n_back = n_back
        self._active = False
        self._sequence: list = []
        self._window = None
        self._label = None
        self._thread: Optional[threading.Thread] = None
        self._last_stimulus_time: Optional[float] = None

    def start(self):
        if not TKINTER_AVAILABLE:
            logger.warning("Dual-task disabled (tkinter missing)")
            return
        self._active = True
        self._thread = threading.Thread(target=self._run_gui, daemon=True)
        self._thread.start()
        logger.info("Dual-task started")

    def stop(self):
        self._active = False
        if self._window:
            try:
                self._window.after(0, self._window.destroy)
            except Exception:
                pass
        logger.info("Dual-task stopped")

    def _run_gui(self):
        root = tk.Tk()
        self._window = root
        root.title(f"{self.n_back}-Back Task")
        root.geometry("280x180+50+50")
        root.configure(bg="#0d0d1a")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        tk.Label(root, text=f"{self.n_back}-BACK TASK",
                 font=("Arial", 9, "bold"), bg="#0d0d1a", fg="#555577").pack(pady=(8, 0))

        self._label = tk.Label(root, text=" ", font=("Courier", 72, "bold"),
                               bg="#0d0d1a", fg="#e94560", width=3)
        self._label.pack(expand=True)

        tk.Label(root, text="SPACE = match  |  ignore = no match",
                 font=("Arial", 8), bg="#0d0d1a", fg="#666688").pack(pady=(0, 8))

        root.bind("<space>", self._on_space)
        self._schedule_next_stimulus(root)
        root.mainloop()

    def _schedule_next_stimulus(self, root):
        if not self._active:
            return
        delay_ms = random.randint(2000, 4000)
        root.after(delay_ms, lambda: self._show_stimulus(root))

    def _show_stimulus(self, root):
        if not self._active:
            return
        letter = self._pick_letter()
        self._sequence.append(letter)
        self._last_stimulus_time = time.time()
        self._label.config(text=letter)
        root.after(800, lambda: self._label.config(text=" ") if self._active else None)
        self._schedule_next_stimulus(root)

    def _pick_letter(self) -> str:
        pool = "BCDFGHJKLMNPQRSTVWXZ"
        if len(self._sequence) >= self.n_back and random.random() < 0.3:
            return self._sequence[-self.n_back]
        return random.choice(pool)

    def _on_space(self, event):
        now = time.time()
        if not self._sequence:
            return
        reaction_ms = round((now - self._last_stimulus_time) * 1000, 1) if self._last_stimulus_time else 0.0
        target = self._sequence[-self.n_back] if len(self._sequence) >= self.n_back else None
        is_correct = (self._sequence[-1] == target) if target else False
        self.on_response({
            "timestamp": now,
            "event_type": "dual_task_response",
            "letter": self._sequence[-1],
            "n_back_target": target or "",
            "is_correct": is_correct,
            "reaction_time_ms": reaction_ms,
        })
