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
_EDGE_FAST = "#d95f59"
_EDGE_MEDIUM = "#d99a2b"
_EDGE_DEEP = "#5c73d8"
_EDGE_PERSIST = "#2f9e83"
_EDGE_MUTED = "#9aaabd"
_EDGE_HOVER = "#1d2937"
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
    system_metrics: pd.DataFrame | None = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, float(self.window_end) - float(self.window_start))

    @property
    def app_node_count(self) -> int:
        return sum(1 for node in self.nodes if node.node_kind == "app")

    @property
    def tab_node_count(self) -> int:
        return sum(1 for node in self.nodes if node.node_kind in {"tab", "page"})


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

        raw_ts = behavior_df["timestamp"].to_numpy(dtype=float, copy=False)
        keyboard_df = raw_streams.get("keyboard", pd.DataFrame())
        mouse_df = raw_streams.get("mouse", pd.DataFrame())
        system_df = raw_streams.get("system_metrics", pd.DataFrame())
        keyboard_ts = keyboard_df["timestamp"].to_numpy(dtype=float, copy=False) if not keyboard_df.empty and "timestamp" in keyboard_df.columns else []
        mouse_ts = mouse_df["timestamp"].to_numpy(dtype=float, copy=False) if not mouse_df.empty and "timestamp" in mouse_df.columns else []
        system_ts = system_df["timestamp"].to_numpy(dtype=float, copy=False) if not system_df.empty and "timestamp" in system_df.columns else []
        engine = WindowEngine()

        window_graphs: list[WindowGraphView] = []
        for window_row in windows_df.itertuples(index=False):
            feature_row = window_feature_map.get(window_row.window_id, {})
            window_start = float(window_row.window_start)
            window_end = float(window_row.window_end)
            window_events = cleaner.slice_events_for_window(cleaned_events, window_start, window_end)

            raw_lo, raw_hi = engine.window_slice_indices(
                raw_ts,
                window_start,
                window_end,
            )
            raw_slice = behavior_df.iloc[raw_lo:raw_hi].copy()
            if len(keyboard_ts):
                key_lo, key_hi = engine.window_slice_indices(keyboard_ts, window_start, window_end)
                keyboard_slice = keyboard_df.iloc[key_lo:key_hi].copy()
            else:
                keyboard_slice = pd.DataFrame()
            if len(mouse_ts):
                mouse_lo, mouse_hi = engine.window_slice_indices(mouse_ts, window_start, window_end)
                mouse_slice = mouse_df.iloc[mouse_lo:mouse_hi].copy()
            else:
                mouse_slice = pd.DataFrame()
            if len(system_ts):
                system_lo, system_hi = engine.window_slice_indices(system_ts, window_start, window_end)
                system_slice = system_df.iloc[system_lo:system_hi].copy()
            else:
                system_slice = pd.DataFrame()

            window_graphs.append(
                _build_window_graph(
                    session_id=session_dir.name,
                    window_id=str(window_row.window_id),
                    window_start=window_start,
                    window_end=window_end,
                    window_events=window_events,
                    raw_behavior_slice=raw_slice,
                    raw_keyboard_slice=keyboard_slice,
                    raw_mouse_slice=mouse_slice,
                    raw_system_slice=system_slice,
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
        self._viewport_width = 0
        self._resize_after_id: object | None = None
        self._rendering_windows = False

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
        body.add(left, stretch="always", minsize=460)
        body.add(right, minsize=320)

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
            wraplength=340,
        ).pack(anchor="w", fill="x", pady=(10, 4))

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
        previous_width = self._viewport_width
        self._viewport_width = int(event.width)
        self.canvas.itemconfigure(self.grid_window, width=event.width)
        if self._rendering_windows or self._bundle is None or self._bundle.error:
            return
        if abs(self._viewport_width - previous_width) >= 48:
            self._schedule_layout_refresh()

    def _schedule_layout_refresh(self) -> None:
        if self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.root.after(220, self._refresh_layout_after_resize)

    def _refresh_layout_after_resize(self) -> None:
        self._resize_after_id = None
        self.render_windows()

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
        self._rendering_windows = True
        try:
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

            columns, graph_width, graph_height = self._window_grid_metrics()
            max_configured_columns = max(5, int(self.columns_var.get()), columns)
            for col in range(max_configured_columns):
                self.grid_frame.grid_columnconfigure(col, weight=0, uniform="")
            for col in range(columns):
                self.grid_frame.grid_columnconfigure(col, weight=1, uniform="window_cols")

            for index, window_graph in enumerate(self._bundle.windows):
                row = index // columns
                col = index % columns
                card = self._build_window_card(self.grid_frame, window_graph, graph_width, graph_height)
                card.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)

            if self._bundle.windows:
                first = self._bundle.windows[0]
                if first.nodes:
                    self._show_node_details(first, first.nodes[0])
                else:
                    self._show_window_details(first)
            self._on_frame_configure()
        finally:
            self._rendering_windows = False

    def _window_grid_metrics(self) -> tuple[int, int, int]:
        requested_columns = max(1, int(self.columns_var.get()))
        viewport_width = self._viewport_width or self.canvas.winfo_width()
        if viewport_width <= 1:
            viewport_width = (self.card_width + 24) * requested_columns

        min_outer_width = 310
        usable_width = max(260, int(viewport_width) - 8)
        max_columns_for_width = max(1, usable_width // min_outer_width)
        columns = max(1, min(requested_columns, max_columns_for_width))

        horizontal_padding = 16 * columns
        outer_width = max(min_outer_width, int((usable_width - horizontal_padding) / columns))
        graph_width = max(240, outer_width - 22)
        graph_height = max(170, min(240, int(graph_width * 0.58)))
        return columns, graph_width, graph_height

    def _build_window_card(self, parent, window_graph: WindowGraphView, graph_width: int, graph_height: int):
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
            width=graph_width,
            height=graph_height,
            bg=_CANVAS_BG,
            highlightthickness=0,
        )
        canvas.pack(fill="x", expand=False)
        draw_state = self._draw_window_graph(canvas, window_graph, graph_width, graph_height)
        self._canvas_registry[canvas] = draw_state
        canvas.bind("<Button-1>", lambda event, c=canvas: self._on_canvas_click(event, c))
        canvas.bind("<Motion>", lambda event, c=canvas: self._on_canvas_motion(event, c))
        canvas.bind("<Leave>", lambda _event, c=canvas: self._on_canvas_leave(c))

        footer = tk.Frame(card, bg=_CARD_BG)
        footer.pack(fill="x", pady=(8, 0))
        footer.grid_columnconfigure(0, weight=1, minsize=80)

        tk.Label(
            footer,
            text=_window_footer_text(window_graph),
            bg=_CARD_BG,
            fg=_MUTED,
            justify="left",
            anchor="w",
            font=("Segoe UI", 9),
            wraplength=max(90, graph_width - 165),
        ).grid(row=0, column=0, sticky="ew")

        button_bar = tk.Frame(footer, bg=_CARD_BG)
        button_bar.grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(
            button_bar,
            text="Details",
            width=8,
            command=lambda wg=window_graph: self._open_window_details(wg),
        ).pack(side="left")

        ttk.Button(
            button_bar,
            text="Metrics",
            width=8,
            command=lambda wg=window_graph: self._open_system_pattern(wg),
        ).pack(side="left", padx=(6, 0))

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
            return {
                "window_graph": window_graph,
                "node_positions": {},
                "node_radius": 18,
                "edge_hits": [],
                "node_hits": [],
                "node_map": {},
            }

        positions = _compute_positions(window_graph.nodes, width, height)
        node_radius = 18
        edge_hits: list[dict[str, object]] = []
        node_hits: list[dict[str, object]] = []
        _draw_edge_legend(canvas, width)
        edge_offsets = _edge_parallel_offsets(window_graph.edges)

        for edge in window_graph.edges:
            x1, y1 = positions[edge.source_key]
            x2, y2 = positions[edge.target_key]
            style = _edge_style(edge, window_graph.duration_seconds)
            line_color = style["color"]
            line_width = float(style["width"])
            dash = style["dash"]

            if edge.source_key == edge.target_key:
                loop_r = 16
                item_id = canvas.create_arc(
                    x1 - loop_r,
                    y1 - loop_r - 22,
                    x1 + loop_r,
                    y1 + loop_r - 4,
                    start=30,
                    extent=300,
                    style=tk.ARC,
                    outline=line_color,
                    width=line_width,
                    dash=dash,
                )
                edge_hits.append(
                    {
                        "edge": edge,
                        "bbox": (x1 - loop_r, y1 - loop_r - 22, x1 + loop_r, y1 + loop_r - 4),
                        "self_loop": True,
                        "items": [item_id],
                        "stroke_items": [item_id],
                        "style": style,
                    }
                )
            else:
                offset = float(edge_offsets.get(edge.edge_id, 0.0))
                draw_points, label_x, label_y = _offset_edge_points(x1, y1, x2, y2, offset)
                item_id = canvas.create_line(
                    *draw_points,
                    fill=line_color,
                    width=line_width,
                    arrow=tk.LAST,
                    smooth=True,
                    dash=dash,
                )
                edge_hits.append(
                    {
                        "edge": edge,
                        "segment": (x1, y1, x2, y2),
                        "curve_points": draw_points,
                        "self_loop": False,
                        "items": [item_id],
                        "stroke_items": [item_id],
                        "style": style,
                    }
                )
                label = _edge_label(edge)
                if label:
                    label_width = max(24, min(52, 10 + 7 * len(label)))
                    rect_id = canvas.create_rectangle(
                        label_x - label_width / 2,
                        label_y - 9,
                        label_x + label_width / 2,
                        label_y + 9,
                        fill="#ffffff",
                        outline="",
                    )
                    text_id = canvas.create_text(label_x, label_y, text=label, fill=line_color, font=("Segoe UI", 8, "bold"))
                    edge_hits[-1]["items"].extend([rect_id, text_id])

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
                    "items": [oval_id, text_id],
                    "circle_item": oval_id,
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
                    self._select_node(canvas, state, node_hit)
                    self._show_node_details(window_graph, node_map[node_hit["node_key"]])
                    return

        for node_key, (nx, ny) in node_positions.items():
            if ((x - nx) ** 2 + (y - ny) ** 2) <= (node_radius ** 2):
                node_hit = next((hit for hit in node_hits if hit.get("node_key") == node_key), None)
                if node_hit:
                    self._select_node(canvas, state, node_hit)
                self._show_node_details(window_graph, node_map[node_key])
                return

        edge_hit_result = self._hit_test_edge(state, x, y)
        if edge_hit_result is not None:
            edge_hit = edge_hit_result["edge_hit"]
            edge = edge_hit["edge"]
            self._select_edge(canvas, state, edge_hit)
            self._show_edge_details(window_graph, edge)
            return

        self._clear_selection(canvas, state)
        self._show_window_details(window_graph)

    def _on_canvas_motion(self, event, canvas) -> None:
        state = self._canvas_registry.get(canvas)
        if not state:
            return
        hit = self._hit_test_canvas(state, float(event.x), float(event.y))
        previous = state.get("hover_hit")
        if _same_canvas_hit(previous, hit):
            return
        self._clear_hover(canvas, state)
        state["hover_hit"] = hit
        if hit is None:
            canvas.configure(cursor="")
            return
        canvas.configure(cursor="hand2")
        if hit.get("kind") == "edge":
            _configure_edge_hit(canvas, hit["edge_hit"], highlighted=True)
        elif hit.get("kind") == "node":
            _configure_node_hit(canvas, hit["node_hit"], highlighted=True)

    def _on_canvas_leave(self, canvas) -> None:
        state = self._canvas_registry.get(canvas)
        if not state:
            return
        self._clear_hover(canvas, state)
        canvas.configure(cursor="")

    def _hit_test_canvas(self, state: dict[str, object], x: float, y: float) -> dict[str, object] | None:
        node_hits: list[dict[str, object]] = state.get("node_hits", [])
        for node_hit in node_hits:
            for bbox_name in ("circle_bbox", "label_bbox"):
                bbox = node_hit.get(bbox_name)
                if not bbox:
                    continue
                x1, y1, x2, y2 = bbox
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return {"kind": "node", "node_hit": node_hit}

        return self._hit_test_edge(state, x, y)

    def _hit_test_edge(self, state: dict[str, object], x: float, y: float) -> dict[str, object] | None:
        best_hit: dict[str, object] | None = None
        best_distance = 13.0
        for edge_hit in state.get("edge_hits", []):
            if edge_hit["self_loop"]:
                x1, y1, x2, y2 = edge_hit["bbox"]
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return {"kind": "edge", "edge_hit": edge_hit}
                continue

            points = edge_hit.get("curve_points")
            distance = (
                _distance_to_polyline(x, y, points)
                if isinstance(points, (list, tuple))
                else _distance_to_segment(x, y, *edge_hit["segment"])
            )
            if distance <= best_distance:
                best_distance = distance
                best_hit = edge_hit
        return {"kind": "edge", "edge_hit": best_hit} if best_hit is not None else None

    def _clear_hover(self, canvas, state: dict[str, object]) -> None:
        hit = state.pop("hover_hit", None)
        if not hit:
            return
        selected = state.get("selected_hit")
        if _same_canvas_hit(selected, hit):
            return
        if hit.get("kind") == "edge":
            _configure_edge_hit(canvas, hit["edge_hit"], highlighted=False)
        elif hit.get("kind") == "node":
            _configure_node_hit(canvas, hit["node_hit"], highlighted=False)

    def _clear_selection(self, canvas, state: dict[str, object]) -> None:
        selected = state.pop("selected_hit", None)
        if not selected:
            return
        if selected.get("kind") == "edge":
            _configure_edge_hit(canvas, selected["edge_hit"], highlighted=False)
        elif selected.get("kind") == "node":
            _configure_node_hit(canvas, selected["node_hit"], highlighted=False)

    def _select_edge(self, canvas, state: dict[str, object], edge_hit: dict[str, object]) -> None:
        self._clear_selection(canvas, state)
        selected = {"kind": "edge", "edge_hit": edge_hit}
        state["selected_hit"] = selected
        _configure_edge_hit(canvas, edge_hit, highlighted=True, selected=True)

    def _select_node(self, canvas, state: dict[str, object], node_hit: dict[str, object]) -> None:
        self._clear_selection(canvas, state)
        selected = {"kind": "node", "node_hit": node_hit}
        state["selected_hit"] = selected
        _configure_node_hit(canvas, node_hit, highlighted=True, selected=True)

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
        style = _edge_style(edge, window_graph.duration_seconds)
        raw_features = dict(edge.features)
        features = {
            "window_id": window_graph.window_id,
            "source_id": edge.source_key,
            "target_id": edge.target_key,
            "source_label": edge.source_label,
            "target_label": edge.target_label,
        }
        for key in (
            "copy_count",
            "cut_count",
            "paste_count",
            "copy_paste_count",
            "copy_paste_latency_mean_ms",
        ):
            if key in raw_features:
                features[key] = raw_features.get(key)
        for key, value in raw_features.items():
            if key not in features:
                features[key] = value
        features["duration_band"] = style.get("duration_band")
        features["visual_color"] = style.get("color")
        features["visual_reason"] = style.get("style_reason")
        self._show_info(f"Edge {window_graph.window_id}: {edge.source_label} -> {edge.target_label}", meta, features)

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
        detail_state = self._draw_window_graph(graph_canvas, window_graph, 1100, 320)
        self._canvas_registry[graph_canvas] = detail_state
        graph_canvas.bind("<Button-1>", lambda event, c=graph_canvas: self._on_canvas_click(event, c))
        graph_canvas.bind("<Motion>", lambda event, c=graph_canvas: self._on_canvas_motion(event, c))
        graph_canvas.bind("<Leave>", lambda _event, c=graph_canvas: self._on_canvas_leave(c))
        graph_canvas.bind(
            "<Configure>",
            lambda event, c=graph_canvas, wg=window_graph: self._redraw_detail_graph(c, wg, event.width, event.height),
        )

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
        edge_tree = ttk.Treeview(edge_frame, columns=("kind", "source", "target", "clip"), show="headings", height=16)
        edge_tree.heading("kind", text="kind")
        edge_tree.heading("source", text="source")
        edge_tree.heading("target", text="target")
        edge_tree.heading("clip", text="clip")
        edge_tree.column("kind", width=90, anchor="center")
        edge_tree.column("source", width=220, anchor="w")
        edge_tree.column("target", width=220, anchor="w")
        edge_tree.column("clip", width=70, anchor="center")
        edge_tree.pack(fill="both", expand=True, pady=(6, 0))

        for edge in window_graph.edges:
            clip = _edge_clipboard_activity(edge)
            edge_tree.insert("", "end", values=(edge.edge_kind, edge.source_label, edge.target_label, _format_value(clip)))

    def _redraw_detail_graph(self, canvas, window_graph: WindowGraphView, width: int, height: int) -> None:
        if width < 260 or height < 160:
            return
        state = self._draw_window_graph(canvas, window_graph, int(width), int(height))
        self._canvas_registry[canvas] = state

    def _open_system_pattern(self, window_graph: WindowGraphView) -> None:
        tk = self.tk
        ttk = self.ttk

        win = tk.Toplevel(self.root)
        win.title(f"System Pattern - {window_graph.session_id} - {window_graph.window_id}")
        win.geometry("980x640")
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
                f"duration={window_graph.duration_seconds:.2f}s"
            ),
            bg="#eef4fb",
            fg=_MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        metrics = window_graph.system_metrics.copy() if window_graph.system_metrics is not None else pd.DataFrame()
        chart = tk.Canvas(
            win,
            width=930,
            height=390,
            bg="#ffffff",
            highlightbackground=_CARD_BORDER,
            highlightthickness=1,
        )
        chart.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        _draw_system_pattern_chart(
            chart,
            metrics,
            window_graph.window_start,
            window_graph.window_end,
            width=930,
            height=390,
        )
        chart.bind(
            "<Configure>",
            lambda event, c=chart, m=metrics: _draw_system_pattern_chart(
                c,
                m,
                window_graph.window_start,
                window_graph.window_end,
                width=max(320, int(event.width)),
                height=max(220, int(event.height)),
            ),
        )

        summary_frame = tk.Frame(win, bg="#eef4fb", padx=14, pady=10)
        summary_frame.pack(fill="x")
        tk.Label(
            summary_frame,
            text="Summary",
            bg="#eef4fb",
            fg=_TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        tree = ttk.Treeview(summary_frame, columns=("metric", "mean", "max"), show="headings", height=6)
        tree.heading("metric", text="metric")
        tree.heading("mean", text="mean")
        tree.heading("max", text="max")
        tree.column("metric", width=180, anchor="w")
        tree.column("mean", width=150, anchor="w")
        tree.column("max", width=150, anchor="w")
        tree.pack(fill="both", expand=True, pady=(6, 0))

        for label, column in (
            ("CPU %", "cpu_mean"),
            ("RAM %", "ram_mean"),
            ("Network bps", "network_rate_bps"),
            ("Bytes in", "bytes_in"),
            ("Bytes out", "bytes_out"),
        ):
            values = pd.to_numeric(metrics.get(column, pd.Series(dtype=float)), errors="coerce").dropna()
            mean_value = float(values.mean()) if not values.empty else 0.0
            max_value = float(values.max()) if not values.empty else 0.0
            tree.insert("", "end", values=(label, _format_value(mean_value), _format_value(max_value)))

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
    raw_keyboard_slice: pd.DataFrame,
    raw_mouse_slice: pd.DataFrame,
    raw_system_slice: pd.DataFrame,
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
            system_metrics=raw_system_slice,
        )

    builder = GraphBuilder(node_level="app")
    nodes_df, edges_df, _ = builder.build_from_events(
        window_events,
        behavior_df=raw_behavior_slice,
        keyboard_df=raw_keyboard_slice,
        mouse_df=raw_mouse_slice,
    )
    if nodes_df.empty:
        return WindowGraphView(
            session_id=session_id,
            window_id=window_id,
            window_start=window_start,
            window_end=window_end,
            nodes=[],
            edges=[],
            window_features=window_features,
            system_metrics=raw_system_slice,
        )

    nodes: list[GraphNodeView] = []
    node_label_map: dict[str, str] = {}
    for _, row in nodes_df.iterrows():
        node_id = str(row.get("node_id", ""))
        node_kind = str(row.get("node_kind") or row.get("node_type") or "app")
        label = str(row.get("label") or node_id)
        node_label_map[node_id] = label
        features = _build_context_feature_payload(
            row=row.to_dict(),
            window_id=window_id,
            node_id=node_id,
            node_type=node_kind,
            node_feature_map=node_feature_map,
        )
        nodes.append(
            GraphNodeView(
                key=node_id,
                label=label,
                node_kind=node_kind,
                feature_node_type=node_kind,
                feature_node_id=node_id,
                features=features,
            )
        )

    edges: list[GraphEdgeView] = []
    for _, row in edges_df.iterrows():
        source = str(row.get("source", ""))
        target = str(row.get("target", ""))
        edge_type = str(row.get("edge_type", "transition"))
        source_label = node_label_map.get(source, source)
        target_label = node_label_map.get(target, target)
        edges.append(
            GraphEdgeView(
                edge_id=f"{edge_type}::{source}::{target}",
                source_key=source,
                target_key=target,
                source_label=source_label,
                target_label=target_label,
                edge_kind=edge_type,
                features={key: row.get(key) for key in edges_df.columns if key not in {"source", "target"}},
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
        system_metrics=raw_system_slice,
    )


def _build_context_feature_payload(
    row: dict[str, object],
    window_id: str,
    node_id: str,
    node_type: str,
    node_feature_map: dict[tuple[str, str, str], dict[str, object]],
) -> dict[str, object]:
    ordered_keys = [
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
    for key in NODE_FEATURE_COLUMNS:
        if key not in {"session_id", "window_id", "window_start", "window_end", "node_id", "node_type"}:
            if key not in ordered_keys:
                ordered_keys.append(key)
    exported = node_feature_map.get((window_id, node_id, node_type), {})
    payload = {key: row.get(key) for key in ordered_keys}
    payload.update({key: value for key, value in exported.items() if key in ordered_keys})
    return payload


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
    tab_nodes = [node for node in nodes if node.node_kind in {"tab", "page"}]
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


def _draw_edge_legend(canvas, width: int) -> None:
    x = max(10, width - 218)
    y = 10
    items = [
        (_EDGE_PERSIST, "persist"),
        ("#aa4c7a", "clip"),
        (_EDGE_FAST, "fast"),
        (_EDGE_MEDIUM, "medium"),
        (_EDGE_DEEP, "deep"),
    ]
    canvas.create_rectangle(x - 8, y - 5, width - 8, y + 20, fill="#ffffff", outline="#dbe4ef")
    cursor_x = x
    for color, label in items:
        canvas.create_line(cursor_x, y + 7, cursor_x + 15, y + 7, fill=color, width=3)
        canvas.create_text(cursor_x + 20, y + 7, text=label, anchor="w", fill=_MUTED, font=("Segoe UI", 7))
        cursor_x += 42


def _draw_system_pattern_chart(
    canvas,
    metrics: pd.DataFrame,
    window_start: float,
    window_end: float,
    width: int,
    height: int,
) -> None:
    canvas.delete("all")
    if metrics is None or metrics.empty or "timestamp" not in metrics.columns:
        canvas.create_text(
            width / 2,
            height / 2,
            text="No system metrics for this window",
            fill=_MUTED,
            font=("Segoe UI", 11),
        )
        return

    chart_specs = [
        ("CPU %", "cpu_mean", "#d95f59"),
        ("RAM %", "ram_mean", "#2f9e83"),
        ("Network bps", "network_rate_bps", "#5c73d8"),
    ]
    metrics = metrics.copy()
    metrics["timestamp"] = pd.to_numeric(metrics["timestamp"], errors="coerce")
    metrics = metrics.dropna(subset=["timestamp"]).sort_values("timestamp")
    if metrics.empty:
        return

    margin_left = 68
    margin_right = 18
    margin_top = 18
    band_gap = 18
    band_height = (height - (2 * margin_top) - (band_gap * (len(chart_specs) - 1))) / len(chart_specs)
    plot_width = width - margin_left - margin_right
    time_span = max(float(window_end) - float(window_start), 0.001)

    for index, (label, column, color) in enumerate(chart_specs):
        top = margin_top + index * (band_height + band_gap)
        bottom = top + band_height
        left = margin_left
        right = margin_left + plot_width

        canvas.create_rectangle(left, top, right, bottom, fill="#fbfdff", outline="#e1e9f3")
        canvas.create_text(12, top + 12, text=label, anchor="w", fill=_TEXT, font=("Segoe UI", 9, "bold"))

        values = pd.to_numeric(metrics.get(column, pd.Series(dtype=float)), errors="coerce")
        plot_df = pd.DataFrame({"timestamp": metrics["timestamp"], "value": values}).dropna()
        if plot_df.empty:
            canvas.create_text((left + right) / 2, (top + bottom) / 2, text="N/A", fill=_MUTED)
            continue

        y_min = float(plot_df["value"].min())
        y_max = float(plot_df["value"].max())
        if math.isclose(y_min, y_max):
            y_min = 0.0 if y_max >= 0 else y_max - 1.0
            y_max = y_max + 1.0

        points: list[float] = []
        for row in plot_df.itertuples(index=False):
            x = left + ((float(row.timestamp) - float(window_start)) / time_span) * plot_width
            y = bottom - ((float(row.value) - y_min) / max(y_max - y_min, 0.001)) * (band_height - 16) - 8
            x = max(left, min(right, x))
            y = max(top + 8, min(bottom - 8, y))
            points.extend([x, y])

        canvas.create_text(left - 8, top + 8, text=_format_value(y_max), anchor="e", fill=_MUTED, font=("Segoe UI", 8))
        canvas.create_text(left - 8, bottom - 8, text=_format_value(y_min), anchor="e", fill=_MUTED, font=("Segoe UI", 8))
        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=2, smooth=True)
        else:
            canvas.create_oval(points[0] - 3, points[1] - 3, points[0] + 3, points[1] + 3, fill=color, outline="")


def _edge_parallel_offsets(edges: list[GraphEdgeView]) -> dict[str, float]:
    groups: dict[tuple[str, str], list[GraphEdgeView]] = {}
    for edge in edges:
        if edge.source_key == edge.target_key:
            continue
        pair = tuple(sorted((edge.source_key, edge.target_key)))
        groups.setdefault(pair, []).append(edge)

    offsets: dict[str, float] = {}
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda edge: (
                edge.source_key,
                edge.target_key,
                -_edge_clipboard_activity(edge),
                edge.edge_kind,
                edge.edge_id,
            ),
        )
        if len(ordered) == 1:
            offsets[ordered[0].edge_id] = 0.0
            continue
        center = (len(ordered) - 1) / 2.0
        for index, edge in enumerate(ordered):
            offsets[edge.edge_id] = (index - center) * 22.0
    return offsets


def _offset_edge_points(x1: float, y1: float, x2: float, y2: float, offset: float) -> tuple[list[float], float, float]:
    if abs(offset) < 0.01:
        return [x1, y1, x2, y2], (x1 + x2) / 2.0, (y1 + y2) / 2.0

    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length <= 0:
        return [x1, y1, x2, y2], x1, y1

    normal_x = -dy / length
    normal_y = dx / length
    control_x = (x1 + x2) / 2.0 + normal_x * offset
    control_y = (y1 + y2) / 2.0 + normal_y * offset
    return [x1, y1, control_x, control_y, x2, y2], control_x, control_y


def _edge_style(edge: GraphEdgeView, window_duration_seconds: float) -> dict[str, object]:
    edge_type = str(edge.features.get("edge_type") or edge.edge_kind)
    count = max(1.0, _feature_float(edge.features.get("transition_count"), 1.0))
    total_ms = max(0.0, _feature_float(edge.features.get("total_duration"), 0.0))
    avg_ms = max(0.0, _feature_float(edge.features.get("avg_duration"), total_ms))
    window_ms = max(1.0, window_duration_seconds * 1_000.0)
    clipboard_count = _feature_float(edge.features.get("copy_paste_count"), 0.0)
    clipboard_activity = (
        clipboard_count
        + _feature_float(edge.features.get("copy_count"), 0.0)
        + _feature_float(edge.features.get("cut_count"), 0.0)
        + _feature_float(edge.features.get("paste_count"), 0.0)
    )

    if clipboard_activity > 0 or edge_type == "copy_paste":
        width = 2.2 + min(5.0, clipboard_activity * 1.2)
        return {
            "color": "#aa4c7a",
            "width": width,
            "dash": (3, 2) if edge_type == "copy_paste" else "",
            "duration_band": "clipboard_transfer",
            "style_reason": f"clipboard activity {int(clipboard_activity)}, transfers {int(clipboard_count)}",
        }

    if edge_type == "persistence":
        share = min(1.0, total_ms / window_ms)
        color = _blend_hex(_EDGE_MUTED, _EDGE_PERSIST, max(0.25, share))
        width = 1.5 + min(4.5, 4.0 * share + 0.35 * count)
        return {
            "color": color,
            "width": width,
            "dash": "",
            "duration_band": "persistence",
            "style_reason": f"persistence share {_format_value(share)}",
        }

    if avg_ms < 2_000:
        color = _EDGE_FAST
        band = "fast_switch"
    elif avg_ms < 8_000:
        color = _EDGE_MEDIUM
        band = "medium_switch"
    else:
        color = _EDGE_DEEP
        band = "deep_transition"

    width = 1.6 + min(4.8, 0.85 * count + math.log1p(total_ms / 1_000.0) * 0.55)
    return {
        "color": color,
        "width": width,
        "dash": (5, 3) if count <= 1 else "",
        "duration_band": band,
        "style_reason": f"avg {_format_value(avg_ms / 1000.0)}s, count {int(count)}",
    }


def _edge_color(edge_kind: str) -> str:
    if edge_kind == "persistence":
        return _EDGE_PERSIST
    if edge_kind == "transition":
        return _EDGE_MEDIUM
    return _APP_TAB_EDGE


def _edge_label(edge: GraphEdgeView) -> str:
    clipboard_count = _feature_float(edge.features.get("copy_paste_count"), 0.0)
    if clipboard_count > 0:
        return f"cp:{int(clipboard_count)}"
    clipboard_activity = _edge_clipboard_activity(edge)
    if clipboard_activity > 0:
        return f"clip:{int(clipboard_activity)}"
    count = _feature_float(edge.features.get("transition_count"), 0.0)
    avg_ms = _feature_float(edge.features.get("avg_duration"), 0.0)
    if count <= 0:
        return ""
    if avg_ms > 0:
        return f"{int(count)}|{avg_ms / 1000.0:.1f}s"
    return str(int(count))


def _edge_clipboard_activity(edge: GraphEdgeView) -> float:
    return (
        _feature_float(edge.features.get("copy_paste_count"), 0.0)
        + _feature_float(edge.features.get("copy_count"), 0.0)
        + _feature_float(edge.features.get("cut_count"), 0.0)
        + _feature_float(edge.features.get("paste_count"), 0.0)
    )


def _configure_edge_hit(canvas, edge_hit: dict[str, object], highlighted: bool, selected: bool = False) -> None:
    style = edge_hit.get("style", {})
    color = _EDGE_HOVER if highlighted else str(style.get("color", _EDGE_MUTED))
    width = float(style.get("width", 2.0)) + (2.0 if selected else 1.1 if highlighted else 0.0)
    for item_id in edge_hit.get("stroke_items", []):
        item_type = canvas.type(item_id)
        if item_type == "line":
            canvas.itemconfigure(item_id, fill=color, width=width)
        elif item_type == "arc":
            canvas.itemconfigure(item_id, outline=color, width=width)


def _configure_node_hit(canvas, node_hit: dict[str, object], highlighted: bool, selected: bool = False) -> None:
    circle_item = node_hit.get("circle_item")
    if circle_item is None:
        return
    outline = _EDGE_HOVER if highlighted else "#ffffff"
    width = 4 if selected else 3 if highlighted else 2
    canvas.itemconfigure(circle_item, outline=outline, width=width)


def _same_canvas_hit(left: object, right: object) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if left.get("kind") != right.get("kind"):
        return False
    if left.get("kind") == "edge":
        return left.get("edge_hit") is right.get("edge_hit")
    if left.get("kind") == "node":
        return left.get("node_hit") is right.get("node_hit")
    return False


def _feature_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result):
        return default
    return result


def _blend_hex(left: str, right: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))

    def _channels(color: str) -> tuple[int, int, int]:
        text = color.lstrip("#")
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)

    l_r, l_g, l_b = _channels(left)
    r_r, r_g, r_b = _channels(right)
    mixed = (
        round(l_r + (r_r - l_r) * amount),
        round(l_g + (r_g - l_g) * amount),
        round(l_b + (r_b - l_b) * amount),
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _window_footer_text(window_graph: WindowGraphView) -> str:
    summary = []
    if "switch_rate" in window_graph.window_features:
        summary.append(f"switch_rate={_format_value(window_graph.window_features.get('switch_rate'))}")
    if "focus_duration_ratio" in window_graph.window_features:
        summary.append(f"focus_ratio={_format_value(window_graph.window_features.get('focus_duration_ratio'))}")
    clipboard_total = sum(
        _feature_float(edge.features.get("copy_paste_count"), 0.0)
        + _feature_float(edge.features.get("copy_count"), 0.0)
        + _feature_float(edge.features.get("cut_count"), 0.0)
        + _feature_float(edge.features.get("paste_count"), 0.0)
        for edge in window_graph.edges
    )
    if clipboard_total > 0:
        summary.append(f"clip={_format_value(clipboard_total)}")
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


def _distance_to_polyline(px: float, py: float, points: list[float] | tuple[float, ...]) -> float:
    if len(points) < 4:
        return float("inf")
    distances = []
    for index in range(0, len(points) - 2, 2):
        distances.append(
            _distance_to_segment(
                px,
                py,
                float(points[index]),
                float(points[index + 1]),
                float(points[index + 2]),
                float(points[index + 3]),
            )
        )
    return min(distances) if distances else float("inf")


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
