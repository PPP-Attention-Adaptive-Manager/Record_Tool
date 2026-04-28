"""
End-to-end feature engineering pipeline.

Pipeline stages
---------------
1. Load raw CSVs from  data/<session_id>/raw/
2. Infer session span (t_start, t_end) from all streams
3. For each WindowConfig:
      a. Generate windows
      b. Extract features per window (all streams)
      c. Normalize (min-max or z-score)
      d. Write  data/<session_id>/features/features_<label>.csv
4. On the primary window (default: 30 s):
      a. Build directed temporal + similarity graph
      b. Write  data/<session_id>/graph/edge_list.csv
                                         node_features.csv
                                         graph.json
      c. Cluster windows → cognitive states
      d. Write  data/<session_id>/graph/communities.csv

CLI usage
---------
python -m feature_engineering.pipeline <session_id> [--data-dir PATH] [--primary-window 30s]

"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from .clustering import CognitiveStateClusterer
from .features import FeatureConfig, FeatureExtractor, Normalizer
from .graph import GraphBuilder
from .windowing import (
    DEFAULT_WINDOW_CONFIGS,
    MESO_30S,
    WindowConfig,
    WindowEngine,
)

LOGGER = logging.getLogger(__name__)

_STREAM_NAMES = [
    "behavior",
    "keyboard",
    "mouse",
    "notification",
    "system_metrics",
    "dual_task",
]


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """Full pipeline configuration."""

    window_configs: list[WindowConfig] = field(default_factory=lambda: list(DEFAULT_WINDOW_CONFIGS))
    """Window schemes to generate.  Defaults to 5 s / 30 s / 120 s tumbling."""

    primary_window_label: str = "30s"
    """Label of the window config used for graph construction and clustering."""

    normalization: Literal["minmax", "zscore"] = "minmax"

    similarity_threshold: float = 0.90
    """Cosine similarity threshold for adding graph similarity edges."""

    add_similarity_edges: bool = True

    n_clusters: int = 4
    clustering_algorithm: Literal["kmeans", "dbscan"] = "kmeans"

    feature_config: FeatureConfig = field(default_factory=FeatureConfig)

    skip_graph: bool = False
    """Set True to skip graph + clustering (features CSV only)."""


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _load_streams(raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Load and sort all stream CSVs from *raw_dir*."""
    streams: dict[str, pd.DataFrame] = {}
    for name in _STREAM_NAMES:
        path = raw_dir / f"{name}.csv"
        if not path.exists():
            LOGGER.debug("Stream not found, skipping: %s", path)
            continue
        try:
            df = pd.read_csv(path, low_memory=False)
            if df.empty or "timestamp" not in df.columns:
                LOGGER.warning("Empty or missing timestamp column in %s", path)
                continue
            df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
            df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            streams[name] = df
            LOGGER.info("Loaded %-16s %6d rows", name, len(df))
        except Exception as exc:
            LOGGER.warning("Could not read %s: %s", path, exc)
    return streams


# ── Pipeline ──────────────────────────────────────────────────────────────────

class FeaturePipeline:
    """
    Orchestrates the full RAW → FEATURES → GRAPH pipeline for one session.

    Usage
    -----
    >>> pipeline = FeaturePipeline()
    >>> pipeline.run("session_20240427_001")
    """

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config   = config or PipelineConfig()
        self._engine  = WindowEngine()
        self._extractor = FeatureExtractor(self.config.feature_config)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        session_id: str,
        data_dir: Optional[Path] = None,
    ) -> dict[str, Path]:
        """
        Run the full pipeline for *session_id*.

        Parameters
        ----------
        session_id : folder name under data_dir.
        data_dir   : defaults to  <repo_root>/data  (same as system_agent).

        Returns
        -------
        dict mapping output name → Path (for programmatic use / testing).
        """
        t0 = time.perf_counter()

        data_dir = data_dir or self._default_data_dir()
        session_dir = data_dir / session_id

        raw_dir      = session_dir / "raw"
        features_dir = session_dir / "features"
        graph_dir    = session_dir / "graph"
        features_dir.mkdir(parents=True, exist_ok=True)

        LOGGER.info("=" * 60)
        LOGGER.info("Pipeline start  session=%s", session_id)
        LOGGER.info("  raw_dir     : %s", raw_dir)

        # ── 1. Load streams ───────────────────────────────────────────────────
        streams = _load_streams(raw_dir)
        if not streams:
            raise FileNotFoundError(f"No stream CSVs found in {raw_dir}")

        # ── 2. Infer session span ─────────────────────────────────────────────
        t_start, t_end = self._engine.session_span(*streams.values())
        duration_min   = (t_end - t_start) / 60
        LOGGER.info("  session span : %.1f min  [%.3f -> %.3f]", duration_min, t_start, t_end)

        outputs: dict[str, Path] = {}
        primary_features: Optional[pd.DataFrame] = None

        # ── 3. Feature extraction per window config ───────────────────────────
        for wc in self.config.window_configs:
            LOGGER.info("Window config: %s", wc.label)

            windows = self._engine.generate(t_start, t_end, wc)
            if windows.empty:
                LOGGER.warning("  No windows generated for config %s", wc.label)
                continue

            features_raw = self._extractor.extract(streams, windows, session_id)
            if features_raw.empty:
                LOGGER.warning("  No features extracted for config %s", wc.label)
                continue

            normalizer   = Normalizer(self.config.normalization)
            features_norm = normalizer.fit_transform(features_raw)

            out_path = features_dir / f"features_{wc.label}.csv"
            features_norm.to_csv(out_path, index=False)
            outputs[f"features_{wc.label}"] = out_path
            LOGGER.info(
                "  Wrote %d windows x %d features -> %s",
                len(features_norm),
                len(features_norm.columns),
                out_path.name,
            )

            if wc.label == self.config.primary_window_label:
                primary_features = features_norm

        # ── 4. Graph + clustering (primary window only) ───────────────────────
        if not self.config.skip_graph:
            if primary_features is None:
                # Fall back to the first available feature set.
                for wc in self.config.window_configs:
                    p = features_dir / f"features_{wc.label}.csv"
                    if p.exists():
                        primary_features = pd.read_csv(p)
                        LOGGER.warning(
                            "Primary window '%s' missing; using '%s' for graph.",
                            self.config.primary_window_label, wc.label,
                        )
                        break

            if primary_features is not None and not primary_features.empty:
                graph_dir.mkdir(parents=True, exist_ok=True)

                # Graph construction.
                builder = GraphBuilder(
                    similarity_threshold=self.config.similarity_threshold,
                    add_similarity_edges=self.config.add_similarity_edges,
                )
                nodes_df, edges_df = builder.build(primary_features)

                # Clustering.
                clusterer = CognitiveStateClusterer(
                    n_clusters=self.config.n_clusters,
                    algorithm=self.config.clustering_algorithm,
                )
                cluster_labels = clusterer.fit_predict(primary_features)
                state_labels   = clusterer.label_states(primary_features, cluster_labels)

                # Attach labels to node DataFrame before export.
                nodes_df = nodes_df.copy()
                nodes_df["cluster_id"]     = cluster_labels.values
                nodes_df["cognitive_state"] = state_labels.values

                builder.export(nodes_df, edges_df, graph_dir, session_id,
                               window_label=self.config.primary_window_label)

                comm_path = clusterer.export(primary_features, cluster_labels, state_labels, graph_dir)
                outputs["communities"]    = comm_path
                outputs["edge_list"]      = graph_dir / "edge_list.csv"
                outputs["node_features"]  = graph_dir / "node_features.csv"
                outputs["graph_json"]     = graph_dir / "graph.json"
            else:
                LOGGER.warning("Skipping graph/clustering: no primary feature data.")

        elapsed = time.perf_counter() - t0
        LOGGER.info("Pipeline complete in %.2f s  outputs: %s", elapsed, list(outputs.keys()))
        return outputs

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _default_data_dir() -> Path:
        """Mirror the same default data directory as system_agent/config.py."""
        return Path(__file__).resolve().parent.parent / "data"


# ── CLI entry point ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m feature_engineering.pipeline",
        description="Run the feature engineering pipeline for a session.",
    )
    p.add_argument("session_id", help="Session folder name (e.g. session_20240427_001)")
    p.add_argument(
        "--data-dir", default=None,
        help="Path to the data directory (default: cognitive_system/data/)",
    )
    p.add_argument(
        "--primary-window", default="30s", choices=["5s", "30s", "120s"],
        dest="primary_window",
        help="Window size used for graph + clustering (default: 30s)",
    )
    p.add_argument(
        "--normalization", default="minmax", choices=["minmax", "zscore"],
    )
    p.add_argument(
        "--clustering", default="kmeans", choices=["kmeans", "dbscan"],
    )
    p.add_argument(
        "--n-clusters", type=int, default=4,
    )
    p.add_argument(
        "--skip-graph", action="store_true",
        help="Compute features only; skip graph construction and clustering.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    config = PipelineConfig(
        primary_window_label=args.primary_window,
        normalization=args.normalization,
        clustering_algorithm=args.clustering,
        n_clusters=args.n_clusters,
        skip_graph=args.skip_graph,
    )
    data_dir = Path(args.data_dir) if args.data_dir else None

    try:
        FeaturePipeline(config).run(args.session_id, data_dir)
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
