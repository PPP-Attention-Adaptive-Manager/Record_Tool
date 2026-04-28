"""
Graph builder for temporal behavioral windows.

Graph schema
------------
Nodes  : one per window; attributes = normalized feature vector + cluster label.
Edges  : temporal (consecutive windows) + optional feature-similarity edges.

Output files  (written to  data/<session_id>/graph/)
------------
edge_list.csv    — source, target, edge_type, weight
node_features.csv— window_id, cluster, <feature cols>
graph.json       — node-link format compatible with networkx / PyTorch Geometric

Design
------
- Temporal edges are always generated (the backbone of the time-series graph).
- Similarity edges are optional; added when cosine similarity ≥ threshold and
  the two windows are not already consecutive.
- sklearn is required only for similarity edges; falls back gracefully.
"""
from __future__ import annotations

import json
import logging
from datetime import timezone, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

_META_COLS = {"window_id", "session_id", "window_start", "window_end", "cluster", "cognitive_state"}


class GraphBuilder:
    """
    Builds a directed temporal graph from a windowed feature DataFrame.

    Parameters
    ----------
    similarity_threshold : float
        Minimum cosine similarity to create a similarity edge (0–1).
        Set to 1.0 to disable similarity edges entirely.
    add_similarity_edges : bool
        When False, only temporal edges are created regardless of threshold.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.90,
        add_similarity_edges: bool = True,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.add_similarity_edges = add_similarity_edges

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self, features_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Build node and edge DataFrames from a windowed feature table.

        Parameters
        ----------
        features_df : output of FeatureExtractor.extract() (already normalized).
                      May optionally include a 'cluster' / 'cognitive_state' column.

        Returns
        -------
        nodes_df : window_id + all feature/meta columns (one row per node).
        edges_df : source, target, edge_type, weight.
        """
        if features_df.empty:
            return pd.DataFrame(), pd.DataFrame()

        nodes_df = features_df.copy().reset_index(drop=True)
        feature_cols = [c for c in nodes_df.columns if c not in _META_COLS]

        # ── Temporal edges ────────────────────────────────────────────────────
        ids = nodes_df["window_id"].tolist()
        temporal_edges = pd.DataFrame(
            {
                "source":    ids[:-1],
                "target":    ids[1:],
                "edge_type": "temporal",
                "weight":    1.0,
            }
        )

        # ── Similarity edges ──────────────────────────────────────────────────
        similarity_edges = pd.DataFrame(columns=["source", "target", "edge_type", "weight"])
        if self.add_similarity_edges and self.similarity_threshold < 1.0 and len(nodes_df) > 1:
            similarity_edges = self._build_similarity_edges(nodes_df, ids, feature_cols)

        edges_df = pd.concat([temporal_edges, similarity_edges], ignore_index=True)
        return nodes_df, edges_df

    def export(
        self,
        nodes_df: pd.DataFrame,
        edges_df: pd.DataFrame,
        out_dir: Path,
        session_id: str,
        window_label: str = "30s",
    ) -> None:
        """
        Write edge_list.csv, node_features.csv, and graph.json to *out_dir*.
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        # edge_list.csv
        edges_df.to_csv(out_dir / "edge_list.csv", index=False)
        LOGGER.info("Graph: wrote %d edges to edge_list.csv", len(edges_df))

        # node_features.csv — window_id + cluster + feature values
        node_out_cols = ["window_id"]
        for c in ("cluster", "cognitive_state"):
            if c in nodes_df.columns:
                node_out_cols.append(c)
        feature_cols = [c for c in nodes_df.columns if c not in _META_COLS]
        node_out_cols += feature_cols
        nodes_df[node_out_cols].to_csv(out_dir / "node_features.csv", index=False)
        LOGGER.info("Graph: wrote %d nodes to node_features.csv", len(nodes_df))

        # graph.json — node-link format
        graph_json = self._to_json(nodes_df, edges_df, session_id, window_label)
        (out_dir / "graph.json").write_text(
            json.dumps(graph_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        LOGGER.info("Graph: wrote graph.json")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_similarity_edges(
        self,
        nodes_df: pd.DataFrame,
        ids: list[str],
        feature_cols: list[str],
    ) -> pd.DataFrame:
        try:
            from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import]
        except ImportError:
            LOGGER.warning("sklearn not available — similarity edges skipped.")
            return pd.DataFrame(columns=["source", "target", "edge_type", "weight"])

        X = nodes_df[feature_cols].fillna(0).values.astype(float)
        sim_matrix = cosine_similarity(X)
        n = len(ids)

        # Build consecutive-pair set to avoid duplicating temporal edges.
        temporal_pairs = {(i, i + 1) for i in range(n - 1)}

        rows = []
        # Only upper triangle (i < j) to avoid duplicates.
        for i in range(n):
            for j in range(i + 2, n):  # skip i+1 (already temporal edge)
                sim = float(sim_matrix[i, j])
                if sim >= self.similarity_threshold and (i, j) not in temporal_pairs:
                    rows.append(
                        {
                            "source":    ids[i],
                            "target":    ids[j],
                            "edge_type": "similarity",
                            "weight":    round(sim, 6),
                        }
                    )

        if rows:
            LOGGER.info("Graph: added %d similarity edges (threshold=%.2f)", len(rows), self.similarity_threshold)
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["source", "target", "edge_type", "weight"]
        )

    @staticmethod
    def _to_json(
        nodes_df: pd.DataFrame,
        edges_df: pd.DataFrame,
        session_id: str,
        window_label: str,
    ) -> dict:
        """Serialize graph to a node-link dict (networkx-compatible)."""
        feature_cols = [c for c in nodes_df.columns if c not in _META_COLS]

        nodes = []
        for _, row in nodes_df.iterrows():
            node: dict = {"id": row["window_id"]}
            for c in ("cluster", "cognitive_state", "window_start", "window_end"):
                if c in row.index:
                    node[c] = row[c]
            node["features"] = {c: round(float(row[c]), 6) for c in feature_cols}
            nodes.append(node)

        links = [
            {
                "source":    r["source"],
                "target":    r["target"],
                "edge_type": r["edge_type"],
                "weight":    float(r["weight"]),
            }
            for _, r in edges_df.iterrows()
        ]

        return {
            "directed":    True,
            "multigraph":  False,
            "graph": {
                "session_id":   session_id,
                "window_size":  window_label,
                "n_nodes":      len(nodes),
                "n_edges":      len(links),
                "created_at":   datetime.now(tz=timezone.utc).isoformat(),
            },
            "nodes": nodes,
            "links": links,
        }
