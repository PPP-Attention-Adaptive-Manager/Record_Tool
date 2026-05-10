"""
Temporal event-to-event graph builder for behavioral context logs.

The graph represents sequential state transitions:

    event[i] -> event[i + 1]

Each edge is derived from chronologically ordered behavior events, never from
window IDs. Windows remain available only as a secondary slicing layer that
builds smaller event-transition graphs inside each window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from .context_model import (
    context_from_json_series,
    is_internal_context,
    resolve_context_from_payload,
    resolve_context_nodes_frame,
    resolve_context_node,
)

LOGGER = logging.getLogger(__name__)

NODE_LEVEL = ["app", "domain", "url"]

_CANONICAL_EVENT_TYPES = frozenset({"focus", "switch", "context_end"})
_EVENT_TYPE_ALIASES = {
    "active": "focus",
    "app_focus": "focus",
    "context_end": "context_end",
    "focus": "focus",
    "navigation": "switch",
    "new_tab": "switch",
    "switch": "switch",
    "tab_switch": "switch",
    "window_switch": "switch",
}

_STRING_COLUMNS = ["app_name", "url", "window_title", "title", "tab_id", "extra"]
_NUMERIC_COLUMNS = ["timestamp", "start_time", "end_time", "duration_ms"]
_EVENT_COLUMNS = [
    "timestamp",
    "event_type",
    "app_name",
    "url",
    "duration_ms",
    "start_time",
    "end_time",
    "window_title",
    "title",
    "tab_id",
    "node_id",
    "node_type",
    "node_kind",
    "label",
    "domain",
    "path",
    "path_depth",
]

_NODE_FEATURE_COLUMNS = [
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
_NODE_METADATA_COLUMNS = [
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
]
_GLOBAL_NODE_COLUMNS = [*_NODE_METADATA_COLUMNS, *_NODE_FEATURE_COLUMNS]
_GLOBAL_EDGE_COLUMNS = ["source", "target", "edge_type", "transition_count", "total_duration", "avg_duration"]
_GLOBAL_TEMPORAL_COLUMNS = ["source", "target", "edge_type", "timestamp"]
_WINDOW_NODE_COLUMNS = ["window_id", *_GLOBAL_NODE_COLUMNS]
_WINDOW_EDGE_COLUMNS = ["window_id", *_GLOBAL_EDGE_COLUMNS]
_WINDOW_TEMPORAL_COLUMNS = ["window_id", *_GLOBAL_TEMPORAL_COLUMNS]


@dataclass(frozen=True)
class GraphConfig:
    """Configuration for temporal event graph construction."""

    node_level: Literal["app", "domain", "url"] = "app"

    def __post_init__(self) -> None:
        if self.node_level not in NODE_LEVEL:
            raise ValueError(f"node_level must be one of {NODE_LEVEL}, got {self.node_level!r}")


class GraphBuilder:
    """
    Build behavioral temporal graphs from cleaned behavior events.

    Parameters
    ----------
    node_level:
        Event state granularity. Supported values are defined in `NODE_LEVEL`:
        `app`, `domain`, and `url`.
    """

    def __init__(self, node_level: Literal["app", "domain", "url"] = "app") -> None:
        self.config = GraphConfig(node_level=node_level)

    def clean_events(self, behavior_df: pd.DataFrame) -> pd.DataFrame:
        """
        Collapse fragmented rows into complete events, filter to transition-like
        events, and attach node IDs according to the configured granularity.
        """
        if behavior_df is None or behavior_df.empty:
            return pd.DataFrame(columns=_EVENT_COLUMNS)

        df = behavior_df.copy()

        for col in _STRING_COLUMNS:
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].fillna("").astype(str).str.strip()

        for col in _NUMERIC_COLUMNS:
            if col not in df.columns:
                df[col] = np.nan
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "event_type" not in df.columns:
            return pd.DataFrame(columns=_EVENT_COLUMNS)

        df["event_type"] = (
            df["event_type"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map(lambda value: _EVENT_TYPE_ALIASES.get(value, value))
        )
        df = df[df["event_type"].isin(_CANONICAL_EVENT_TYPES)].copy()
        df = df.dropna(subset=["timestamp"])
        df = df[~_internal_context_mask(df)].copy()
        if df.empty:
            return pd.DataFrame(columns=_EVENT_COLUMNS)

        df["end_time"] = df["end_time"].fillna(df["timestamp"])

        duration_s = df["duration_ms"] / 1_000.0
        inferred_start = df["end_time"] - duration_s
        df["start_time"] = df["start_time"].fillna(inferred_start)
        df["start_time"] = df["start_time"].fillna(df["timestamp"])

        # Group fragments using temporal anchors instead of app/url so rows
        # that carry complementary fields still collapse into one event.
        df["_anchor_start"] = np.where(
            df["event_type"].eq("context_end"),
            df["start_time"],
            df["timestamp"],
        )
        df["_anchor_end"] = np.where(
            df["event_type"].eq("context_end"),
            df["end_time"],
            df["timestamp"],
        )

        df = df.sort_values(["_anchor_start", "_anchor_end", "timestamp"]).reset_index(drop=True)
        grouped = (
            df.groupby(["event_type", "_anchor_start", "_anchor_end"], dropna=False, as_index=False)
            .agg(
                timestamp=("timestamp", "max"),
                app_name=("app_name", _first_non_blank),
                url=("url", _first_non_blank),
                duration_ms=("duration_ms", "max"),
                start_time=("start_time", "min"),
                end_time=("end_time", "max"),
                window_title=("window_title", _first_non_blank),
                title=("title", _first_non_blank),
                tab_id=("tab_id", _first_non_blank),
            )
        )

        grouped = grouped.sort_values("timestamp").reset_index(drop=True)
        next_timestamp = grouped["timestamp"].shift(-1)
        fallback_duration_ms = ((next_timestamp - grouped["timestamp"]) * 1_000.0).clip(lower=0)
        interval_duration_ms = ((grouped["end_time"] - grouped["start_time"]) * 1_000.0).clip(lower=0)

        grouped["duration_ms"] = grouped["duration_ms"].where(grouped["duration_ms"].gt(0))
        grouped["duration_ms"] = grouped["duration_ms"].fillna(interval_duration_ms)
        grouped["duration_ms"] = grouped["duration_ms"].fillna(fallback_duration_ms)
        grouped["duration_ms"] = grouped["duration_ms"].fillna(0.0).clip(lower=0).round(2)

        missing_start = grouped["start_time"].isna()
        grouped.loc[missing_start, "start_time"] = (
            grouped.loc[missing_start, "end_time"]
            - (grouped.loc[missing_start, "duration_ms"] / 1_000.0)
        )
        grouped["start_time"] = grouped["start_time"].fillna(grouped["timestamp"])
        grouped["end_time"] = grouped["end_time"].fillna(grouped["timestamp"])

        grouped["app_name"] = grouped["app_name"].replace("", pd.NA)
        grouped["url"] = grouped["url"].replace("", pd.NA)
        grouped["app_name"] = grouped["app_name"].fillna("unknown")
        grouped["url"] = grouped["url"].fillna("")
        grouped = grouped[grouped["duration_ms"].gt(0)].copy()
        if grouped.empty:
            return pd.DataFrame(columns=_EVENT_COLUMNS)

        node_frame = self._resolve_nodes(grouped)
        cleaned = pd.concat([grouped, node_frame], axis=1)
        cleaned = cleaned[cleaned["node_id"].notna() & cleaned["node_id"].astype(str).str.len().gt(0)]

        cleaned = cleaned[_EVENT_COLUMNS].reset_index(drop=True)
        LOGGER.info("Graph: cleaned %d behavior events for node_level=%s", len(cleaned), self.config.node_level)
        return cleaned

    def build(
        self,
        behavior_df: pd.DataFrame,
        keyboard_df: pd.DataFrame | None = None,
        mouse_df: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Clean a behavior stream and return:
          cleaned_events, nodes.csv DataFrame, edges.csv DataFrame, temporal_edges.csv DataFrame
        """
        events_df = self.clean_events(behavior_df)
        nodes_df, edges_df, temporal_edges_df = self.build_from_events(
            events_df,
            behavior_df=behavior_df,
            keyboard_df=keyboard_df,
            mouse_df=mouse_df,
        )
        return events_df, nodes_df, edges_df, temporal_edges_df

    def build_from_events(
        self,
        events_df: pd.DataFrame,
        behavior_df: pd.DataFrame | None = None,
        keyboard_df: pd.DataFrame | None = None,
        mouse_df: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Build graph tables from a cleaned event table."""
        if events_df is None or events_df.empty:
            return (
                pd.DataFrame(columns=_GLOBAL_NODE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_EDGE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_TEMPORAL_COLUMNS),
            )

        events_df = events_df.copy()
        events_df["duration_ms"] = pd.to_numeric(events_df.get("duration_ms"), errors="coerce").fillna(0.0)
        events_df = events_df[events_df["duration_ms"].gt(0)].reset_index(drop=True)
        if events_df.empty:
            return (
                pd.DataFrame(columns=_GLOBAL_NODE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_EDGE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_TEMPORAL_COLUMNS),
            )

        transitions = self._build_temporal_transitions(events_df)
        node_features = self._build_node_feature_table(
            events_df=events_df,
            transitions=transitions,
            behavior_df=behavior_df,
            keyboard_df=keyboard_df,
            mouse_df=mouse_df,
        )

        if transitions.empty:
            return (
                node_features,
                pd.DataFrame(columns=_GLOBAL_EDGE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_TEMPORAL_COLUMNS),
            )

        temporal_edges_df = transitions[["source", "target", "edge_type", "timestamp"]].reset_index(drop=True)
        edges_df = (
            transitions.groupby(["source", "target", "edge_type"], as_index=False)
            .agg(
                transition_count=("timestamp", "size"),
                total_duration=("duration_ms", "sum"),
                avg_duration=("duration_ms", "mean"),
            )
        )
        edges_df["total_duration"] = edges_df["total_duration"].round(2)
        edges_df["avg_duration"] = edges_df["avg_duration"].round(2)
        return node_features, edges_df[_GLOBAL_EDGE_COLUMNS], temporal_edges_df[_GLOBAL_TEMPORAL_COLUMNS]

    def build_windowed(
        self,
        events_df: pd.DataFrame,
        windows_df: pd.DataFrame,
        behavior_df: pd.DataFrame | None = None,
        keyboard_df: pd.DataFrame | None = None,
        mouse_df: pd.DataFrame | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Build per-window event-transition graphs while keeping events, not
        windows, as the actual graph nodes.
        """
        if (
            events_df is None
            or events_df.empty
            or windows_df is None
            or windows_df.empty
        ):
            return (
                pd.DataFrame(columns=_WINDOW_NODE_COLUMNS),
                pd.DataFrame(columns=_WINDOW_EDGE_COLUMNS),
                pd.DataFrame(columns=_WINDOW_TEMPORAL_COLUMNS),
            )

        sorted_behavior = _sort_by_timestamp(behavior_df)
        sorted_keyboard = _sort_by_timestamp(keyboard_df)
        sorted_mouse = _sort_by_timestamp(mouse_df)
        window_nodes: list[pd.DataFrame] = []
        window_edges: list[pd.DataFrame] = []
        window_temporal_edges: list[pd.DataFrame] = []

        for window in windows_df.itertuples(index=False):
            window_start = float(window.window_start)
            window_end = float(window.window_end)
            window_events = self.slice_events_for_window(events_df, window_start, window_end)
            if window_events.empty:
                continue

            behavior_slice = _slice_timestamp_frame(sorted_behavior, window_start, window_end)
            keyboard_slice = _slice_timestamp_frame(sorted_keyboard, window_start, window_end)
            mouse_slice = _slice_timestamp_frame(sorted_mouse, window_start, window_end)

            nodes_df, edges_df, temporal_edges_df = self.build_from_events(
                window_events,
                behavior_df=behavior_slice,
                keyboard_df=keyboard_slice,
                mouse_df=mouse_slice,
            )

            if not nodes_df.empty:
                nodes_df = nodes_df.copy()
                nodes_df.insert(0, "window_id", window.window_id)
                window_nodes.append(nodes_df[_WINDOW_NODE_COLUMNS])

            if not edges_df.empty:
                edges_df = edges_df.copy()
                edges_df.insert(0, "window_id", window.window_id)
                window_edges.append(edges_df[_WINDOW_EDGE_COLUMNS])

            if not temporal_edges_df.empty:
                temporal_edges_df = temporal_edges_df.copy()
                temporal_edges_df.insert(0, "window_id", window.window_id)
                window_temporal_edges.append(temporal_edges_df[_WINDOW_TEMPORAL_COLUMNS])

        return (
            _concat_or_empty(window_nodes, _WINDOW_NODE_COLUMNS),
            _concat_or_empty(window_edges, _WINDOW_EDGE_COLUMNS),
            _concat_or_empty(window_temporal_edges, _WINDOW_TEMPORAL_COLUMNS),
        )

    @staticmethod
    def slice_events_for_window(events_df: pd.DataFrame, window_start: float, window_end: float) -> pd.DataFrame:
        """Return context intervals overlapping a window, with duration clipped to the window."""
        if events_df is None or events_df.empty:
            return pd.DataFrame(columns=_EVENT_COLUMNS)

        events = events_df.copy()
        for col in ("start_time", "end_time", "timestamp", "duration_ms"):
            if col in events.columns:
                events[col] = pd.to_numeric(events[col], errors="coerce")
        events["start_time"] = events.get("start_time", events.get("timestamp")).fillna(events.get("timestamp"))
        events["end_time"] = events.get("end_time", events.get("timestamp")).fillna(events.get("timestamp"))
        mask = events["start_time"].lt(window_end) & events["end_time"].gt(window_start)
        events = events[mask].copy()
        if events.empty:
            return events.reset_index(drop=True)

        clipped_start = np.maximum(events["start_time"].to_numpy(dtype=float), float(window_start))
        clipped_end = np.minimum(events["end_time"].to_numpy(dtype=float), float(window_end))
        clipped_duration_ms = np.maximum(0.0, clipped_end - clipped_start) * 1_000.0
        events["start_time"] = clipped_start
        events["end_time"] = clipped_end
        events["timestamp"] = clipped_end
        events["duration_ms"] = clipped_duration_ms.round(2)
        events = events[events["duration_ms"].gt(0)].copy()
        return events.sort_values(["start_time", "end_time", "timestamp"]).reset_index(drop=True)

    def export(
        self,
        nodes_df: pd.DataFrame,
        edges_df: pd.DataFrame,
        temporal_edges_df: pd.DataFrame,
        out_dir: Path,
    ) -> None:
        """Write the main session graph tables."""
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(nodes_df, out_dir / "nodes.csv", _GLOBAL_NODE_COLUMNS)
        _write_csv(edges_df, out_dir / "edges.csv", _GLOBAL_EDGE_COLUMNS)
        _write_csv(temporal_edges_df, out_dir / "temporal_edges.csv", _GLOBAL_TEMPORAL_COLUMNS)
        LOGGER.info(
            "Graph: wrote session graph to %s (%d nodes, %d aggregated edges, %d temporal edges)",
            out_dir,
            len(nodes_df),
            len(edges_df),
            len(temporal_edges_df),
        )

    def export_windowed(
        self,
        nodes_df: pd.DataFrame,
        edges_df: pd.DataFrame,
        temporal_edges_df: pd.DataFrame,
        out_dir: Path,
    ) -> None:
        """Write per-window event graph tables."""
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(nodes_df, out_dir / "nodes.csv", _WINDOW_NODE_COLUMNS)
        _write_csv(edges_df, out_dir / "edges.csv", _WINDOW_EDGE_COLUMNS)
        _write_csv(temporal_edges_df, out_dir / "temporal_edges.csv", _WINDOW_TEMPORAL_COLUMNS)
        LOGGER.info(
            "Graph: wrote windowed graph to %s (%d node rows, %d edge rows, %d temporal rows)",
            out_dir,
            len(nodes_df),
            len(edges_df),
            len(temporal_edges_df),
        )

    def _resolve_nodes(self, events_df: pd.DataFrame) -> pd.DataFrame:
        nodes = resolve_context_nodes_frame(events_df)
        return nodes[["node_id", "node_type", "node_kind", "label", "domain", "path", "path_depth"]]

    def _build_node_feature_table(
        self,
        events_df: pd.DataFrame,
        transitions: pd.DataFrame,
        behavior_df: pd.DataFrame | None,
        keyboard_df: pd.DataFrame | None,
        mouse_df: pd.DataFrame | None,
    ) -> pd.DataFrame:
        metadata = (
            events_df.groupby("node_id", as_index=False)
            .agg(
                node_kind=("node_kind", _first_non_blank),
                node_type=("node_type", _first_non_blank),
                label=("label", _first_non_blank),
                title=("title", _first_non_blank),
                url=("url", _first_non_blank),
                domain=("domain", _first_non_blank),
                path=("path", _first_non_blank),
                path_depth=("path_depth", "max"),
                app_name=("app_name", _first_non_blank),
                window_title=("window_title", _first_non_blank),
                duration_ms=("duration_ms", "sum"),
                interval_count=("node_id", "size"),
            )
        )

        metadata["duration"] = (pd.to_numeric(metadata["duration_ms"], errors="coerce").fillna(0.0) / 1_000.0).round(3)
        metadata["revisit_count"] = pd.to_numeric(metadata["interval_count"], errors="coerce").fillna(0).astype(int)
        browser_node_mask = metadata["node_kind"].astype(str).isin({"tab", "page"})
        metadata.loc[browser_node_mask, "app_name"] = ""

        idle_events = events_df[events_df["app_name"].fillna("").astype(str).str.lower().eq("idle")]
        idle_map = idle_events.groupby("node_id").size().to_dict() if not idle_events.empty else {}
        metadata["idle_segments"] = metadata["node_id"].map(lambda node_id: int(idle_map.get(node_id, 0)))

        transition_rows = transitions[transitions.get("edge_type", pd.Series(dtype=str)).eq("transition")]
        switch_in = transition_rows.groupby("target").size().to_dict() if not transition_rows.empty else {}
        switch_out = transition_rows.groupby("source").size().to_dict() if not transition_rows.empty else {}
        metadata["switch_in"] = metadata["node_id"].map(lambda node_id: int(switch_in.get(node_id, 0)))
        metadata["switch_out"] = metadata["node_id"].map(lambda node_id: int(switch_out.get(node_id, 0)))

        scroll_features = self._build_scroll_feature_map(behavior_df, mouse_df, events_df)
        keystroke_counts = self._build_keyboard_count_map(keyboard_df, events_df)
        mouse_counts = self._build_mouse_count_map(mouse_df, events_df)

        duration_by_node = metadata.set_index("node_id")["duration"].to_dict()
        metadata["scroll_intensity"] = metadata["node_id"].map(
            lambda node_id: round(
                float(scroll_features.get(node_id, {}).get("scroll_total", 0.0))
                / max(float(duration_by_node.get(node_id, 0.0)), 0.001),
                4,
            )
        )
        metadata["scroll_depth"] = metadata["node_id"].map(
            lambda node_id: round(float(scroll_features.get(node_id, {}).get("scroll_depth", 0.0)), 4)
        )
        metadata["keystrokes"] = metadata["node_id"].map(lambda node_id: int(keystroke_counts.get(node_id, 0)))
        metadata["mouse_activity"] = metadata["node_id"].map(lambda node_id: int(mouse_counts.get(node_id, 0)))

        metadata["path_depth"] = pd.to_numeric(metadata["path_depth"], errors="coerce").fillna(0).astype(int)
        return metadata[_GLOBAL_NODE_COLUMNS].sort_values(["node_kind", "label", "node_id"]).reset_index(drop=True)

    def _build_scroll_feature_map(
        self,
        behavior_df: pd.DataFrame | None,
        mouse_df: pd.DataFrame | None,
        events_df: pd.DataFrame,
    ) -> dict[str, dict[str, float]]:
        frames: list[pd.DataFrame] = []

        behavior_scrolls = self._prepare_behavior_scroll_rows(behavior_df, events_df)
        if not behavior_scrolls.empty:
            frames.append(behavior_scrolls)

        mouse_scrolls = self._prepare_mouse_scroll_rows(mouse_df, events_df)
        if not mouse_scrolls.empty:
            frames.append(mouse_scrolls)

        if not frames:
            return {}

        scrolls = pd.concat(frames, ignore_index=True)
        if scrolls.empty:
            return {}

        grouped = (
            scrolls.groupby("node_id")
            .agg(
                scroll_total=("scroll_delta_y", lambda values: float(values.abs().sum())),
                scroll_depth=("scroll_total_y", lambda values: float(values.abs().max()) if len(values) else 0.0),
            )
        )
        return grouped.to_dict(orient="index")

    def _prepare_behavior_scroll_rows(
        self,
        behavior_df: pd.DataFrame | None,
        events_df: pd.DataFrame,
    ) -> pd.DataFrame:
        if behavior_df is None or behavior_df.empty or "timestamp" not in behavior_df.columns:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls = behavior_df.copy()
        scrolls["event_type"] = scrolls.get("event_type", pd.Series(dtype=str)).fillna("").astype(str)
        scrolls = scrolls[scrolls["event_type"].str.contains("scroll", case=False, na=False)].copy()
        if scrolls.empty:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls["timestamp"] = pd.to_numeric(scrolls["timestamp"], errors="coerce")
        scrolls = scrolls.dropna(subset=["timestamp"]).copy()
        if "app_name" not in scrolls.columns:
            scrolls["app_name"] = ""
        scrolls = scrolls[~_internal_context_mask(scrolls)].copy()
        if scrolls.empty:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls["node_id"] = self._resolve_row_nodes_with_interval_fallback(scrolls, events_df)
        scrolls = scrolls[scrolls["node_id"].astype(str).str.len().gt(0)].copy()
        if scrolls.empty:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls["scroll_delta_y"] = pd.to_numeric(
            scrolls.get("scroll_delta_y", pd.Series(dtype=float)),
            errors="coerce",
        ).fillna(0.0)
        scrolls["scroll_total_y"] = pd.to_numeric(
            scrolls.get("scroll_total_y", pd.Series(dtype=float)),
            errors="coerce",
        ).fillna(0.0)
        return scrolls[["node_id", "scroll_delta_y", "scroll_total_y"]]

    def _prepare_mouse_scroll_rows(
        self,
        mouse_df: pd.DataFrame | None,
        events_df: pd.DataFrame,
    ) -> pd.DataFrame:
        if mouse_df is None or mouse_df.empty or "timestamp" not in mouse_df.columns:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls = mouse_df.copy()
        scrolls["timestamp"] = pd.to_numeric(scrolls["timestamp"], errors="coerce")
        scrolls = scrolls.dropna(subset=["timestamp"]).copy()
        if scrolls.empty or "event_type" not in scrolls.columns:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls = scrolls[scrolls["event_type"].fillna("").astype(str).str.lower().eq("mouse_scroll")].copy()
        if scrolls.empty:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        scrolls["node_id"] = self._resolve_context_json_nodes_with_interval_fallback(scrolls, events_df)
        scrolls = scrolls[scrolls["node_id"].astype(str).str.len().gt(0)].copy()
        if scrolls.empty:
            return pd.DataFrame(columns=["node_id", "scroll_delta_y", "scroll_total_y"])

        dy = pd.to_numeric(scrolls.get("delta_y", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        dx = pd.to_numeric(scrolls.get("delta_x", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        scrolls["scroll_delta_y"] = dy.where(dy.abs().ge(dx.abs()), dx)
        scrolls["scroll_total_y"] = scrolls.groupby("node_id")["scroll_delta_y"].cumsum().abs()
        return scrolls[["node_id", "scroll_delta_y", "scroll_total_y"]]

    def _build_keyboard_count_map(
        self,
        keyboard_df: pd.DataFrame | None,
        events_df: pd.DataFrame,
    ) -> dict[str, int]:
        if keyboard_df is None or keyboard_df.empty or "timestamp" not in keyboard_df.columns:
            return {}
        keys = keyboard_df.copy()
        keys["timestamp"] = pd.to_numeric(keys["timestamp"], errors="coerce")
        keys = keys.dropna(subset=["timestamp"]).copy()
        if "event_type" in keys.columns:
            keys = keys[keys["event_type"].fillna("").astype(str).str.lower().eq("key_press")].copy()
        if keys.empty:
            return {}
        keys["node_id"] = self._resolve_context_json_nodes_with_interval_fallback(keys, events_df)
        keys = keys[keys["node_id"].astype(str).str.len().gt(0)]
        return keys.groupby("node_id").size().astype(int).to_dict() if not keys.empty else {}

    def _build_mouse_count_map(
        self,
        mouse_df: pd.DataFrame | None,
        events_df: pd.DataFrame,
    ) -> dict[str, int]:
        if mouse_df is None or mouse_df.empty or "timestamp" not in mouse_df.columns:
            return {}
        mouse = mouse_df.copy()
        mouse["timestamp"] = pd.to_numeric(mouse["timestamp"], errors="coerce")
        mouse = mouse.dropna(subset=["timestamp"]).copy()
        if mouse.empty:
            return {}
        mouse["node_id"] = self._resolve_context_json_nodes_with_interval_fallback(mouse, events_df)
        mouse = mouse[mouse["node_id"].astype(str).str.len().gt(0)]
        return mouse.groupby("node_id").size().astype(int).to_dict() if not mouse.empty else {}

    def _resolve_row_nodes_with_interval_fallback(
        self,
        rows: pd.DataFrame,
        events_df: pd.DataFrame,
    ) -> pd.Series:
        valid_nodes = set(events_df["node_id"].fillna("").astype(str))
        fallback = _build_interval_node_lookup(events_df)
        node_ids: list[str] = []
        for row in rows.to_dict("records"):
            resolved = resolve_context_node(
                app_name=row.get("app_name", ""),
                url=row.get("url", ""),
                title=row.get("title", ""),
                window_title=row.get("window_title", ""),
                tab_id=row.get("tab_id", ""),
            )
            node_id = str(resolved.get("node_id", ""))
            if node_id not in valid_nodes:
                node_id = fallback(float(row.get("timestamp", np.nan)))
            node_ids.append(node_id if node_id in valid_nodes else "")
        return pd.Series(node_ids, index=rows.index)

    def _resolve_context_json_nodes_with_interval_fallback(
        self,
        rows: pd.DataFrame,
        events_df: pd.DataFrame,
    ) -> pd.Series:
        valid_nodes = set(events_df["node_id"].fillna("").astype(str))
        fallback = _build_interval_node_lookup(events_df)
        contexts = context_from_json_series(rows.get("context", pd.Series(dtype=str)))
        node_ids: list[str] = []
        for pos, row in enumerate(rows.to_dict("records")):
            context = contexts.iloc[pos].to_dict() if pos < len(contexts) else {}
            app_name = context.get("active_app") or context.get("app_name") or ""
            if is_internal_context(app_name, context.get("window_title", ""), context.get("title", "")):
                node_ids.append("")
                continue
            resolved = resolve_context_from_payload(context)
            node_id = str(resolved.get("node_id", ""))
            if node_id not in valid_nodes:
                node_id = fallback(float(row.get("timestamp", np.nan)))
            node_ids.append(node_id if node_id in valid_nodes else "")
        return pd.Series(node_ids, index=rows.index)

    @staticmethod
    def _build_temporal_transitions(events_df: pd.DataFrame) -> pd.DataFrame:
        if events_df is None or events_df.empty:
            return pd.DataFrame(columns=["source", "target", "edge_type", "timestamp", "duration_ms"])

        events = events_df.copy().reset_index(drop=True)
        events["duration_ms"] = pd.to_numeric(events.get("duration_ms"), errors="coerce").fillna(0.0)

        persistence = pd.DataFrame(
            {
                "source": events["node_id"],
                "target": events["node_id"],
                "edge_type": "persistence",
                "timestamp": pd.to_numeric(events.get("start_time", events["timestamp"]), errors="coerce").fillna(
                    pd.to_numeric(events["timestamp"], errors="coerce")
                ),
                "duration_ms": events["duration_ms"],
            }
        )
        persistence = persistence[persistence["duration_ms"].gt(0)]

        if len(events) < 2:
            return persistence.dropna(subset=["source", "target"]).reset_index(drop=True)

        transitions = pd.DataFrame(
            {
                "source": events["node_id"],
                "target": events["node_id"].shift(-1),
                "edge_type": "transition",
                "timestamp": pd.to_numeric(events["timestamp"], errors="coerce"),
                "duration_ms": events["duration_ms"],
            }
        )
        transitions = transitions.iloc[:-1].dropna(subset=["source", "target"])
        transitions = transitions[transitions["source"].astype(str) != transitions["target"].astype(str)]
        combined = pd.concat([persistence, transitions], ignore_index=True)
        return combined.dropna(subset=["source", "target"]).reset_index(drop=True)


def _first_non_blank(series: pd.Series) -> str:
    non_blank = series[series.astype(str).str.strip().ne("")]
    if non_blank.empty:
        return ""
    return str(non_blank.iloc[0]).strip()


def _internal_context_mask(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=bool)
    app_names = df.get("app_name", pd.Series("", index=df.index))
    window_titles = df.get("window_title", pd.Series("", index=df.index))
    titles = df.get("title", pd.Series("", index=df.index))
    return pd.Series(
        [
            is_internal_context(app_name, window_title, title)
            for app_name, window_title, title in zip(app_names, window_titles, titles)
        ],
        index=df.index,
        dtype=bool,
    )


def _build_interval_node_lookup(events_df: pd.DataFrame):
    if events_df is None or events_df.empty:
        return lambda _timestamp: ""

    events = events_df.copy()
    events["start_time"] = pd.to_numeric(events.get("start_time"), errors="coerce")
    events["end_time"] = pd.to_numeric(events.get("end_time"), errors="coerce")
    events = events.dropna(subset=["start_time", "end_time", "node_id"]).sort_values("end_time")
    starts = events["start_time"].to_numpy(dtype=float, copy=False)
    ends = events["end_time"].to_numpy(dtype=float, copy=False)
    node_ids = events["node_id"].astype(str).to_numpy(copy=False)

    def _lookup(timestamp: float) -> str:
        if np.isnan(timestamp) or len(ends) == 0:
            return ""
        idx = int(np.searchsorted(ends, timestamp, side="left"))
        if idx >= len(ends):
            idx = len(ends) - 1
        if starts[idx] <= timestamp <= ends[idx]:
            return str(node_ids[idx])
        return ""

    return _lookup


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


def _concat_or_empty(frames: list[pd.DataFrame], columns: list[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)[columns]


def _write_csv(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    out = df.copy() if df is not None else pd.DataFrame(columns=columns)
    for col in columns:
        if col not in out.columns:
            out[col] = pd.Series(dtype=object)
    out[columns].to_csv(path, index=False)
