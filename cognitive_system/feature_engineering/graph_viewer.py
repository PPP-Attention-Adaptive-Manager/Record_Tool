"""
Tkinter GUI for visualizing one session as a table of window graphs.

What it shows
-------------
- One selected session at a time
- A grid of windows for that session
- One graph per window cell
- Click a node to inspect its features
- Click an edge to inspect its features

Current viewer behavior
-----------------------
- App nodes are built from `app_name`
- Tab nodes are built from URL or domain when present
- Some requested node/edge features are estimated heuristically
- Unavailable pipeline features are displayed as `N/A`
"""
from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pandas as pd

from .features import MODALITY_FEATURE_COLUMNS
from .graph_builder import GraphBuilder
from .node_features import NODE_FEATURE_COLUMNS
from .windowing import DEFAULT_WINDOW_CONFIGS, WindowConfig, WindowEngine

LOGGER = logging.getLogger(__name__)

_CARD_BG = "#f8fbff"
_CARD_BORDER = "#d7e1ef"
_APP_NODE = "#6ea8fe"
_TAB_NODE = "#74d3ae"
_APP_EDGE = "#4f7fc4"
_TAB_EDGE = "#319f77"
_APP_TAB_EDGE = "#c8863f"
_TEXT = "#223346"
_MUTED = "#607188"
_PANEL_BG = "#eef4fb"
_CANVAS_BG = "#f1f6fc"
_EMPTY_BG = "#edf3fb"
_WINDOW_META_COLUMNS = ["window_id", "session_id", "window_start", "window_end"]
_DISTRACTOR_DOMAINS = {
    "youtube.com",
    "www.youtube.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "tiktok.com",
    "www.tiktok.com",
    "twitter.com",
    "x.com",
}


@dataclass
class GraphNodeView:
    key: str
    label: str
    node_kind: str
    feature_node_type: str
    feature_node_id: str
    features: dict[str, object]


@dataclass
class GraphEdgeView:
    edge_id: str
    source_key: str
    target_key: str
    source_label: str
    target_label: str
    edge_kind: str
    features: dict[str, object]


@dataclass
class WindowGraphView:
    session_id: str
    window_id: str
    window_start: float
    window_end: float
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]
    window_features: dict[str, object]

    @property
    def duration_seconds(self) -> float:
        return max(0.0, float(self.window_end) - float(self.window_start))

    @property
    def app_node_count(self) -> int:
        return sum(1 for node in self.nodes if node.node_kind == "app")

    @property
    def tab_node_count(self) -> int:
        return sum(1 for node in self.nodes if node.node_kind == "tab")


@dataclass
class SessionWindowBundle:
    session_id: str
    session_dir: Path
    window_label: str
    tab_level: str
    windows: list[WindowGraphView]
    error: Optional[str] = None


def discover_sessions(data_dir: Path) -> list[Path]:
    """Return session directories sorted newest-first."""
    if not data_dir.exists():
        return []
    session_dirs = [
        path
        for path in data_dir.iterdir()
        if path.is_dir() and (path / "raw" / "behavior.csv").exists()
    ]
    return sorted(session_dirs, key=lambda path: path.name, reverse=True)


def load_session_bundle(
    session_dir: Path,
    window_label: str,
    tab_level: str,
) -> SessionWindowBundle:
    """Build all per-window graphs for one session."""
    try:
        raw_streams = _load_raw_streams(session_dir / "raw")
        behavior_df = raw_streams.get("behavior", pd.DataFrame())
        if behavior_df.empty:
            return SessionWindowBundle(
                session_id=session_dir.name,
                session_dir=session_dir,
                window_label=window_label,
                tab_level=tab_level,
                windows=[],
                error="No behavior.csv events found.",
            )

        cleaner = GraphBuilder(node_level="app")
        cleaned_events = cleaner.clean_events(behavior_df)
        cleaned_events = cleaned_events.sort_values("timestamp").reset_index(drop=True)

        windows_df, window_feature_map, node_feature_map = _load_windows_for_session(
            session_dir=session_dir,
            raw_streams=raw_streams,
            window_label=window_label,
        )
        if windows_df.empty:
            return SessionWindowBundle(
                session_id=session_dir.name,
                session_dir=session_dir,
                window_label=window_label,
                tab_level=tab_level,
                windows=[],
                error=f"No windows available for label '{window_label}'.",
            )

        event_ts = cleaned_events["timestamp"].to_numpy(dtype=float, copy=False) if not cleaned_events.empty else []
        raw_ts = behavior_df["timestamp"].to_numpy(dtype=float, copy=False)
        engine = WindowEngine()

        window_graphs: list[WindowGraphView] = []
        for window_row in windows_df.itertuples(index=False):
            feature_row = window_feature_map.get(window_row.window_id, {})
            if len(event_ts):
                ev_lo, ev_hi = engine.window_slice_indices(
                    event_ts,
                    float(window_row.window_start),
                    float(window_row.window_end),
                )
                window_events = cleaned_events.iloc[ev_lo:ev_hi].copy()
            else:
                window_events = pd.DataFrame(columns=cleaned_events.columns)

            raw_lo, raw_hi = engine.window_slice_indices(
                raw_ts,
                float(window_row.window_start),
                float(window_row.window_end),
            )
            raw_slice = behavior_df.iloc[raw_lo:raw_hi].copy()

            window_graphs.append(
                _build_window_graph(
                    session_id=session_dir.name,
                    window_id=str(window_row.window_id),
                    window_start=float(window_row.window_start),
                    window_end=float(window_row.window_end),
                    window_events=window_events,
                    raw_behavior_slice=raw_slice,
                    tab_level=tab_level,
                    window_features=feature_row,
                    node_feature_map=node_feature_map,
                )
            )

        return SessionWindowBundle(
            session_id=session_dir.name,
            session_dir=session_dir,
            window_label=window_label,
            tab_level=tab_level,
            windows=window_graphs,
        )
    except Exception as exc:
        LOGGER.exception("Failed to build window bundle for session %s", session_dir.name)
        return SessionWindowBundle(
            session_id=session_dir.name,
            session_dir=session_dir,
            window_label=window_label,
            tab_level=tab_level,
            windows=[],
            error=str(exc),
        )


class SessionWindowGraphViewer:
    """Window-table viewer for one session."""

    def __init__(
        self,
        data_dir: Path,
        session_id: Optional[str] = None,
        window_label: str = "30s",
        tab_level: str = "domain",
        columns: int = 3,
        card_width: int = 320,
        card_height: int = 200,
    ) -> None:
        self.data_dir = data_dir
        self.default_session_id = session_id
        self.default_window_label = window_label
        self.default_tab_level = tab_level
        self.columns = max(1, int(columns))
        self.card_width = max(260, int(card_width))
        self.card_height = max(170, int(card_height))

        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError as exc:
            raise RuntimeError("tkinter is required to run the graph viewer") from exc

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("Window Graph Viewer")
        self.root.geometry("1460x900")
        self.root.configure(bg="#eaf1f9")

        session_dirs = discover_sessions(self.data_dir)
        self.session_path_map = {path.name: path for path in session_dirs}
        initial_session = session_id if session_id in self.session_path_map else (
            session_dirs[0].name if session_dirs else ""
        )

        self.session_var = tk.StringVar(value=initial_session)
        self.window_label_var = tk.StringVar(value=window_label)
        self.tab_level_var = tk.StringVar(value=tab_level)
        self.columns_var = tk.IntVar(value=self.columns)
        self.status_var = tk.StringVar(value="Ready")
        self.selection_title_var = tk.StringVar(value="Selection")
        self.selection_meta_var = tk.StringVar(value="Click a node or edge to inspect its features.")

        self._bundle: Optional[SessionWindowBundle] = None
        self._canvas_registry: dict[object, dict[str, object]] = {}

        self._build_layout()
        self.root.after(50, self.refresh)

    def _build_layout(self) -> None:
        tk = self.tk
        ttk = self.ttk

        shell = tk.Frame(self.root, bg="#eaf1f9")
        shell.pack(fill="both", expand=True)

        controls = tk.Frame(shell, bg="#eaf1f9", padx=18, pady=14)
        controls.pack(fill="x")

        tk.Label(
            controls,
            text="Temporal Window Graph Viewer",
            bg="#eaf1f9",
            fg=_TEXT,
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, columnspan=10, sticky="w")

        tk.Label(
            controls,
            text="One session at a time. Each cell is a window graph. Nodes and edges are clickable.",
            bg="#eaf1f9",
            fg=_MUTED,
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=10, sticky="w", pady=(4, 10))

        ttk.Label(controls, text="Session").grid(row=2, column=0, sticky="w")
        session_box = ttk.Combobox(
            controls,
            textvariable=self.session_var,
            values=list(self.session_path_map.keys()),
            width=38,
            state="readonly",
        )
        session_box.grid(row=2, column=1, padx=(8, 16), sticky="w")
        session_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="Window").grid(row=2, column=2, sticky="w")
        window_box = ttk.Combobox(
            controls,
            textvariable=self.window_label_var,
            values=[config.label for config in DEFAULT_WINDOW_CONFIGS],
            width=10,
            state="readonly",
        )
        window_box.grid(row=2, column=3, padx=(8, 16), sticky="w")
        window_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="Tab level").grid(row=2, column=4, sticky="w")
        tab_level_box = ttk.Combobox(
            controls,
            textvariable=self.tab_level_var,
            values=["domain", "url"],
            width=10,
            state="readonly",
        )
        tab_level_box.grid(row=2, column=5, padx=(8, 16), sticky="w")
        tab_level_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="Columns").grid(row=2, column=6, sticky="w")
        columns_box = ttk.Combobox(
            controls,
            textvariable=self.columns_var,
            values=[2, 3, 4, 5],
            width=5,
            state="readonly",
        )
        columns_box.grid(row=2, column=7, padx=(8, 16), sticky="w")
        columns_box.bind("<<ComboboxSelected>>", lambda _event: self.render_windows())

        ttk.Button(controls, text="Refresh", command=self.refresh).grid(row=2, column=8, sticky="w")

        tk.Label(
            controls,
            textvariable=self.status_var,
            bg="#eaf1f9",
            fg=_MUTED,
            font=("Segoe UI", 10),
        ).grid(row=2, column=9, padx=(16, 0), sticky="e")

        body = tk.PanedWindow(shell, bg="#eaf1f9", sashrelief="flat", sashwidth=8)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = tk.Frame(body, bg="#eaf1f9")
        right = tk.Frame(body, bg=_PANEL_BG, padx=12, pady=12)
        body.add(left, stretch="always", minsize=860)
        body.add(right, minsize=360)

        self.canvas = tk.Canvas(left, bg="#eaf1f9", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.grid_frame = tk.Frame(self.canvas, bg="#eaf1f9")
        self.grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self._build_inspector(right)

    def _build_inspector(self, parent) -> None:
        tk = self.tk
        ttk = self.ttk

        tk.Label(
            parent,
            text="Inspector",
            bg=_PANEL_BG,
            fg=_TEXT,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")

        tk.Label(
            parent,
            textvariable=self.selection_title_var,
            bg=_PANEL_BG,
            fg=_TEXT,
            font=("Segoe UI", 11, "bold"),
            justify="left",
        ).pack(anchor="w", pady=(10, 4))

        tk.Label(
            parent,
            textvariable=self.selection_meta_var,
            bg=_PANEL_BG,
            fg=_MUTED,
            justify="left",
            wraplength=320,
            font=("Segoe UI", 10),
        ).pack(anchor="w", fill="x")

        tk.Label(
            parent,
            text="Features",
            bg=_PANEL_BG,
            fg=_TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(16, 6))

        self.feature_tree = ttk.Treeview(
            parent,
            columns=("feature", "value"),
            show="headings",
            height=28,
        )
        self.feature_tree.heading("feature", text="feature")
        self.feature_tree.heading("value", text="value")
        self.feature_tree.column("feature", width=150, anchor="w")
        self.feature_tree.column("value", width=170, anchor="w")
        self.feature_tree.pack(fill="both", expand=True)

    def _on_frame_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.grid_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def refresh(self) -> None:
        session_id = self.session_var.get().strip()
        if not session_id:
            self._bundle = SessionWindowBundle(
                session_id="",
                session_dir=self.data_dir,
                window_label=self.window_label_var.get().strip() or "30s",
                tab_level=self.tab_level_var.get().strip() or "domain",
                windows=[],
                error=f"No sessions found in {self.data_dir}",
            )
            self.status_var.set(self._bundle.error or "No sessions found.")
            self.render_windows()
            return

        session_dir = self.session_path_map.get(session_id)
        if session_dir is None:
            self.status_var.set(f"Session '{session_id}' not found.")
            return

        window_label = self.window_label_var.get().strip() or "30s"
        tab_level = self.tab_level_var.get().strip() or "domain"
        self.status_var.set(f"Loading {session_id} ({window_label}, tab={tab_level}) ...")
        self.root.update_idletasks()

        self._bundle = load_session_bundle(
            session_dir=session_dir,
            window_label=window_label,
            tab_level=tab_level,
        )
        if self._bundle.error:
            self.status_var.set(self._bundle.error)
        else:
            self.status_var.set(
                f"{self._bundle.session_id}: {len(self._bundle.windows)} windows, "
                f"tab level '{self._bundle.tab_level}'"
            )
        self.render_windows()

    def render_windows(self) -> None:
        tk = self.tk
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._canvas_registry.clear()

        if self._bundle is None:
            return

        if self._bundle.error:
            error_label = tk.Label(
                self.grid_frame,
                text=self._bundle.error,
                bg="#eaf1f9",
                fg="#af4343",
                font=("Segoe UI", 12),
                padx=18,
                pady=30,
            )
            error_label.grid(row=0, column=0, sticky="w")
            self._show_info("Selection", self._bundle.error, {})
            self._on_frame_configure()
            return

        columns = max(1, int(self.columns_var.get()))
        for col in range(columns):
            self.grid_frame.grid_columnconfigure(col, weight=1, uniform="window_cols")

        for index, window_graph in enumerate(self._bundle.windows):
            row = index // columns
            col = index % columns
            card = self._build_window_card(self.grid_frame, window_graph)
            card.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)

        if self._bundle.windows:
            first = self._bundle.windows[0]
            if first.nodes:
                self._show_node_details(first, first.nodes[0])
            else:
                self._show_window_details(first)
        self._on_frame_configure()

    def _build_window_card(self, parent, window_graph: WindowGraphView):
        tk = self.tk
        ttk = self.ttk

        card = tk.Frame(
            parent,
            bg=_CARD_BG,
            highlightbackground=_CARD_BORDER,
            highlightthickness=1,
            bd=0,
            padx=10,
            pady=10,
        )

        tk.Label(
            card,
            text=window_graph.window_id,
            bg=_CARD_BG,
            fg=_TEXT,
            anchor="w",
            font=("Segoe UI", 11, "bold"),
        ).pack(fill="x")

        tk.Label(
            card,
            text=(
                f"{window_graph.window_start:.3f} -> {window_graph.window_end:.3f}\n"
                f"apps: {window_graph.app_node_count}   tabs: {window_graph.tab_node_count}   "
                f"edges: {len(window_graph.edges)}"
            ),
            bg=_CARD_BG,
            fg=_MUTED,
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(3, 8))

        canvas = tk.Canvas(
            card,
            width=self.card_width,
            height=self.card_height,
            bg=_CANVAS_BG,
            highlightthickness=0,
        )
        canvas.pack(fill="both", expand=False)
        draw_state = self._draw_window_graph(canvas, window_graph, self.card_width, self.card_height)
        self._canvas_registry[canvas] = draw_state
        canvas.bind("<Button-1>", lambda event, c=canvas: self._on_canvas_click(event, c))

        footer = tk.Frame(card, bg=_CARD_BG)
        footer.pack(fill="x", pady=(8, 0))

        tk.Label(
            footer,
            text=_window_footer_text(window_graph),
            bg=_CARD_BG,
            fg=_MUTED,
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            footer,
            text="Window Details",
            command=lambda wg=window_graph: self._open_window_details(wg),
        ).pack(side="right")

        return card

    def _draw_window_graph(self, canvas, window_graph: WindowGraphView, width: int, height: int) -> dict[str, object]:
        tk = self.tk
        canvas.delete("all")

        if not window_graph.nodes:
            canvas.create_text(
                width / 2,
                height / 2,
                text="No graph nodes in this window",
                fill=_MUTED,
                font=("Segoe UI", 10),
            )
            return {"window_graph": window_graph, "node_positions": {}, "node_radius": 18, "edge_hits": []}

        positions = _compute_positions(window_graph.nodes, width, height)
        node_radius = 18
        edge_hits: list[dict[str, object]] = []
        node_hits: list[dict[str, object]] = []

        for edge in window_graph.edges:
            x1, y1 = positions[edge.source_key]
            x2, y2 = positions[edge.target_key]
            line_color = _edge_color(edge.edge_kind)
            weight = edge.features.get("transition_count") or edge.features.get("switch_count") or 1
            line_width = 1.5 + min(4.0, float(weight))

            if edge.source_key == edge.target_key:
                loop_r = 16
                canvas.create_arc(
                    x1 - loop_r,
                    y1 - loop_r - 22,
                    x1 + loop_r,
                    y1 + loop_r - 4,
                    start=30,
                    extent=300,
                    style=tk.ARC,
                    outline=line_color,
                    width=line_width,
                )
                edge_hits.append(
                    {
                        "edge": edge,
                        "bbox": (x1 - loop_r, y1 - loop_r - 22, x1 + loop_r, y1 + loop_r - 4),
                        "self_loop": True,
                    }
                )
            else:
                canvas.create_line(
                    x1,
                    y1,
                    x2,
                    y2,
                    fill=line_color,
                    width=line_width,
                    arrow=tk.LAST,
                    smooth=True,
                )
                edge_hits.append(
                    {
                        "edge": edge,
                        "segment": (x1, y1, x2, y2),
                        "self_loop": False,
                    }
                )
                mx = (x1 + x2) / 2
                my = (y1 + y2) / 2
                label = _edge_label(edge)
                canvas.create_rectangle(mx - 12, my - 9, mx + 12, my + 9, fill="#ffffff", outline="")
                canvas.create_text(mx, my, text=label, fill=line_color, font=("Segoe UI", 8, "bold"))

        for node in window_graph.nodes:
            x, y = positions[node.key]
            fill = _APP_NODE if node.node_kind == "app" else _TAB_NODE
            oval_id = canvas.create_oval(
                x - node_radius,
                y - node_radius,
                x + node_radius,
                y + node_radius,
                fill=fill,
                outline="#ffffff",
                width=2,
            )
            text_id = canvas.create_text(
                x,
                y + node_radius + 12,
                text=_truncate(node.label, 16),
                fill=_TEXT,
                font=("Segoe UI", 8),
                width=100,
                justify="center",
            )
            node_hits.append(
                {
                    "node_key": node.key,
                    "circle_bbox": canvas.bbox(oval_id),
                    "label_bbox": canvas.bbox(text_id),
                }
            )

        return {
            "window_graph": window_graph,
            "node_positions": positions,
            "node_radius": node_radius,
            "edge_hits": edge_hits,
            "node_hits": node_hits,
            "node_map": {node.key: node for node in window_graph.nodes},
        }

    def _on_canvas_click(self, event, canvas) -> None:
        state = self._canvas_registry.get(canvas)
        if not state:
            return

        window_graph = state["window_graph"]
        node_positions: dict[str, tuple[float, float]] = state["node_positions"]
        node_radius = int(state["node_radius"])
        node_hits: list[dict[str, object]] = state.get("node_hits", [])
        node_map: dict[str, GraphNodeView] = state["node_map"]
        x = float(event.x)
        y = float(event.y)

        for node_hit in node_hits:
            for bbox_name in ("circle_bbox", "label_bbox"):
                bbox = node_hit.get(bbox_name)
                if not bbox:
                    continue
                x1, y1, x2, y2 = bbox
                if x1 <= x <= x2 and y1 <= y <= y2:
                    self._show_node_details(window_graph, node_map[node_hit["node_key"]])
                    return

        for node_key, (nx, ny) in node_positions.items():
            if ((x - nx) ** 2 + (y - ny) ** 2) <= (node_radius ** 2):
                self._show_node_details(window_graph, node_map[node_key])
                return

        for edge_hit in state["edge_hits"]:
            edge = edge_hit["edge"]
            if edge_hit["self_loop"]:
                x1, y1, x2, y2 = edge_hit["bbox"]
                if x1 <= x <= x2 and y1 <= y <= y2:
                    self._show_edge_details(window_graph, edge)
                    return
            else:
                if _distance_to_segment(x, y, *edge_hit["segment"]) <= 10.0:
                    self._show_edge_details(window_graph, edge)
                    return

        self._show_window_details(window_graph)

    def _show_window_details(self, window_graph: WindowGraphView) -> None:
        meta = (
            f"{window_graph.window_id}   "
            f"duration={window_graph.duration_seconds:.2f}s   "
            f"apps={window_graph.app_node_count}   tabs={window_graph.tab_node_count}   "
            f"edges={len(window_graph.edges)}"
        )
        features = dict(window_graph.window_features)
        if not features:
            features = {
                "window_start": window_graph.window_start,
                "window_end": window_graph.window_end,
                "duration_seconds": round(window_graph.duration_seconds, 3),
            }
        self._show_info("Window", meta, features)

    def _show_node_details(self, window_graph: WindowGraphView, node: GraphNodeView) -> None:
        meta = f"{window_graph.window_id}   kind={node.node_kind}   label={node.label}"
        self._show_info(f"Node: {node.label}", meta, node.features)

    def _show_edge_details(self, window_graph: WindowGraphView, edge: GraphEdgeView) -> None:
        meta = (
            f"{window_graph.window_id}   kind={edge.edge_kind}   "
            f"{edge.source_label} -> {edge.target_label}"
        )
        self._show_info(f"Edge: {edge.source_label} -> {edge.target_label}", meta, edge.features)

    def _show_info(self, title: str, meta: str, features: dict[str, object]) -> None:
        self.selection_title_var.set(title)
        self.selection_meta_var.set(meta)
        for item in self.feature_tree.get_children():
            self.feature_tree.delete(item)
        for key, value in features.items():
            self.feature_tree.insert("", "end", values=(key, _format_value(value)))

    def _open_window_details(self, window_graph: WindowGraphView) -> None:
        tk = self.tk
        ttk = self.ttk

        win = tk.Toplevel(self.root)
        win.title(f"Window Details - {window_graph.session_id} - {window_graph.window_id}")
        win.geometry("1180x760")
        win.configure(bg="#eef4fb")

        header = tk.Frame(win, bg="#eef4fb", padx=14, pady=12)
        header.pack(fill="x")

        tk.Label(
            header,
            text=f"{window_graph.session_id} / {window_graph.window_id}",
            bg="#eef4fb",
            fg=_TEXT,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")

        tk.Label(
            header,
            text=(
                f"{window_graph.window_start:.3f} -> {window_graph.window_end:.3f}   "
                f"duration={window_graph.duration_seconds:.2f}s   "
                f"apps={window_graph.app_node_count}   tabs={window_graph.tab_node_count}   "
                f"edges={len(window_graph.edges)}"
            ),
            bg="#eef4fb",
            fg=_MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        graph_canvas = tk.Canvas(
            win,
            width=1100,
            height=320,
            bg="#ffffff",
            highlightbackground=_CARD_BORDER,
            highlightthickness=1,
        )
        graph_canvas.pack(fill="x", padx=14, pady=(0, 12))
        self._draw_window_graph(graph_canvas, window_graph, 1100, 320)

        tables = tk.Frame(win, bg="#eef4fb", padx=14, pady=14)
        tables.pack(fill="both", expand=True)
        tables.grid_columnconfigure(0, weight=1)
        tables.grid_columnconfigure(1, weight=1)
        tables.grid_rowconfigure(0, weight=1)

        node_frame = tk.Frame(tables, bg="#eef4fb")
        node_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        edge_frame = tk.Frame(tables, bg="#eef4fb")
        edge_frame.grid(row=0, column=1, sticky="nsew")

        tk.Label(node_frame, text="Nodes", bg="#eef4fb", fg=_TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        node_tree = ttk.Treeview(node_frame, columns=("kind", "label"), show="headings", height=16)
        node_tree.heading("kind", text="kind")
        node_tree.heading("label", text="label")
        node_tree.column("kind", width=90, anchor="center")
        node_tree.column("label", width=300, anchor="w")
        node_tree.pack(fill="both", expand=True, pady=(6, 0))

        for node in window_graph.nodes:
            node_tree.insert("", "end", values=(node.node_kind, node.label))

        tk.Label(edge_frame, text="Edges", bg="#eef4fb", fg=_TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        edge_tree = ttk.Treeview(edge_frame, columns=("kind", "source", "target"), show="headings", height=16)
        edge_tree.heading("kind", text="kind")
        edge_tree.heading("source", text="source")
        edge_tree.heading("target", text="target")
        edge_tree.column("kind", width=90, anchor="center")
        edge_tree.column("source", width=220, anchor="w")
        edge_tree.column("target", width=220, anchor="w")
        edge_tree.pack(fill="both", expand=True, pady=(6, 0))

        for edge in window_graph.edges:
            edge_tree.insert("", "end", values=(edge.edge_kind, edge.source_label, edge.target_label))

    def run(self) -> None:
        self.root.mainloop()


def _load_raw_streams(raw_dir: Path) -> dict[str, pd.DataFrame]:
    streams: dict[str, pd.DataFrame] = {}
    for name in ("behavior", "keyboard", "mouse", "dual_task", "notification", "system_metrics"):
        path = raw_dir / f"{name}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, low_memory=False)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
                df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            streams[name] = df
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", path, exc)
    return streams


def _load_windows_for_session(
    session_dir: Path,
    raw_streams: dict[str, pd.DataFrame],
    window_label: str,
) -> tuple[pd.DataFrame, dict[str, dict[str, object]], dict[tuple[str, str, str], dict[str, object]]]:
    feature_frames: list[pd.DataFrame] = []
    for modality in MODALITY_FEATURE_COLUMNS:
        path = session_dir / "features" / modality / f"features_{modality}_{window_label}.csv"
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            LOGGER.warning("Failed to load %s: %s", path, exc)
            continue
        if {"window_id", "window_start", "window_end"}.issubset(frame.columns):
            frame = frame.copy()
            frame["window_id"] = frame["window_id"].astype(str)
            feature_frames.append(frame)

    window_feature_map: dict[str, dict[str, object]] = {}
    if feature_frames:
        combined = feature_frames[0].copy()
        merge_keys = [col for col in _WINDOW_META_COLUMNS if col in combined.columns]
        for frame in feature_frames[1:]:
            frame_keys = [col for col in merge_keys if col in frame.columns]
            if not frame_keys:
                continue
            combined = combined.merge(frame, on=frame_keys, how="outer")

        combined = combined.sort_values(["window_start", "window_end"]).reset_index(drop=True)
        window_feature_map = {
            str(row["window_id"]): {
                key: row[key]
                for key in combined.columns
                if key != "session_id"
            }
            for _, row in combined.iterrows()
        }
        windows_df = combined[["window_id", "window_start", "window_end"]].drop_duplicates().reset_index(drop=True)
        node_feature_map = _load_node_feature_map(session_dir, window_label)
        return windows_df, window_feature_map, node_feature_map

    available = [df for df in raw_streams.values() if df is not None and not df.empty and "timestamp" in df.columns]
    if not available:
        return pd.DataFrame(columns=["window_id", "window_start", "window_end"]), {}, {}

    config = next((cfg for cfg in DEFAULT_WINDOW_CONFIGS if cfg.label == window_label), None)
    if config is None:
        try:
            size_seconds = int(window_label.rstrip("s"))
            config = WindowConfig(size_seconds=size_seconds, label=window_label)
        except Exception:
            config = WindowConfig(size_seconds=30, label="30s")

    engine = WindowEngine()
    t_start, t_end = engine.session_span(*available)
    windows_df = engine.generate(t_start, t_end, config)
    return windows_df, {}, _load_node_feature_map(session_dir, window_label)


def _load_node_feature_map(
    session_dir: Path,
    window_label: str,
) -> dict[tuple[str, str, str], dict[str, object]]:
    path = session_dir / "node_features" / f"node_features_{window_label}.csv"
    if not path.exists():
        return {}
    try:
        features_df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        LOGGER.warning("Failed to load %s: %s", path, exc)
        return {}

    required = {"window_id", "node_id", "node_type"}
    if not required.issubset(features_df.columns):
        return {}

    features_df = features_df.copy()
    features_df["window_id"] = features_df["window_id"].astype(str)
    features_df["node_id"] = features_df["node_id"].fillna("").astype(str).str.strip()
    features_df["node_type"] = features_df["node_type"].fillna("").astype(str).str.strip().str.lower()
    features_df = features_df[
        features_df["window_id"].ne("")
        & features_df["node_id"].ne("")
        & features_df["node_type"].ne("")
    ].copy()
    if features_df.empty:
        return {}

    payload_columns = [
        col for col in NODE_FEATURE_COLUMNS
        if col in features_df.columns and col not in {"session_id", "window_id", "node_id", "node_type"}
    ]
    return {
        (str(row["window_id"]), str(row["node_id"]), str(row["node_type"])): {
            col: row[col]
            for col in payload_columns
        }
        for _, row in features_df.iterrows()
    }


def _build_window_graph(
    session_id: str,
    window_id: str,
    window_start: float,
    window_end: float,
    window_events: pd.DataFrame,
    raw_behavior_slice: pd.DataFrame,
    tab_level: str,
    window_features: dict[str, object],
    node_feature_map: dict[tuple[str, str, str], dict[str, object]],
) -> WindowGraphView:
    if window_events.empty:
        return WindowGraphView(
            session_id=session_id,
            window_id=window_id,
            window_start=window_start,
            window_end=window_end,
            nodes=[],
            edges=[],
            window_features=window_features,
        )

    events = window_events.copy()
    events["app_name"] = events["app_name"].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    events["url"] = events["url"].fillna("").astype(str).str.strip()
    events["tab_node_id"] = events["url"].map(lambda value: _resolve_tab_id(value, tab_level))

    window_duration = max(0.001, window_end - window_start)

    app_transitions = _build_transition_table(events["app_name"], events["timestamp"], events["duration_ms"])
    tab_transitions = _build_transition_table(
        events["tab_node_id"],
        events["timestamp"],
        events["duration_ms"],
        require_non_empty=True,
    )
    app_tab_links = _build_app_tab_links(events)

    app_node_rows = _build_app_nodes(
        events=events,
        app_transitions=app_transitions,
        app_tab_links=app_tab_links,
        window_start=window_start,
        window_end=window_end,
    )
    tab_node_rows = _build_tab_nodes(
        events=events,
        raw_behavior_slice=raw_behavior_slice,
        tab_transitions=tab_transitions,
        app_tab_links=app_tab_links,
        tab_level=tab_level,
        window_end=window_end,
        window_duration=window_duration,
    )

    app_feature_map = {row["node_label"]: row for row in app_node_rows}
    tab_feature_map = {row["node_label"]: row for row in tab_node_rows}

    nodes: list[GraphNodeView] = []
    for row in app_node_rows:
        label = str(row["node_label"])
        nodes.append(
            GraphNodeView(
                key=f"app::{label}",
                label=label,
                node_kind="app",
                feature_node_type="app",
                feature_node_id=label,
                features=_build_app_feature_payload(
                    base_features={
                    "type_app": row["type_app"],
                    "role": row["role"],
                    "usage_time": row["usage_time"],
                    "frequency": row["frequency"],
                    "recency": row["recency"],
                    "task_affiliation": row["task_affiliation"],
                    "centrality": row["centrality"],
                    "switch_in": row["switch_in"],
                    "switch_out": row["switch_out"],
                    },
                    window_id=window_id,
                    node_id=label,
                    node_feature_map=node_feature_map,
                ),
            )
        )

    for row in tab_node_rows:
        label = str(row["node_label"])
        nodes.append(
            GraphNodeView(
                key=f"tab::{label}",
                label=label,
                node_kind="tab",
                feature_node_type=tab_level,
                feature_node_id=label,
                features=_build_tab_feature_payload(
                    base_features={
                    "type_site": row["type_site"],
                    "role": row["role"],
                    "dwell_time": row["dwell_time"],
                    "frequency": row["frequency"],
                    "recency": row["recency"],
                    "scroll_speed": row["scroll_speed"],
                    "scroll_depth": row["scroll_depth"],
                    "tab_switch_rate": row["tab_switch_rate"],
                    "content_stability": row["content_stability"],
                    },
                    window_id=window_id,
                    node_id=label,
                    node_type=tab_level,
                    node_feature_map=node_feature_map,
                ),
            )
        )

    total_node_count = max(1, len(nodes))
    centrality_map = _compute_node_centrality(
        node_count=total_node_count,
        app_transitions=app_transitions,
        tab_transitions=tab_transitions,
        app_tab_links=app_tab_links,
        app_feature_map=app_feature_map,
        tab_feature_map=tab_feature_map,
    )
    for node in nodes:
        node.features["centrality"] = centrality_map.get(node.key, node.features.get("centrality"))

    app_edge_rows = _build_app_edges(
        app_transitions=app_transitions,
        app_feature_map=app_feature_map,
        window_duration=window_duration,
        events=events,
    )
    tab_edge_rows = _build_tab_edges(
        tab_transitions=tab_transitions,
        tab_feature_map=tab_feature_map,
        window_duration=window_duration,
    )
    app_tab_edge_rows = _build_app_tab_edges(
        app_tab_links=app_tab_links,
        app_feature_map=app_feature_map,
        tab_feature_map=tab_feature_map,
    )

    edges: list[GraphEdgeView] = []
    for row in app_edge_rows:
        edges.append(
            GraphEdgeView(
                edge_id=f"app_app::{row['source']}::{row['target']}",
                source_key=f"app::{row['source']}",
                target_key=f"app::{row['target']}",
                source_label=str(row["source"]),
                target_label=str(row["target"]),
                edge_kind="app_app",
                features={
                    "transition_count": row["transition_count"],
                    "transition_rate": row["transition_rate"],
                    "semantic_distance": row["semantic_distance"],
                    "task_similarity": row["task_similarity"],
                    "interruption_cost": row["interruption_cost"],
                    "resume_latency": row["resume_latency"],
                    "directionality": row["directionality"],
                },
            )
        )

    for row in tab_edge_rows:
        edges.append(
            GraphEdgeView(
                edge_id=f"tab_tab::{row['source']}::{row['target']}",
                source_key=f"tab::{row['source']}",
                target_key=f"tab::{row['target']}",
                source_label=str(row["source"]),
                target_label=str(row["target"]),
                edge_kind="tab_tab",
                features={
                    "switch_count": row["switch_count"],
                    "switch_rate": row["switch_rate"],
                    "semantic_gap": row["semantic_gap"],
                    "task_continuity": row["task_continuity"],
                    "navigation_pattern": row["navigation_pattern"],
                },
            )
        )

    for row in app_tab_edge_rows:
        edges.append(
            GraphEdgeView(
                edge_id=f"app_tab::{row['source']}::{row['target']}",
                source_key=f"app::{row['source']}",
                target_key=f"tab::{row['target']}",
                source_label=str(row["source"]),
                target_label=str(row["target"]),
                edge_kind="app_tab",
                features={
                    "latency": row["latency"],
                    "copy_paste": row["copy_paste"],
                    "sequence_pattern": row["sequence_pattern"],
                    "semantic_alignment": row["semantic_alignment"],
                    "usage_dependency": row["usage_dependency"],
                },
            )
        )

    return WindowGraphView(
        session_id=session_id,
        window_id=window_id,
        window_start=window_start,
        window_end=window_end,
        nodes=nodes,
        edges=edges,
        window_features=window_features,
    )


def _build_app_feature_payload(
    base_features: dict[str, object],
    window_id: str,
    node_id: str,
    node_feature_map: dict[tuple[str, str, str], dict[str, object]],
) -> dict[str, object]:
    payload = {
        "type_app": None,
        "role": None,
        "usage_time": None,
        "frequency": None,
        "scroll_intensity": None,
        "interaction_rate": None,
        "recency": None,
        "task_affiliation": None,
        "centrality": None,
        "switch_in": None,
        "switch_out": None,
    }
    payload.update(base_features)
    payload.update(node_feature_map.get((window_id, node_id, "app"), {}))
    return payload


def _build_tab_feature_payload(
    base_features: dict[str, object],
    window_id: str,
    node_id: str,
    node_type: str,
    node_feature_map: dict[tuple[str, str, str], dict[str, object]],
) -> dict[str, object]:
    payload = {
        "type_site": None,
        "role": None,
        "dwell_time": None,
        "usage_time": None,
        "frequency": None,
        "scroll_speed": None,
        "scroll_intensity": None,
        "interaction_rate": None,
        "scroll_depth": None,
        "recency": None,
        "tab_switch_rate": None,
        "content_stability": None,
    }
    payload.update(base_features)

    exported = node_feature_map.get((window_id, node_id, node_type), {})
    if "usage_time" in exported:
        payload["usage_time"] = exported["usage_time"]
        if payload.get("dwell_time") is None:
            payload["dwell_time"] = exported["usage_time"]
    if "frequency" in exported:
        payload["frequency"] = exported["frequency"]
    if "scroll_intensity" in exported:
        payload["scroll_intensity"] = exported["scroll_intensity"]
    if "interaction_rate" in exported:
        payload["interaction_rate"] = exported["interaction_rate"]
    return payload


def _build_transition_table(
    node_series: pd.Series,
    timestamp_series: pd.Series,
    duration_series: pd.Series,
    require_non_empty: bool = False,
) -> pd.DataFrame:
    table = pd.DataFrame(
        {
            "source": node_series.astype(str),
            "target": node_series.shift(-1).astype(str),
            "timestamp": pd.to_numeric(timestamp_series, errors="coerce"),
            "duration_ms": pd.to_numeric(duration_series, errors="coerce").fillna(0.0),
        }
    )
    table = table.iloc[:-1].copy()
    if require_non_empty:
        table = table[
            table["source"].astype(str).str.len().gt(0)
            & table["target"].astype(str).str.len().gt(0)
        ]
    else:
        table = table[
            table["source"].astype(str).str.len().gt(0)
            & table["target"].astype(str).str.len().gt(0)
        ]
    table = table[table["source"] != "nan"]
    table = table[table["target"] != "nan"]
    return table.reset_index(drop=True)


def _build_app_tab_links(events: pd.DataFrame) -> pd.DataFrame:
    links = events[["app_name", "tab_node_id", "timestamp", "duration_ms"]].copy()
    links = links[
        links["app_name"].astype(str).str.len().gt(0)
        & links["tab_node_id"].astype(str).str.len().gt(0)
    ].reset_index(drop=True)
    return links


def _build_app_nodes(
    events: pd.DataFrame,
    app_transitions: pd.DataFrame,
    app_tab_links: pd.DataFrame,
    window_start: float,
    window_end: float,
) -> list[dict[str, object]]:
    groups = (
        events.groupby("app_name", as_index=False)
        .agg(
            usage_time=("duration_ms", "sum"),
            frequency=("app_name", "size"),
            last_timestamp=("timestamp", "max"),
        )
    )

    switch_in_map = app_transitions.groupby("target").size().to_dict()
    switch_out_map = app_transitions.groupby("source").size().to_dict()

    rows: list[dict[str, object]] = []
    if groups.empty:
        return rows

    max_usage = float(groups["usage_time"].max()) if not groups.empty else 0.0

    for row in groups.itertuples(index=False):
        app_name = str(row.app_name)
        type_app = _classify_app_type(app_name)
        task_affiliation = _classify_task_affiliation(app_name)
        role = "primary" if float(row.usage_time) >= max_usage else "support"
        rows.append(
            {
                "node_label": app_name,
                "type_app": type_app,
                "role": role,
                "usage_time": round(float(row.usage_time), 2),
                "frequency": int(row.frequency),
                "recency": round(float(window_end - float(row.last_timestamp)), 3),
                "task_affiliation": task_affiliation,
                "centrality": None,
                "switch_in": int(switch_in_map.get(app_name, 0)),
                "switch_out": int(switch_out_map.get(app_name, 0)),
            }
        )

    return rows


def _build_tab_nodes(
    events: pd.DataFrame,
    raw_behavior_slice: pd.DataFrame,
    tab_transitions: pd.DataFrame,
    app_tab_links: pd.DataFrame,
    tab_level: str,
    window_end: float,
    window_duration: float,
) -> list[dict[str, object]]:
    tab_events = events[events["tab_node_id"].astype(str).str.len().gt(0)].copy()
    if tab_events.empty:
        return []

    groups = (
        tab_events.groupby("tab_node_id", as_index=False)
        .agg(
            dwell_time=("duration_ms", "sum"),
            frequency=("tab_node_id", "size"),
            last_timestamp=("timestamp", "max"),
        )
    )

    raw_slice = raw_behavior_slice.copy()
    raw_slice["url"] = raw_slice.get("url", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    raw_slice["tab_node_id"] = raw_slice["url"].map(lambda value: _resolve_tab_id(value, tab_level))
    raw_slice["event_type"] = raw_slice.get("event_type", pd.Series(dtype=str)).fillna("").astype(str)
    raw_slice["scroll_delta_y"] = pd.to_numeric(raw_slice.get("scroll_delta_y", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    raw_slice["scroll_total_y"] = pd.to_numeric(raw_slice.get("scroll_total_y", pd.Series(dtype=float)), errors="coerce").fillna(0.0)

    tab_switch_touch = pd.concat([tab_transitions["source"], tab_transitions["target"]], ignore_index=True)
    tab_switch_map = tab_switch_touch.value_counts().to_dict() if not tab_transitions.empty else {}

    site_types: dict[str, str] = {}
    dwell_map = groups.set_index("tab_node_id")["dwell_time"].to_dict()
    non_distractors = {
        tab_id: dwell
        for tab_id, dwell in dwell_map.items()
        if _classify_site_role(str(tab_id), _classify_site_type(str(tab_id))) != "distractor"
    }
    primary_tab = max(non_distractors, key=non_distractors.get) if non_distractors else None

    rows: list[dict[str, object]] = []
    for row in groups.itertuples(index=False):
        tab_id = str(row.tab_node_id)
        site_type = _classify_site_type(tab_id)
        site_types[tab_id] = site_type
        base_role = _classify_site_role(tab_id, site_type)
        role = "primary" if primary_tab == tab_id and base_role != "distractor" else base_role
        if role not in {"primary", "support", "distractor"}:
            role = "support"

        tab_raw = raw_slice[raw_slice["tab_node_id"] == tab_id]
        scroll_rows = tab_raw[tab_raw["event_type"].str.contains("scroll", case=False, na=False)]
        scroll_speed = None
        scroll_depth = None
        if not scroll_rows.empty:
            scroll_speed = round(float(scroll_rows["scroll_delta_y"].abs().sum() / max(window_duration, 0.001)), 4)
            scroll_depth = round(float(scroll_rows["scroll_total_y"].abs().max()), 4)

        content_stability = None
        title_candidates = pd.Series(dtype=str)
        if "title" in tab_raw.columns:
            title_candidates = tab_raw["title"].fillna("").astype(str).str.strip()
        if title_candidates.empty and "window_title" in tab_raw.columns:
            title_candidates = tab_raw["window_title"].fillna("").astype(str).str.strip()
        title_candidates = title_candidates[title_candidates.ne("")]
        if not title_candidates.empty:
            content_stability = round(float(title_candidates.value_counts(normalize=True).iloc[0]), 4)

        rows.append(
            {
                "node_label": tab_id,
                "type_site": site_type,
                "role": role,
                "dwell_time": round(float(row.dwell_time), 2),
                "frequency": int(row.frequency),
                "recency": round(float(window_end - float(row.last_timestamp)), 3),
                "scroll_speed": scroll_speed,
                "scroll_depth": scroll_depth,
                "tab_switch_rate": round(float(tab_switch_map.get(tab_id, 0)) / max(window_duration, 0.001), 4),
                "content_stability": content_stability,
            }
        )

    return rows


def _compute_node_centrality(
    node_count: int,
    app_transitions: pd.DataFrame,
    tab_transitions: pd.DataFrame,
    app_tab_links: pd.DataFrame,
    app_feature_map: dict[str, dict[str, object]],
    tab_feature_map: dict[str, dict[str, object]],
) -> dict[str, float]:
    degree_map: dict[str, int] = {}

    def _bump(node_key: str) -> None:
        degree_map[node_key] = degree_map.get(node_key, 0) + 1

    for row in app_transitions.itertuples(index=False):
        _bump(f"app::{row.source}")
        _bump(f"app::{row.target}")
    for row in tab_transitions.itertuples(index=False):
        _bump(f"tab::{row.source}")
        _bump(f"tab::{row.target}")
    for row in app_tab_links.itertuples(index=False):
        _bump(f"app::{row.app_name}")
        _bump(f"tab::{row.tab_node_id}")

    denominator = max(1, node_count - 1)
    return {
        node_key: round(float(degree) / denominator, 4)
        for node_key, degree in degree_map.items()
    }


def _build_app_edges(
    app_transitions: pd.DataFrame,
    app_feature_map: dict[str, dict[str, object]],
    window_duration: float,
    events: pd.DataFrame,
) -> list[dict[str, object]]:
    if app_transitions.empty:
        return []

    grouped = (
        app_transitions.groupby(["source", "target"], as_index=False)
        .agg(
            transition_count=("timestamp", "size"),
            avg_duration=("duration_ms", "mean"),
        )
    )
    reverse_map = grouped.set_index(["target", "source"])["transition_count"].to_dict()
    sequence = events["app_name"].astype(str).tolist()
    durations = pd.to_numeric(events["duration_ms"], errors="coerce").fillna(0.0).tolist()

    rows: list[dict[str, object]] = []
    for row in grouped.itertuples(index=False):
        source = str(row.source)
        target = str(row.target)
        task_similarity = _task_similarity(
            app_feature_map.get(source, {}).get("task_affiliation"),
            app_feature_map.get(target, {}).get("task_affiliation"),
        )
        reverse = int(reverse_map.get((source, target), 0))
        directionality = round(float(row.transition_count) / max(1, float(row.transition_count + reverse)), 4)
        resume_latency = _estimate_resume_latency(sequence, durations, source, target)
        rows.append(
            {
                "source": source,
                "target": target,
                "transition_count": int(row.transition_count),
                "transition_rate": round(float(row.transition_count) / max(window_duration, 0.001), 4),
                "semantic_distance": None if task_similarity is None else round(1.0 - task_similarity, 4),
                "task_similarity": task_similarity,
                "interruption_cost": None,
                "resume_latency": resume_latency,
                "directionality": directionality,
            }
        )
    return rows


def _build_tab_edges(
    tab_transitions: pd.DataFrame,
    tab_feature_map: dict[str, dict[str, object]],
    window_duration: float,
) -> list[dict[str, object]]:
    if tab_transitions.empty:
        return []

    grouped = (
        tab_transitions.groupby(["source", "target"], as_index=False)
        .agg(
            switch_count=("timestamp", "size"),
        )
    )

    rows: list[dict[str, object]] = []
    for row in grouped.itertuples(index=False):
        source = str(row.source)
        target = str(row.target)
        source_type = tab_feature_map.get(source, {}).get("type_site")
        target_type = tab_feature_map.get(target, {}).get("type_site")
        source_role = tab_feature_map.get(source, {}).get("role")
        target_role = tab_feature_map.get(target, {}).get("role")
        rows.append(
            {
                "source": source,
                "target": target,
                "switch_count": int(row.switch_count),
                "switch_rate": round(float(row.switch_count) / max(window_duration, 0.001), 4),
                "semantic_gap": None if source_type is None or target_type is None else float(source_type != target_type),
                "task_continuity": None
                if source_role is None or target_role is None
                else float(source_role != "distractor" and target_role != "distractor"),
                "navigation_pattern": _navigation_pattern(source, target),
            }
        )
    return rows


def _build_app_tab_edges(
    app_tab_links: pd.DataFrame,
    app_feature_map: dict[str, dict[str, object]],
    tab_feature_map: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    if app_tab_links.empty:
        return []

    grouped = (
        app_tab_links.groupby(["app_name", "tab_node_id"], as_index=False)
        .agg(
            association_count=("timestamp", "size"),
        )
    )
    app_frequency = app_tab_links.groupby("app_name").size().to_dict()

    rows: list[dict[str, object]] = []
    for row in grouped.itertuples(index=False):
        app_name = str(row.app_name)
        tab_id = str(row.tab_node_id)
        app_affiliation = app_feature_map.get(app_name, {}).get("task_affiliation")
        site_type = tab_feature_map.get(tab_id, {}).get("type_site")
        rows.append(
            {
                "source": app_name,
                "target": tab_id,
                "latency": None,
                "copy_paste": None,
                "sequence_pattern": "co_occurrence",
                "semantic_alignment": _semantic_alignment(app_affiliation, site_type),
                "usage_dependency": round(float(row.association_count) / max(1, int(app_frequency.get(app_name, 1))), 4),
            }
        )
    return rows


def _compute_positions(nodes: list[GraphNodeView], width: int, height: int) -> dict[str, tuple[float, float]]:
    if not nodes:
        return {}

    app_nodes = [node for node in nodes if node.node_kind == "app"]
    tab_nodes = [node for node in nodes if node.node_kind == "tab"]
    positions: dict[str, tuple[float, float]] = {}

    if app_nodes and tab_nodes:
        positions.update(_arc_positions(app_nodes, width, height, start_deg=200, end_deg=340, y_bias=-18))
        positions.update(_arc_positions(tab_nodes, width, height, start_deg=20, end_deg=160, y_bias=18))
        return positions

    only_nodes = app_nodes if app_nodes else tab_nodes
    center_x = width / 2
    center_y = height / 2
    radius = min(width, height) * 0.3
    for index, node in enumerate(only_nodes):
        angle = (2 * math.pi * index / max(1, len(only_nodes))) - (math.pi / 2)
        positions[node.key] = (
            center_x + radius * math.cos(angle),
            center_y + radius * math.sin(angle),
        )
    return positions


def _arc_positions(
    nodes: list[GraphNodeView],
    width: int,
    height: int,
    start_deg: float,
    end_deg: float,
    y_bias: float,
) -> dict[str, tuple[float, float]]:
    center_x = width / 2
    center_y = height / 2 + y_bias
    radius_x = width * 0.34
    radius_y = height * 0.24
    positions: dict[str, tuple[float, float]] = {}

    if len(nodes) == 1:
        angle = math.radians((start_deg + end_deg) / 2)
        positions[nodes[0].key] = (
            center_x + radius_x * math.cos(angle),
            center_y + radius_y * math.sin(angle),
        )
        return positions

    for index, node in enumerate(nodes):
        frac = index / max(1, len(nodes) - 1)
        angle = math.radians(start_deg + (end_deg - start_deg) * frac)
        positions[node.key] = (
            center_x + radius_x * math.cos(angle),
            center_y + radius_y * math.sin(angle),
        )
    return positions


def _edge_color(edge_kind: str) -> str:
    if edge_kind == "app_app":
        return _APP_EDGE
    if edge_kind == "tab_tab":
        return _TAB_EDGE
    return _APP_TAB_EDGE


def _edge_label(edge: GraphEdgeView) -> str:
    if edge.edge_kind == "app_app":
        return str(edge.features.get("transition_count", ""))
    if edge.edge_kind == "tab_tab":
        return str(edge.features.get("switch_count", ""))
    return ""


def _window_footer_text(window_graph: WindowGraphView) -> str:
    summary = []
    if "switch_rate" in window_graph.window_features:
        summary.append(f"switch_rate={_format_value(window_graph.window_features.get('switch_rate'))}")
    if "focus_duration_ratio" in window_graph.window_features:
        summary.append(f"focus_ratio={_format_value(window_graph.window_features.get('focus_duration_ratio'))}")
    return "   ".join(summary) if summary else f"duration={window_graph.duration_seconds:.2f}s"


def _resolve_tab_id(url: str, tab_level: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if tab_level == "url":
        return text
    return _extract_domain(text) or ""


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


def _classify_app_type(app_name: str) -> str:
    name = str(app_name).lower()
    if any(token in name for token in ("chrome", "firefox", "edge", "browser")):
        return "browser"
    if any(token in name for token in ("code", "pycharm", "idea", "notepad++", "sublime")):
        return "editor"
    if any(token in name for token in ("terminal", "powershell", "cmd", "bash")):
        return "terminal"
    if any(token in name for token in ("teams", "slack", "discord", "zoom")):
        return "communication"
    if any(token in name for token in ("word", "excel", "powerpoint")):
        return "office"
    if any(token in name for token in ("youtube", "spotify", "vlc", "media")):
        return "media"
    if any(token in name for token in ("explorer", "settings", "system")):
        return "system"
    return "unknown"


def _classify_task_affiliation(app_name: str) -> str:
    name = str(app_name).lower()
    if any(token in name for token in ("code", "pycharm", "idea", "terminal", "powershell", "cmd")):
        return "development"
    if any(token in name for token in ("chrome", "edge", "firefox", "browser")):
        return "research"
    if any(token in name for token in ("teams", "slack", "discord", "zoom", "outlook")):
        return "communication"
    if any(token in name for token in ("word", "excel", "powerpoint")):
        return "documentation"
    if any(token in name for token in ("youtube", "spotify", "vlc")):
        return "media"
    return "unknown"


def _classify_site_type(tab_id: str) -> str:
    domain = _extract_domain(tab_id) or str(tab_id).lower()
    if any(token in domain for token in ("chatgpt", "stackoverflow", "github", "docs", "readthedocs")):
        return "documentation"
    if any(token in domain for token in ("google", "bing", "duckduckgo")):
        return "search"
    if any(token in domain for token in ("youtube", "netflix", "spotify")):
        return "media"
    if any(token in domain for token in ("facebook", "instagram", "tiktok", "x.com", "twitter")):
        return "social"
    if any(token in domain for token in ("gmail", "outlook", "teams", "slack", "discord")):
        return "communication"
    if any(token in domain for token in ("github", "gitlab")):
        return "coding"
    return "unknown"


def _classify_site_role(tab_id: str, site_type: str) -> str:
    domain = _extract_domain(tab_id) or str(tab_id).lower()
    if domain in _DISTRACTOR_DOMAINS or site_type in {"media", "social"}:
        return "distractor"
    return "support"


def _task_similarity(left: object, right: object) -> float | None:
    if left in (None, "", "unknown") or right in (None, "", "unknown"):
        return None
    return 1.0 if left == right else 0.0


def _semantic_alignment(app_affiliation: object, site_type: object) -> float | None:
    if app_affiliation in (None, "", "unknown") or site_type in (None, "", "unknown"):
        return None
    mapping = {
        "development": {"documentation", "coding", "search"},
        "research": {"documentation", "search"},
        "communication": {"communication"},
        "documentation": {"documentation"},
        "media": {"media"},
    }
    allowed = mapping.get(str(app_affiliation), set())
    return 1.0 if str(site_type) in allowed else 0.0


def _estimate_resume_latency(
    sequence: list[str],
    durations: list[float],
    source: str,
    target: str,
) -> float | None:
    latencies: list[float] = []
    for index in range(len(sequence) - 1):
        if sequence[index] != source or sequence[index + 1] != target:
            continue
        total_ms = 0.0
        for j in range(index + 1, len(sequence)):
            total_ms += float(durations[j - 1]) if (j - 1) < len(durations) else 0.0
            if sequence[j] == source:
                latencies.append(total_ms)
                break
    if not latencies:
        return None
    return round(sum(latencies) / len(latencies), 2)


def _navigation_pattern(source: str, target: str) -> str:
    if source == target:
        return "self_loop"
    source_domain = _extract_domain(source) or source
    target_domain = _extract_domain(target) or target
    if source_domain == target_domain:
        return "within_domain"
    return "cross_site"


def _distance_to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / float(dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _format_value(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if math.isnan(value):
            return "N/A"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m feature_engineering.graph_viewer",
        description="Open a GUI to view one session as a table of window graphs.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to the data directory (default: cognitive_system/data/)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional session id to open initially.",
    )
    parser.add_argument(
        "--window-label",
        default="30s",
        choices=[cfg.label for cfg in DEFAULT_WINDOW_CONFIGS],
        help="Window size label to display.",
    )
    parser.add_argument(
        "--tab-level",
        default="domain",
        choices=["domain", "url"],
        help="How tab nodes are resolved from URLs.",
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=3,
        help="Number of window cells per row.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    data_dir = (
        Path(args.data_dir)
        if args.data_dir
        else Path(__file__).resolve().parent.parent / "data"
    )

    viewer = SessionWindowGraphViewer(
        data_dir=data_dir,
        session_id=args.session_id,
        window_label=args.window_label,
        tab_level=args.tab_level,
        columns=args.columns,
    )
    viewer.run()


if __name__ == "__main__":
    main()
