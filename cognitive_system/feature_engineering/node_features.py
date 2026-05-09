"""
Node-level feature extraction for windowed context graphs.

Each output row represents one context node inside one time window. The schema
matches the context-centric graph node model so the viewer can attach the
feature dictionary directly to the clicked node.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

from .graph_builder import GraphBuilder

LOGGER = logging.getLogger(__name__)

NODE_FEATURE_COLUMNS = [
    "session_id",
    "window_id",
    "window_start",
    "window_end",
    "node_id",
    "node_kind",
    "node_type",
    "label",
    "title",
    "url",
    "domain",
    "path",
    "path_depth",
    "app_name",
    "window_title",
    "duration",
    "scroll_intensity",
    "scroll_depth",
    "keystrokes",
    "mouse_activity",
    "revisit_count",
    "switch_in",
    "switch_out",
    "idle_segments",
]


class NodeFeatureBuilder:
    """Build context-node features for each analysis window."""

    def __init__(self) -> None:
        self._graph_builder = GraphBuilder(node_level="app")

    def build(
        self,
        streams: Dict[str, pd.DataFrame],
        windows: pd.DataFrame,
        session_id: str,
    ) -> pd.DataFrame:
        if windows is None or windows.empty:
            return pd.DataFrame(columns=NODE_FEATURE_COLUMNS)

        behavior_df = streams.get("behavior", pd.DataFrame())
        cleaned_events = self._graph_builder.clean_events(behavior_df)
        if cleaned_events.empty:
            return pd.DataFrame(columns=NODE_FEATURE_COLUMNS)

        behavior_sorted = _sort_by_timestamp(behavior_df)
        keyboard_sorted = _sort_by_timestamp(streams.get("keyboard", pd.DataFrame()))
        mouse_sorted = _sort_by_timestamp(streams.get("mouse", pd.DataFrame()))

        rows: list[pd.DataFrame] = []
        for win in windows.itertuples(index=False):
            ws = float(win.window_start)
            we = float(win.window_end)
            window_events = self._graph_builder.slice_events_for_window(cleaned_events, ws, we)
            if window_events.empty:
                continue

            behavior_slice = _slice_timestamp_frame(behavior_sorted, ws, we)
            keyboard_slice = _slice_timestamp_frame(keyboard_sorted, ws, we)
            mouse_slice = _slice_timestamp_frame(mouse_sorted, ws, we)
            nodes_df, _, _ = self._graph_builder.build_from_events(
                window_events,
                behavior_df=behavior_slice,
                keyboard_df=keyboard_slice,
                mouse_df=mouse_slice,
            )
            if nodes_df.empty:
                continue

            nodes_df = nodes_df.copy()
            nodes_df.insert(0, "window_end", we)
            nodes_df.insert(0, "window_start", ws)
            nodes_df.insert(0, "window_id", str(win.window_id))
            nodes_df.insert(0, "session_id", session_id)
            rows.append(nodes_df[NODE_FEATURE_COLUMNS])

        if not rows:
            return pd.DataFrame(columns=NODE_FEATURE_COLUMNS)

        result = pd.concat(rows, ignore_index=True)
        return _finalize(result)

    def export(self, node_features_df: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        out = node_features_df.copy() if node_features_df is not None else pd.DataFrame(columns=NODE_FEATURE_COLUMNS)
        for col in NODE_FEATURE_COLUMNS:
            if col not in out.columns:
                out[col] = pd.Series(dtype=object)
        out[NODE_FEATURE_COLUMNS].to_csv(path, index=False)
        LOGGER.info("Node features: wrote %d rows to %s", len(out), path.name)


def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    numeric_defaults = {
        "path_depth": 0,
        "duration": 0.0,
        "scroll_intensity": 0.0,
        "scroll_depth": 0.0,
        "keystrokes": 0,
        "mouse_activity": 0,
        "revisit_count": 0,
        "switch_in": 0,
        "switch_out": 0,
        "idle_segments": 0,
    }
    for col, default in numeric_defaults.items():
        if col not in out.columns:
            out[col] = default
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(default)
    for col in ("path_depth", "keystrokes", "mouse_activity", "revisit_count", "switch_in", "switch_out", "idle_segments"):
        out[col] = out[col].astype(int)
    for col in ("duration", "scroll_intensity", "scroll_depth"):
        out[col] = out[col].astype(float).round(4)

    text_cols = [
        "session_id",
        "window_id",
        "node_id",
        "node_kind",
        "node_type",
        "label",
        "title",
        "url",
        "domain",
        "path",
        "app_name",
        "window_title",
    ]
    for col in text_cols:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].fillna("").astype(str)
    return out[NODE_FEATURE_COLUMNS]


def _sort_by_timestamp(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["timestamp"] = pd.to_numeric(out["timestamp"], errors="coerce")
    return out.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def _slice_timestamp_frame(df: pd.DataFrame, window_start: float, window_end: float) -> pd.DataFrame:
    if df is None or df.empty or "timestamp" not in df.columns:
        return pd.DataFrame(columns=df.columns if df is not None else [])
    timestamps = df["timestamp"].to_numpy(dtype=float, copy=False)
    lo = int(np.searchsorted(timestamps, float(window_start), side="left"))
    hi = int(np.searchsorted(timestamps, float(window_end), side="left"))
    return df.iloc[lo:hi].reset_index(drop=True)
