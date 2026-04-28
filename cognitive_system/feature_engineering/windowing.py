"""
Windowing engine for multi-stream behavioral data.

Supports tumbling and sliding windows of configurable sizes.
Window assignment uses binary search (O(W log N)) so large sessions
are processed without nested loops over raw events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

import numpy as np
import pandas as pd


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class WindowConfig:
    """Describes one windowing scheme."""

    size_seconds: int
    """Width of each window in seconds."""

    step_seconds: Optional[int] = None
    """Slide step in seconds.
    None or equal to size_seconds → tumbling (non-overlapping) window."""

    mode: Literal["sliding", "tumbling"] = "tumbling"

    label: str = ""
    """Short label used in output filenames (auto-filled from size if empty)."""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.size_seconds}s"
        if self.mode == "tumbling" or self.step_seconds is None:
            self.step_seconds = self.size_seconds

    @property
    def effective_step(self) -> int:
        return self.step_seconds  # type: ignore[return-value]


# ── Built-in presets ──────────────────────────────────────────────────────────

MICRO_5S   = WindowConfig(size_seconds=5,   mode="tumbling", label="5s")
MESO_30S   = WindowConfig(size_seconds=30,  mode="tumbling", label="30s")
MACRO_120S = WindowConfig(size_seconds=120, mode="tumbling", label="120s")

DEFAULT_WINDOW_CONFIGS: list[WindowConfig] = [MICRO_5S, MESO_30S, MACRO_120S]


# ── Engine ────────────────────────────────────────────────────────────────────

class WindowEngine:
    """
    Generates window boundary intervals and exposes utilities for
    slicing sorted DataFrames into per-window subsets via searchsorted.

    Usage
    -----
    >>> engine = WindowEngine()
    >>> windows = engine.generate(t_start, t_end, MESO_30S)
    # windows: DataFrame[window_id, window_start, window_end]

    # Slice a stream DataFrame for a single window:
    >>> lo, hi = engine.window_slice_indices(ts_array, 1700000030.0, 1700000060.0)
    >>> window_df = stream_df.iloc[lo:hi]
    """

    def generate(
        self,
        t_start: float,
        t_end: float,
        config: WindowConfig,
    ) -> pd.DataFrame:
        """
        Return a DataFrame of window boundary intervals.

        Columns
        -------
        window_id   : str  e.g. "w000042"
        window_start: float  epoch seconds
        window_end  : float  epoch seconds

        Notes
        -----
        - For tumbling windows the last window may be shorter than size_seconds
          (partial window at the end of the session is retained, not dropped).
        - For sliding windows each event can fall in multiple windows.
        """
        size = float(config.size_seconds)
        step = float(config.effective_step)

        if t_end <= t_start:
            return pd.DataFrame(columns=["window_id", "window_start", "window_end"])

        starts = np.arange(t_start, t_end, step)
        ends   = np.minimum(starts + size, t_end)

        return pd.DataFrame(
            {
                "window_id":    [f"w{i:06d}" for i in range(len(starts))],
                "window_start": starts,
                "window_end":   ends,
            }
        )

    @staticmethod
    def window_slice_indices(
        sorted_timestamps: np.ndarray,
        win_start: float,
        win_end: float,
    ) -> Tuple[int, int]:
        """
        Return (lo, hi) indices into a sorted timestamp array for the half-open
        interval [win_start, win_end).  Use df.iloc[lo:hi] to get the slice.
        """
        lo = int(np.searchsorted(sorted_timestamps, win_start, side="left"))
        hi = int(np.searchsorted(sorted_timestamps, win_end,   side="left"))
        return lo, hi

    @staticmethod
    def session_span(
        *dfs: Optional[pd.DataFrame],
        ts_col: str = "timestamp",
    ) -> Tuple[float, float]:
        """
        Infer session [t_start, t_end] from the union of all stream DataFrames.

        Parameters
        ----------
        *dfs : optional DataFrames — None and empty ones are silently skipped.

        Returns
        -------
        (t_start, t_end) as epoch seconds.
        """
        mins, maxs = [], []
        for df in dfs:
            if df is not None and not df.empty and ts_col in df.columns:
                ts = pd.to_numeric(df[ts_col], errors="coerce").dropna()
                if not ts.empty:
                    mins.append(float(ts.min()))
                    maxs.append(float(ts.max()))
        if not mins:
            raise ValueError(
                "No valid timestamp data found in any of the provided stream DataFrames."
            )
        return float(min(mins)), float(max(maxs))
