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
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from .windowing import WindowEngine

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
]

_GLOBAL_NODE_COLUMNS = ["node_id", "node_type"]
_GLOBAL_EDGE_COLUMNS = ["source", "target", "transition_count", "total_duration", "avg_duration"]
_GLOBAL_TEMPORAL_COLUMNS = ["source", "target", "timestamp"]
_WINDOW_NODE_COLUMNS = ["window_id", "node_id", "node_type"]
_WINDOW_EDGE_COLUMNS = ["window_id", "source", "target", "transition_count", "total_duration", "avg_duration"]
_WINDOW_TEMPORAL_COLUMNS = ["window_id", "source", "target", "timestamp"]


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
        self._engine = WindowEngine()

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

        node_frame = self._resolve_nodes(grouped)
        cleaned = pd.concat([grouped, node_frame], axis=1)
        cleaned = cleaned[cleaned["node_id"].notna() & cleaned["node_id"].astype(str).str.len().gt(0)]

        cleaned = cleaned[_EVENT_COLUMNS].reset_index(drop=True)
        LOGGER.info("Graph: cleaned %d behavior events for node_level=%s", len(cleaned), self.config.node_level)
        return cleaned

    def build(
        self,
        behavior_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Clean a behavior stream and return:
          cleaned_events, nodes.csv DataFrame, edges.csv DataFrame, temporal_edges.csv DataFrame
        """
        events_df = self.clean_events(behavior_df)
        nodes_df, edges_df, temporal_edges_df = self.build_from_events(events_df)
        return events_df, nodes_df, edges_df, temporal_edges_df

    def build_from_events(
        self,
        events_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Build graph tables from a cleaned event table."""
        if events_df is None or events_df.empty:
            return (
                pd.DataFrame(columns=_GLOBAL_NODE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_EDGE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_TEMPORAL_COLUMNS),
            )

        nodes_df = (
            events_df[["node_id", "node_type"]]
            .drop_duplicates(subset=["node_id"])
            .sort_values("node_id")
            .reset_index(drop=True)
        )

        transitions = self._build_temporal_transitions(events_df)
        if transitions.empty:
            return (
                nodes_df,
                pd.DataFrame(columns=_GLOBAL_EDGE_COLUMNS),
                pd.DataFrame(columns=_GLOBAL_TEMPORAL_COLUMNS),
            )

        temporal_edges_df = transitions[["source", "target", "timestamp"]].reset_index(drop=True)
        edges_df = (
            transitions.groupby(["source", "target"], as_index=False)
            .agg(
                transition_count=("timestamp", "size"),
                total_duration=("duration_ms", "sum"),
                avg_duration=("duration_ms", "mean"),
            )
        )
        edges_df["total_duration"] = edges_df["total_duration"].round(2)
        edges_df["avg_duration"] = edges_df["avg_duration"].round(2)
        return nodes_df, edges_df[_GLOBAL_EDGE_COLUMNS], temporal_edges_df[_GLOBAL_TEMPORAL_COLUMNS]

    def build_windowed(
        self,
        events_df: pd.DataFrame,
        windows_df: pd.DataFrame,
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

        timestamps = events_df["timestamp"].to_numpy(dtype=float, copy=False)
        window_nodes: list[pd.DataFrame] = []
        window_edges: list[pd.DataFrame] = []
        window_temporal_edges: list[pd.DataFrame] = []

        for window in windows_df.itertuples(index=False):
            lo, hi = self._engine.window_slice_indices(
                timestamps,
                float(window.window_start),
                float(window.window_end),
            )
            window_events = events_df.iloc[lo:hi].reset_index(drop=True)
            if window_events.empty:
                continue

            nodes_df, edges_df, temporal_edges_df = self.build_from_events(window_events)

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
        app_names = events_df["app_name"].fillna("").astype(str).str.strip()
        urls = events_df["url"].fillna("").astype(str).str.strip()
        domains = urls.map(_extract_domain)

        if self.config.node_level == "app":
            node_id = app_names.where(app_names.ne(""), domains.fillna("unknown"))
            node_type = np.where(app_names.ne(""), "app", np.where(domains.notna(), "domain", "unknown"))
        elif self.config.node_level == "domain":
            node_id = domains.where(domains.notna() & domains.ne(""), app_names)
            node_type = np.where(domains.notna() & domains.ne(""), "domain", "app")
        else:
            node_id = urls.where(urls.ne(""), app_names)
            node_type = np.where(urls.ne(""), "url", "app")

        node_id = (
            pd.Series(node_id, index=events_df.index)
            .fillna("unknown")
            .astype(str)
            .str.strip()
            .replace("", "unknown")
        )
        node_type = pd.Series(node_type, index=events_df.index).fillna("unknown").astype(str)

        return pd.DataFrame({"node_id": node_id, "node_type": node_type})

    @staticmethod
    def _build_temporal_transitions(events_df: pd.DataFrame) -> pd.DataFrame:
        if events_df is None or len(events_df) < 2:
            return pd.DataFrame(columns=["source", "target", "timestamp", "duration_ms"])

        transitions = pd.DataFrame(
            {
                "source": events_df["node_id"],
                "target": events_df["node_id"].shift(-1),
                "timestamp": events_df["timestamp"],
                "duration_ms": events_df["duration_ms"],
            }
        )
        transitions = transitions.iloc[:-1].dropna(subset=["source", "target"])
        return transitions.reset_index(drop=True)


def _first_non_blank(series: pd.Series) -> str:
    non_blank = series[series.astype(str).str.strip().ne("")]
    if non_blank.empty:
        return ""
    return str(non_blank.iloc[0]).strip()


def _extract_domain(url: str) -> str | None:
    text = str(url or "").strip()
    if not text:
        return None
    try:
        parsed = urlparse(text if "://" in text else f"//{text}")
        domain = (parsed.netloc or parsed.path.split("/", 1)[0]).strip().lower()
    except Exception:
        return None
    if not domain:
        return None
    return domain.split(":", 1)[0] or None


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
