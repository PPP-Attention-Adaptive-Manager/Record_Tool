"""
Per-window feature extraction from all behavioral data streams.

Streams covered
---------------
A. behavior      — context switches, focus duration, idle time, scroll
B. keyboard      — keystroke rate, typing burstiness, pause duration
C. mouse         — movement speed, click rate, movement entropy
D. notification  — rate, interruption density, response latency
E. system_metrics— CPU, RAM, network
F. dual_task     — reaction time, miss rate, error rate

Normalization
-------------
Applied per-session after all windows are computed.
Methods: "minmax" (default) or "zscore".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

import numpy as np
import pandas as pd

from .windowing import WindowConfig, WindowEngine

LOGGER = logging.getLogger(__name__)

_EPS = 1e-9

# ── Feature configuration ─────────────────────────────────────────────────────

@dataclass
class FeatureConfig:
    normalization: Literal["minmax", "zscore"] = "minmax"
    pause_threshold_ms: float = 2_000.0
    """Inter-keystroke gap (ms) above which a pause is counted."""
    movement_entropy_bins: int = 8
    """Number of direction bins for mouse movement entropy."""
    min_events_per_window: int = 0
    """Windows with fewer total events across all streams are still kept
    (features default to 0); set > 0 to drop sparse windows."""

_META_COLS = {"window_id", "session_id", "window_start", "window_end"}

# ── Normalizer ────────────────────────────────────────────────────────────────

class Normalizer:
    """Fits on a session's feature DataFrame and transforms in-place."""

    def __init__(self, method: Literal["minmax", "zscore"] = "minmax") -> None:
        self.method = method
        self.params_: Dict[str, dict] = {}

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize all numeric feature columns (excludes metadata columns).
        Returns a new DataFrame; does not modify the input.
        """
        feature_cols = [c for c in df.columns if c not in _META_COLS and pd.api.types.is_numeric_dtype(df[c])]
        result = df.copy()
        for col in feature_cols:
            vals = result[col].astype(float).values
            if self.method == "minmax":
                lo, hi = vals.min(), vals.max()
                span = hi - lo
                result[col] = (vals - lo) / (span + _EPS)
                self.params_[col] = {"min": lo, "max": hi}
            else:  # zscore
                mu, sigma = vals.mean(), vals.std()
                result[col] = (vals - mu) / (sigma + _EPS)
                self.params_[col] = {"mean": mu, "std": sigma}
        return result


# ── Feature extractor ─────────────────────────────────────────────────────────

class FeatureExtractor:
    """
    Extracts a fixed feature vector for each window across all streams.

    Usage
    -----
    >>> extractor = FeatureExtractor()
    >>> features_df = extractor.extract(streams, windows, session_id="s001")
    # streams: dict[stream_name, pd.DataFrame]  (sorted by timestamp)
    # features_df columns: window_id, session_id, window_start, window_end,
    #                       <feature_1>, ..., <feature_n>
    """

    def __init__(self, config: Optional[FeatureConfig] = None) -> None:
        self.config = config or FeatureConfig()
        self._engine = WindowEngine()

    # ── Public API ────────────────────────────────────────────────────────────

    def extract(
        self,
        streams: Dict[str, pd.DataFrame],
        windows: pd.DataFrame,
        session_id: str,
    ) -> pd.DataFrame:
        """
        Compute one feature vector per window row in *windows*.

        Parameters
        ----------
        streams : dict mapping stream name → sorted DataFrame.
            Expected keys (all optional): "behavior", "keyboard", "mouse",
            "notification", "system_metrics", "dual_task".
        windows : DataFrame from WindowEngine.generate().
        session_id : session identifier written into every output row.

        Returns
        -------
        pd.DataFrame with metadata + feature columns; NaN → 0.
        """
        # Pre-sort streams and cache numpy timestamp arrays for searchsorted.
        sorted_streams: Dict[str, pd.DataFrame] = {}
        ts_arrays: Dict[str, np.ndarray] = {}
        for name, df in streams.items():
            if df is not None and not df.empty and "timestamp" in df.columns:
                s = df.sort_values("timestamp").reset_index(drop=True)
                sorted_streams[name] = s
                ts_arrays[name] = s["timestamp"].values

        rows = []
        for _, win in windows.iterrows():
            ws = float(win["window_start"])
            we = float(win["window_end"])
            size_s = we - ws

            # Slice each stream to this window's interval.
            slices: Dict[str, pd.DataFrame] = {}
            for name, df in sorted_streams.items():
                lo, hi = self._engine.window_slice_indices(ts_arrays[name], ws, we)
                slices[name] = df.iloc[lo:hi]

            row: dict = {
                "window_id":    win["window_id"],
                "session_id":   session_id,
                "window_start": ws,
                "window_end":   we,
            }
            row.update(self._behavior_features(slices.get("behavior",     pd.DataFrame()), size_s))
            row.update(self._keyboard_features(slices.get("keyboard",     pd.DataFrame()), size_s))
            row.update(self._mouse_features(   slices.get("mouse",        pd.DataFrame()), size_s))
            row.update(self._notification_features(slices.get("notification", pd.DataFrame()), size_s))
            row.update(self._system_features(  slices.get("system_metrics", pd.DataFrame())))
            row.update(self._dual_task_features(slices.get("dual_task",   pd.DataFrame())))
            rows.append(row)

        if not rows:
            return pd.DataFrame()

        result = pd.DataFrame(rows)
        # Replace NaN / inf with 0 so downstream normalization is always clean.
        feature_cols = [c for c in result.columns if c not in _META_COLS]
        result[feature_cols] = (
            result[feature_cols]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        return result

    # ── A. Behavior ───────────────────────────────────────────────────────────

    def _behavior_features(self, df: pd.DataFrame, size_s: float) -> dict:
        if df.empty or size_s <= 0:
            return _zero_behavior()
        size_ms = size_s * 1_000.0

        # Context-end events carry start_time, end_time, duration_ms.
        ctx = df[df["event_type"] == "context_end"].copy()
        ctx["start_time"] = pd.to_numeric(ctx.get("start_time"), errors="coerce")
        ctx["end_time"]   = pd.to_numeric(ctx.get("end_time"),   errors="coerce")
        ctx = ctx.dropna(subset=["start_time", "end_time"])

        # Compute the overlap of each context interval with this window.
        if not ctx.empty:
            overlap_s  = np.maximum(0.0,
                np.minimum(ctx["end_time"].values,   df["timestamp"].max()) -
                np.maximum(ctx["start_time"].values, df["timestamp"].min())
            )
            overlap_ms = overlap_s * 1_000.0
            idle_mask  = ctx["app_name"].astype(str).str.lower() == "idle"

            focus_ms = float(overlap_ms[~idle_mask].sum())
            idle_ms  = float(overlap_ms[ idle_mask].sum())
        else:
            focus_ms = idle_ms = 0.0

        switch_rate          = ctx.shape[0] / size_s
        focus_duration_ratio = min(1.0, focus_ms / (size_ms + _EPS))
        idle_ratio           = min(1.0, idle_ms  / (size_ms + _EPS))

        # Distinct tab_ids seen in this window across all events.
        tab_ids   = df["tab_id"].dropna() if "tab_id" in df.columns else pd.Series(dtype=object)
        tab_count = int(tab_ids.astype(str).nunique())

        # Scroll intensity: absolute scroll per second.
        scrolls = df[df["event_type"].astype(str).str.contains("scroll", case=False, na=False)]
        scroll_delta = pd.to_numeric(scrolls.get("scroll_delta_y", pd.Series()), errors="coerce")
        scroll_intensity = float(scroll_delta.abs().sum()) / size_s

        return {
            "switch_rate":          switch_rate,
            "focus_duration_ratio": focus_duration_ratio,
            "idle_ratio":           idle_ratio,
            "tab_count_mean":       tab_count,
            "scroll_intensity":     scroll_intensity,
        }

    # ── B. Keyboard ───────────────────────────────────────────────────────────

    def _keyboard_features(self, df: pd.DataFrame, size_s: float) -> dict:
        if df.empty or size_s <= 0:
            return _zero_keyboard()

        presses  = df[df["event_type"] == "key_press"]
        intervals = pd.to_numeric(presses.get("interval_ms", pd.Series()), errors="coerce").dropna()

        keystroke_rate = len(presses) / size_s

        if len(intervals) > 1:
            mu = float(intervals.mean())
            typing_burstiness = float(intervals.std()) / (mu + _EPS)
        else:
            typing_burstiness = 0.0

        pauses = intervals[intervals > self.config.pause_threshold_ms]
        pause_mean = float(pauses.mean()) if not pauses.empty else 0.0

        return {
            "keystroke_rate":     keystroke_rate,
            "typing_burstiness":  typing_burstiness,
            "pause_mean_duration": pause_mean,
        }

    # ── C. Mouse ─────────────────────────────────────────────────────────────

    def _mouse_features(self, df: pd.DataFrame, size_s: float) -> dict:
        if df.empty or size_s <= 0:
            return _zero_mouse()

        moves  = df[df["event_type"] == "mouse_move"]
        clicks = df[df["event_type"].isin(["button_press", "mouse_click", "click"])]

        speed = pd.to_numeric(moves.get("speed", pd.Series()), errors="coerce").dropna()
        movement_speed_mean = float(speed.mean()) if not speed.empty else 0.0
        click_rate = len(clicks) / size_s

        # Movement entropy: Shannon entropy over 8 direction bins.
        movement_entropy = 0.0
        if len(moves) > 1:
            dx = pd.to_numeric(moves.get("delta_x", pd.Series()), errors="coerce").fillna(0).values
            dy = pd.to_numeric(moves.get("delta_y", pd.Series()), errors="coerce").fillna(0).values
            nonzero = (dx != 0) | (dy != 0)
            if nonzero.sum() > 1:
                angles = np.arctan2(dy[nonzero], dx[nonzero])
                n_bins = self.config.movement_entropy_bins
                counts, _ = np.histogram(angles, bins=n_bins, range=(-np.pi, np.pi))
                total = counts.sum()
                if total > 0:
                    p = counts[counts > 0] / total
                    movement_entropy = float(-np.sum(p * np.log2(p + _EPS)))

        return {
            "movement_speed_mean": movement_speed_mean,
            "click_rate":          click_rate,
            "movement_entropy":    movement_entropy,
        }

    # ── D. Notifications ──────────────────────────────────────────────────────

    def _notification_features(self, df: pd.DataFrame, size_s: float) -> dict:
        if df.empty or size_s <= 0:
            return _zero_notifications()

        notification_rate    = len(df) / size_s
        added                = df[df.get("interaction_type", pd.Series(dtype=str)) == "added"] \
                               if "interaction_type" in df.columns else pd.DataFrame()
        interruption_density = len(added) / size_s

        latencies = pd.to_numeric(
            df.get("response_latency_ms", pd.Series()), errors="coerce"
        ).dropna()
        response_latency_mean = float(latencies.mean()) if not latencies.empty else 0.0

        return {
            "notification_rate":    notification_rate,
            "interruption_density": interruption_density,
            "response_latency_mean": response_latency_mean,
        }

    # ── E. System metrics ─────────────────────────────────────────────────────

    def _system_features(self, df: pd.DataFrame) -> dict:
        if df.empty:
            return _zero_system()

        def _col(name: str) -> pd.Series:
            return pd.to_numeric(df.get(name, pd.Series()), errors="coerce").dropna()

        cpu  = _col("cpu_mean")
        ram  = _col("ram_mean")
        net  = _col("network_rate_bps")
        flag = _col("memory_pressure_flag")

        return {
            "cpu_mean":             float(cpu.mean())  if not cpu.empty  else 0.0,
            "cpu_variance":         float(cpu.var())   if len(cpu) > 1   else 0.0,
            "ram_mean":             float(ram.mean())  if not ram.empty  else 0.0,
            "ram_spikes":           float(flag.mean()) if not flag.empty else 0.0,
            "network_load":         float(net.mean())  if not net.empty  else 0.0,
        }

    # ── F. Dual-task ──────────────────────────────────────────────────────────

    def _dual_task_features(self, df: pd.DataFrame) -> dict:
        if df.empty:
            return _zero_dual_task()

        rt = pd.to_numeric(df.get("reaction_time_ms", pd.Series()), errors="coerce").dropna()
        reaction_time_mean = float(rt.mean()) if not rt.empty else 0.0

        success = pd.to_numeric(df.get("success", pd.Series()), errors="coerce").fillna(0)
        miss_rate = 1.0 - float(success.mean()) if not success.empty else 0.0

        if "error" in df.columns:
            error_rate = float(
                (df["error"].astype(str).str.strip().replace("nan", "").str.len() > 0).mean()
            )
        else:
            error_rate = 0.0

        return {
            "reaction_time_mean": reaction_time_mean,
            "miss_rate":          miss_rate,
            "error_rate":         error_rate,
        }


# ── Zero-value defaults (used when a stream is absent or window is empty) ─────

def _zero_behavior()      -> dict: return dict.fromkeys(["switch_rate","focus_duration_ratio","idle_ratio","tab_count_mean","scroll_intensity"], 0.0)
def _zero_keyboard()      -> dict: return dict.fromkeys(["keystroke_rate","typing_burstiness","pause_mean_duration"], 0.0)
def _zero_mouse()         -> dict: return dict.fromkeys(["movement_speed_mean","click_rate","movement_entropy"], 0.0)
def _zero_notifications() -> dict: return dict.fromkeys(["notification_rate","interruption_density","response_latency_mean"], 0.0)
def _zero_system()        -> dict: return dict.fromkeys(["cpu_mean","cpu_variance","ram_mean","ram_spikes","network_load"], 0.0)
def _zero_dual_task()     -> dict: return dict.fromkeys(["reaction_time_mean","miss_rate","error_rate"], 0.0)


FEATURE_COLUMNS: list[str] = (
    list(_zero_behavior())
    + list(_zero_keyboard())
    + list(_zero_mouse())
    + list(_zero_notifications())
    + list(_zero_system())
    + list(_zero_dual_task())
)
