"""
Per-window feature extraction from multi-stream behavioral data.

This module now treats window features as modality-specific tables instead of
one monolithic output. The supported export groups are:

- behavior
- keyboard
- mouse
- system

The `system` group currently contains both notification-derived and
system-metrics-derived features so the pipeline can keep a compact four-folder
layout while preserving the prior signal set.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Literal, Optional

import numpy as np
import pandas as pd

from .context_model import context_from_json_series, is_internal_app
from .windowing import WindowEngine

LOGGER = logging.getLogger(__name__)

_EPS = 1e-9
_META_COLS = {"window_id", "session_id", "window_start", "window_end"}


@dataclass
class FeatureConfig:
    normalization: Literal["minmax", "zscore"] = "minmax"
    pause_threshold_ms: float = 2_000.0
    """Inter-keystroke gap (ms) above which a pause is counted."""
    movement_entropy_bins: int = 8
    """Number of direction bins for mouse movement entropy."""
    min_events_per_window: int = 0
    """Reserved for future sparse-window filtering."""


BEHAVIOR_FEATURE_COLUMNS = ["switch_rate", "focus_duration_ratio", "idle_ratio", "tab_count_mean", "scroll_intensity"]
KEYBOARD_FEATURE_COLUMNS = ["keystroke_rate", "typing_burstiness", "pause_mean_duration"]
MOUSE_FEATURE_COLUMNS = ["movement_speed_mean", "click_rate", "movement_entropy"]
SYSTEM_FEATURE_COLUMNS = [
    "notification_rate",
    "interruption_density",
    "response_latency_mean",
    "cpu_mean",
    "cpu_variance",
    "ram_mean",
    "ram_spikes",
    "network_load",
]

MODALITY_FEATURE_COLUMNS: dict[str, list[str]] = {
    "behavior": BEHAVIOR_FEATURE_COLUMNS,
    "keyboard": KEYBOARD_FEATURE_COLUMNS,
    "mouse": MOUSE_FEATURE_COLUMNS,
    "system": SYSTEM_FEATURE_COLUMNS,
}


class Normalizer:
    """Fits on one feature DataFrame and returns a normalized copy."""

    def __init__(self, method: Literal["minmax", "zscore"] = "minmax") -> None:
        self.method = method
        self.params_: Dict[str, dict] = {}

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        feature_cols = [c for c in df.columns if c not in _META_COLS and pd.api.types.is_numeric_dtype(df[c])]
        result = df.copy()
        for col in feature_cols:
            vals = result[col].astype(float).values
            if self.method == "minmax":
                lo, hi = vals.min(), vals.max()
                span = hi - lo
                result[col] = (vals - lo) / (span + _EPS)
                self.params_[col] = {"min": lo, "max": hi}
            else:
                mu, sigma = vals.mean(), vals.std()
                result[col] = (vals - mu) / (sigma + _EPS)
                self.params_[col] = {"mean": mu, "std": sigma}
        return result


class FeatureExtractor:
    """
    Extract modality-specific feature tables for each requested window.

    Public methods
    --------------
    - `extract_modalities(...)` returns one DataFrame per modality
    - `combine_modalities(...)` merges modality tables in memory for clustering
    - `extract(...)` remains as a compatibility helper that simply combines the
      modality outputs without writing a monolithic file
    """

    def __init__(self, config: Optional[FeatureConfig] = None) -> None:
        self.config = config or FeatureConfig()
        self._engine = WindowEngine()

    def extract_modalities(
        self,
        streams: Dict[str, pd.DataFrame],
        windows: pd.DataFrame,
        session_id: str,
    ) -> dict[str, pd.DataFrame]:
        """
        Compute one feature table per modality for the supplied windows.
        """
        if windows is None or windows.empty:
            return {
                name: pd.DataFrame(columns=["window_id", "session_id", "window_start", "window_end", *cols])
                for name, cols in MODALITY_FEATURE_COLUMNS.items()
            }

        sorted_streams, ts_arrays = self._prepare_streams(streams)
        rows_by_modality: dict[str, list[dict[str, float | str]]] = {name: [] for name in MODALITY_FEATURE_COLUMNS}

        for _, win in windows.iterrows():
            ws = float(win["window_start"])
            we = float(win["window_end"])
            size_s = we - ws

            slices: Dict[str, pd.DataFrame] = {}
            for name, df in sorted_streams.items():
                if name == "behavior":
                    slices[name] = self._slice_behavior_events(df, ws, we)
                else:
                    lo, hi = self._engine.window_slice_indices(ts_arrays[name], ws, we)
                    slices[name] = df.iloc[lo:hi]

            meta = {
                "window_id": str(win["window_id"]),
                "session_id": session_id,
                "window_start": ws,
                "window_end": we,
            }

            rows_by_modality["behavior"].append({
                **meta,
                **self._behavior_features(slices.get("behavior", pd.DataFrame()), size_s, ws, we),
            })
            rows_by_modality["keyboard"].append({
                **meta,
                **self._keyboard_features(slices.get("keyboard", pd.DataFrame()), size_s),
            })
            rows_by_modality["mouse"].append({
                **meta,
                **self._mouse_features(slices.get("mouse", pd.DataFrame()), size_s),
            })
            rows_by_modality["system"].append({
                **meta,
                **self._notification_features(slices.get("notification", pd.DataFrame()), size_s),
                **self._system_features(slices.get("system_metrics", pd.DataFrame())),
            })

        return {
            modality: self._finalize_feature_frame(rows_by_modality[modality], feature_cols)
            for modality, feature_cols in MODALITY_FEATURE_COLUMNS.items()
        }

    def combine_modalities(self, modality_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Merge modality-specific window features into a single in-memory table.

        This is used by clustering and any downstream consumers that still need
        a full feature vector per window, without forcing the pipeline to write
        a monolithic CSV.
        """
        frames = [df for df in modality_frames.values() if df is not None and not df.empty]
        if not frames:
            return pd.DataFrame(columns=["window_id", "session_id", "window_start", "window_end"])

        combined = frames[0].copy()
        for frame in frames[1:]:
            combined = combined.merge(
                frame,
                on=["window_id", "session_id", "window_start", "window_end"],
                how="outer",
            )

        feature_cols = [c for c in combined.columns if c not in _META_COLS]
        combined[feature_cols] = (
            combined[feature_cols]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        return combined

    def extract(
        self,
        streams: Dict[str, pd.DataFrame],
        windows: pd.DataFrame,
        session_id: str,
    ) -> pd.DataFrame:
        """Compatibility helper: return the in-memory combined feature table."""
        return self.combine_modalities(self.extract_modalities(streams, windows, session_id))

    def _prepare_streams(
        self,
        streams: Dict[str, pd.DataFrame],
    ) -> tuple[dict[str, pd.DataFrame], dict[str, np.ndarray]]:
        sorted_streams: Dict[str, pd.DataFrame] = {}
        ts_arrays: Dict[str, np.ndarray] = {}
        for name, df in streams.items():
            if df is not None and not df.empty and "timestamp" in df.columns:
                sorted_df = df.sort_values("timestamp").reset_index(drop=True)
                sorted_streams[name] = sorted_df
                ts_arrays[name] = sorted_df["timestamp"].to_numpy(dtype=float, copy=False)
        return sorted_streams, ts_arrays

    def _finalize_feature_frame(
        self,
        rows: list[dict[str, float | str]],
        feature_cols: list[str],
    ) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["window_id", "session_id", "window_start", "window_end", *feature_cols])

        result = pd.DataFrame(rows)
        for col in feature_cols:
            if col not in result.columns:
                result[col] = 0.0
        result[feature_cols] = (
            result[feature_cols]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        ordered = ["window_id", "session_id", "window_start", "window_end", *feature_cols]
        return result[ordered]

    def _slice_behavior_events(self, df: pd.DataFrame, window_start: float, window_end: float) -> pd.DataFrame:
        if df is None or df.empty or "timestamp" not in df.columns:
            return pd.DataFrame(columns=df.columns if df is not None else [])

        behavior = df.copy()
        behavior["timestamp"] = pd.to_numeric(behavior["timestamp"], errors="coerce")
        behavior = behavior.dropna(subset=["timestamp"])
        if behavior.empty:
            return behavior

        event_type = behavior.get("event_type", pd.Series(dtype=str)).fillna("").astype(str).str.lower()
        start_time = pd.to_numeric(behavior.get("start_time", pd.Series(dtype=float)), errors="coerce")
        end_time = pd.to_numeric(behavior.get("end_time", pd.Series(dtype=float)), errors="coerce")
        has_interval = event_type.eq("context_end") & start_time.notna() & end_time.notna()
        timestamp_in_window = behavior["timestamp"].ge(window_start) & behavior["timestamp"].lt(window_end)
        interval_overlaps = has_interval & start_time.lt(window_end) & end_time.gt(window_start)
        return behavior[timestamp_in_window | interval_overlaps].copy()

    def _behavior_features(self, df: pd.DataFrame, size_s: float, window_start: float, window_end: float) -> dict[str, float]:
        if df.empty or size_s <= 0:
            return _zero_behavior()
        size_ms = size_s * 1_000.0

        if "app_name" in df.columns:
            df = df[~df["app_name"].map(is_internal_app)].copy()
        if df.empty:
            return _zero_behavior()

        ctx = df[df["event_type"] == "context_end"].copy()
        ctx["start_time"] = pd.to_numeric(ctx.get("start_time"), errors="coerce")
        ctx["end_time"] = pd.to_numeric(ctx.get("end_time"), errors="coerce")
        ctx = ctx.dropna(subset=["start_time", "end_time"])

        if not ctx.empty:
            overlap_s = np.maximum(
                0.0,
                np.minimum(ctx["end_time"].values, window_end)
                - np.maximum(ctx["start_time"].values, window_start),
            )
            overlap_ms = overlap_s * 1_000.0
            idle_mask = ctx["app_name"].astype(str).str.lower() == "idle"
            focus_ms = float(overlap_ms[~idle_mask].sum())
            idle_ms = float(overlap_ms[idle_mask].sum())
        else:
            focus_ms = 0.0
            idle_ms = 0.0

        switch_rate = ctx.shape[0] / size_s
        focus_duration_ratio = min(1.0, focus_ms / (size_ms + _EPS))
        idle_ratio = min(1.0, idle_ms / (size_ms + _EPS))

        tab_ids = df["tab_id"].dropna() if "tab_id" in df.columns else pd.Series(dtype=object)
        tab_count = int(tab_ids.astype(str).nunique())

        scrolls = df[df["event_type"].astype(str).str.contains("scroll", case=False, na=False)]
        scroll_delta = pd.to_numeric(scrolls.get("scroll_delta_y", pd.Series(dtype=float)), errors="coerce")
        scroll_intensity = float(scroll_delta.abs().sum()) / size_s

        return {
            "switch_rate": switch_rate,
            "focus_duration_ratio": focus_duration_ratio,
            "idle_ratio": idle_ratio,
            "tab_count_mean": float(tab_count),
            "scroll_intensity": scroll_intensity,
        }

    def _keyboard_features(self, df: pd.DataFrame, size_s: float) -> dict[str, float]:
        if df.empty or size_s <= 0:
            return _zero_keyboard()
        df = self._exclude_internal_context(df)
        if df.empty:
            return _zero_keyboard()

        presses = df[df["event_type"] == "key_press"]
        intervals = pd.to_numeric(presses.get("interval_ms", pd.Series(dtype=float)), errors="coerce").dropna()

        keystroke_rate = len(presses) / size_s
        if len(intervals) > 1:
            mu = float(intervals.mean())
            typing_burstiness = float(intervals.std()) / (mu + _EPS)
        else:
            typing_burstiness = 0.0

        pauses = intervals[intervals > self.config.pause_threshold_ms]
        pause_mean = float(pauses.mean()) if not pauses.empty else 0.0

        return {
            "keystroke_rate": keystroke_rate,
            "typing_burstiness": typing_burstiness,
            "pause_mean_duration": pause_mean,
        }

    def _mouse_features(self, df: pd.DataFrame, size_s: float) -> dict[str, float]:
        if df.empty or size_s <= 0:
            return _zero_mouse()
        df = self._exclude_internal_context(df)
        if df.empty:
            return _zero_mouse()

        moves = df[df["event_type"] == "mouse_move"]
        clicks = df[df["event_type"].isin(["button_press", "mouse_click", "click", "mouse_press"])]

        speed = pd.to_numeric(moves.get("speed", pd.Series(dtype=float)), errors="coerce").dropna()
        movement_speed_mean = float(speed.mean()) if not speed.empty else 0.0
        click_rate = len(clicks) / size_s

        movement_entropy = 0.0
        if len(moves) > 1:
            dx = pd.to_numeric(moves.get("delta_x", pd.Series(dtype=float)), errors="coerce").fillna(0).values
            dy = pd.to_numeric(moves.get("delta_y", pd.Series(dtype=float)), errors="coerce").fillna(0).values
            nonzero = (dx != 0) | (dy != 0)
            if nonzero.sum() > 1:
                angles = np.arctan2(dy[nonzero], dx[nonzero])
                counts, _ = np.histogram(
                    angles,
                    bins=self.config.movement_entropy_bins,
                    range=(-np.pi, np.pi),
                )
                total = counts.sum()
                if total > 0:
                    p = counts[counts > 0] / total
                    movement_entropy = float(-np.sum(p * np.log2(p + _EPS)))

        return {
            "movement_speed_mean": movement_speed_mean,
            "click_rate": click_rate,
            "movement_entropy": movement_entropy,
        }

    def _notification_features(self, df: pd.DataFrame, size_s: float) -> dict[str, float]:
        if df.empty or size_s <= 0:
            return _zero_notifications()

        notification_rate = len(df) / size_s
        added = (
            df[df.get("interaction_type", pd.Series(dtype=str)) == "added"]
            if "interaction_type" in df.columns
            else pd.DataFrame()
        )
        interruption_density = len(added) / size_s

        latencies = pd.to_numeric(
            df.get("response_latency_ms", pd.Series(dtype=float)),
            errors="coerce",
        ).dropna()
        response_latency_mean = float(latencies.mean()) if not latencies.empty else 0.0

        return {
            "notification_rate": notification_rate,
            "interruption_density": interruption_density,
            "response_latency_mean": response_latency_mean,
        }

    def _system_features(self, df: pd.DataFrame) -> dict[str, float]:
        if df.empty:
            return _zero_system()

        def _col(name: str) -> pd.Series:
            return pd.to_numeric(df.get(name, pd.Series(dtype=float)), errors="coerce").dropna()

        cpu = _col("cpu_mean")
        ram = _col("ram_mean")
        net = _col("network_rate_bps")
        flag = _col("memory_pressure_flag")

        return {
            "cpu_mean": float(cpu.mean()) if not cpu.empty else 0.0,
            "cpu_variance": float(cpu.var()) if len(cpu) > 1 else 0.0,
            "ram_mean": float(ram.mean()) if not ram.empty else 0.0,
            "ram_spikes": float(flag.mean()) if not flag.empty else 0.0,
            "network_load": float(net.mean()) if not net.empty else 0.0,
        }

    def _exclude_internal_context(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "context" not in df.columns:
            return df
        contexts = context_from_json_series(df["context"])
        if contexts.empty or "active_app" not in contexts.columns:
            return df
        internal_mask = contexts["active_app"].map(is_internal_app).fillna(False).to_numpy(dtype=bool)
        if not internal_mask.any():
            return df
        return df.loc[~internal_mask].copy()


def _zero_behavior() -> dict[str, float]:
    return dict.fromkeys(BEHAVIOR_FEATURE_COLUMNS, 0.0)


def _zero_keyboard() -> dict[str, float]:
    return dict.fromkeys(KEYBOARD_FEATURE_COLUMNS, 0.0)


def _zero_mouse() -> dict[str, float]:
    return dict.fromkeys(MOUSE_FEATURE_COLUMNS, 0.0)


def _zero_notifications() -> dict[str, float]:
    return {
        "notification_rate": 0.0,
        "interruption_density": 0.0,
        "response_latency_mean": 0.0,
    }


def _zero_system() -> dict[str, float]:
    return {
        "cpu_mean": 0.0,
        "cpu_variance": 0.0,
        "ram_mean": 0.0,
        "ram_spikes": 0.0,
        "network_load": 0.0,
    }


FEATURE_COLUMNS: list[str] = (
    BEHAVIOR_FEATURE_COLUMNS
    + KEYBOARD_FEATURE_COLUMNS
    + MOUSE_FEATURE_COLUMNS
    + SYSTEM_FEATURE_COLUMNS
)
