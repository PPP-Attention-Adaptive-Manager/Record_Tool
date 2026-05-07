"""
End-to-end feature engineering pipeline.

Pipeline stages
---------------
1. Load raw CSVs from  data/<session_id>/raw/
2. Infer session span (t_start, t_end) from all streams
3. Export labels into  data/<session_id>/labels/
4. For each WindowConfig:
      a. Generate windows
      b. Extract modality-specific features per window
      c. Normalize each modality table independently
      d. Write:
            features/behavior/features_behavior_<label>.csv
            features/keyboard/features_keyboard_<label>.csv
            features/mouse/features_mouse_<label>.csv
            features/system/features_system_<label>.csv
      e. Compute per-node window features
      f. Write  node_features/node_features_<label>.csv
5. Build the session temporal graph from behavior events
6. Build per-window temporal graph slices
7. Cluster the primary window set using an in-memory merge of modality tables
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from .clustering import CognitiveStateClusterer
from .features import FeatureConfig, FeatureExtractor, MODALITY_FEATURE_COLUMNS, Normalizer
from .graph_builder import GraphBuilder, NODE_LEVEL
from .node_features import NodeFeatureBuilder
from .windowing import DEFAULT_WINDOW_CONFIGS, WindowConfig, WindowEngine

LOGGER = logging.getLogger(__name__)

_STREAM_NAMES = [
    "behavior",
    "keyboard",
    "mouse",
    "notification",
    "system_metrics",
    "dual_task",
]

_NASA_TLX_COLUMNS = [
    "timestamp",
    "session_id",
    "device_id",
    "mental_demand",
    "physical_demand",
    "temporal_demand",
    "performance",
    "effort",
    "frustration",
    "stress_self_report",
    "valence",
    "arousal",
]

_DUAL_TASK_LABEL_COLUMNS = [
    "timestamp",
    "session_id",
    "device_id",
    "reaction_time_ms",
    "success",
    "miss",
    "error",
    "app_name",
    "scheduled_delay_seconds",
    "probe_left_px",
    "probe_top_px",
]


@dataclass
class PipelineConfig:
    """Full pipeline configuration."""

    window_configs: list[WindowConfig] = field(default_factory=lambda: list(DEFAULT_WINDOW_CONFIGS))
    """Window schemes to generate. Defaults to 5 s / 30 s / 120 s tumbling."""

    primary_window_label: str = "30s"
    """Label of the window config used for clustering."""

    graph_node_level: Literal["app", "domain", "url"] = "app"
    """Node granularity for session-level temporal graphs."""

    normalization: Literal["minmax", "zscore"] = "minmax"

    n_clusters: int = 4
    clustering_algorithm: Literal["kmeans", "dbscan"] = "kmeans"

    feature_config: FeatureConfig = field(default_factory=FeatureConfig)

    skip_graph: bool = False
    """Set True to skip graph construction and clustering."""


def _load_streams(raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Load and sort supported raw stream CSVs from *raw_dir*."""
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


def _read_optional_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        LOGGER.warning("Could not read %s: %s", path, exc)
        return pd.DataFrame(columns=columns)
    for col in columns:
        if col not in df.columns:
            df[col] = pd.Series(dtype=object)
    return df[columns]


def _write_table(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy() if df is not None else pd.DataFrame(columns=columns)
    for col in columns:
        if col not in out.columns:
            out[col] = pd.Series(dtype=object)
    out[columns].to_csv(path, index=False)


def _export_labels(session_dir: Path) -> dict[str, Path]:
    raw_dir = session_dir / "raw"
    labels_dir = session_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    nasa_tlx_df = _read_optional_csv(raw_dir / "labels.csv", _NASA_TLX_COLUMNS)
    dual_task_df = _read_optional_csv(raw_dir / "dual_task.csv", _DUAL_TASK_LABEL_COLUMNS)

    nasa_tlx_path = labels_dir / "nasa_tlx.csv"
    dual_task_path = labels_dir / "dual_task.csv"
    _write_table(nasa_tlx_df, nasa_tlx_path, _NASA_TLX_COLUMNS)
    _write_table(dual_task_df, dual_task_path, _DUAL_TASK_LABEL_COLUMNS)

    LOGGER.info("Labels: wrote nasa_tlx.csv (%d rows) and dual_task.csv (%d rows)", len(nasa_tlx_df), len(dual_task_df))
    return {
        "labels_nasa_tlx": nasa_tlx_path,
        "labels_dual_task": dual_task_path,
    }


def _safe_unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except PermissionError as exc:
        LOGGER.warning("Could not remove legacy file %s: %s", path, exc)
        return False
    except Exception as exc:
        LOGGER.warning("Could not remove legacy file %s: %s", path, exc)
        return False


def _safe_rmtree(path: Path) -> bool:
    def _retry_remove(func, value, _excinfo):
        try:
            Path(value).chmod(0o666)
            func(value)
        except Exception:
            raise

    try:
        shutil.rmtree(path, onerror=_retry_remove)
        return True
    except FileNotFoundError:
        return False
    except PermissionError as exc:
        LOGGER.warning("Could not remove legacy directory %s: %s", path, exc)
        return False
    except Exception as exc:
        LOGGER.warning("Could not remove legacy directory %s: %s", path, exc)
        return False


def _remove_legacy_graph_artifacts(graph_dir: Path) -> None:
    for name in ("edge_list.csv", "graph.json", "node_features.csv"):
        path = graph_dir / name
        if path.exists() and _safe_unlink(path):
            LOGGER.info("Removed legacy graph artifact: %s", path.name)


def _remove_legacy_feature_artifacts(session_dir: Path) -> None:
    features_dir = session_dir / "features"
    if features_dir.exists():
        for path in features_dir.glob("features_*.csv"):
            if _safe_unlink(path):
                LOGGER.info("Removed legacy feature artifact: %s", path.name)
        for legacy_dir in ("dual_task", "notification", "system_metrics"):
            path = features_dir / legacy_dir
            if path.exists() and _safe_rmtree(path):
                LOGGER.info("Removed legacy feature directory: %s", path.name)


def _modality_output_path(features_dir: Path, modality: str, window_label: str) -> Path:
    return features_dir / modality / f"features_{modality}_{window_label}.csv"


class FeaturePipeline:
    """
    Orchestrates the full RAW -> FEATURES -> GRAPH pipeline for one session.
    """

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config = config or PipelineConfig()
        self._engine = WindowEngine()
        self._extractor = FeatureExtractor(self.config.feature_config)
        self._node_builder = NodeFeatureBuilder()

    def run(
        self,
        session_id: str,
        data_dir: Optional[Path] = None,
    ) -> dict[str, Path]:
        t0 = time.perf_counter()

        data_dir = data_dir or self._default_data_dir()
        session_dir = data_dir / session_id

        raw_dir = session_dir / "raw"
        features_dir = session_dir / "features"
        node_features_dir = session_dir / "node_features"
        graph_dir = session_dir / "graph"

        features_dir.mkdir(parents=True, exist_ok=True)
        node_features_dir.mkdir(parents=True, exist_ok=True)

        LOGGER.info("=" * 60)
        LOGGER.info("Pipeline start  session=%s", session_id)
        LOGGER.info("  raw_dir     : %s", raw_dir)

        _remove_legacy_feature_artifacts(session_dir)
        streams = _load_streams(raw_dir)
        if not streams:
            raise FileNotFoundError(f"No stream CSVs found in {raw_dir}")

        outputs: dict[str, Path] = {}
        outputs.update(_export_labels(session_dir))

        t_start, t_end = self._engine.session_span(*streams.values())
        duration_min = (t_end - t_start) / 60
        LOGGER.info("  session span : %.1f min  [%.3f -> %.3f]", duration_min, t_start, t_end)

        primary_features: Optional[pd.DataFrame] = None
        windows_by_label: dict[str, pd.DataFrame] = {}

        for wc in self.config.window_configs:
            LOGGER.info("Window config: %s", wc.label)
            windows = self._engine.generate(t_start, t_end, wc)
            if windows.empty:
                LOGGER.warning("  No windows generated for config %s", wc.label)
                continue
            windows_by_label[wc.label] = windows

            modality_raw = self._extractor.extract_modalities(streams, windows, session_id)
            modality_norm: dict[str, pd.DataFrame] = {}
            for modality, raw_df in modality_raw.items():
                norm_df = Normalizer(self.config.normalization).fit_transform(raw_df) if not raw_df.empty else raw_df
                modality_norm[modality] = norm_df

                out_path = _modality_output_path(features_dir, modality, wc.label)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                norm_df.to_csv(out_path, index=False)
                outputs[f"features_{modality}_{wc.label}"] = out_path
                LOGGER.info(
                    "  Wrote %s: %d windows x %d cols",
                    out_path.name,
                    len(norm_df),
                    len(norm_df.columns),
                )

            combined_features = self._extractor.combine_modalities(modality_norm)
            if wc.label == self.config.primary_window_label:
                primary_features = combined_features

            node_features_df = self._node_builder.build(streams, windows, session_id)
            node_path = node_features_dir / f"node_features_{wc.label}.csv"
            self._node_builder.export(node_features_df, node_path)
            outputs[f"node_features_{wc.label}"] = node_path

        if not self.config.skip_graph:
            graph_dir.mkdir(parents=True, exist_ok=True)
            _remove_legacy_graph_artifacts(graph_dir)

            builder = GraphBuilder(node_level=self.config.graph_node_level)
            behavior_df = streams.get("behavior", pd.DataFrame())
            cleaned_events, nodes_df, edges_df, temporal_edges_df = builder.build(behavior_df)
            if cleaned_events.empty:
                LOGGER.warning("Graph: no valid behavior events found after cleaning/filtering.")

            builder.export(nodes_df, edges_df, temporal_edges_df, graph_dir)
            outputs["nodes"] = graph_dir / "nodes.csv"
            outputs["edges"] = graph_dir / "edges.csv"
            outputs["temporal_edges"] = graph_dir / "temporal_edges.csv"

            windows_root = graph_dir / "windows"
            for wc in self.config.window_configs:
                windows = windows_by_label.get(wc.label)
                if windows is None:
                    continue
                window_nodes_df, window_edges_df, window_temporal_df = builder.build_windowed(
                    cleaned_events,
                    windows,
                )
                out_dir = windows_root / wc.label
                builder.export_windowed(window_nodes_df, window_edges_df, window_temporal_df, out_dir)
                outputs[f"windowed_graph_{wc.label}"] = out_dir

            if primary_features is None or primary_features.empty:
                for wc in self.config.window_configs:
                    behavior_path = _modality_output_path(features_dir, "behavior", wc.label)
                    if behavior_path.exists():
                        behavior_df = pd.read_csv(behavior_path)
                        modality_frames = {
                            modality: pd.read_csv(_modality_output_path(features_dir, modality, wc.label))
                            for modality in MODALITY_FEATURE_COLUMNS
                            if _modality_output_path(features_dir, modality, wc.label).exists()
                        }
                        primary_features = self._extractor.combine_modalities(modality_frames)
                        LOGGER.warning(
                            "Primary window '%s' missing; using '%s' for clustering.",
                            self.config.primary_window_label,
                            wc.label,
                        )
                        break

            if primary_features is not None and not primary_features.empty:
                try:
                    clusterer = CognitiveStateClusterer(
                        n_clusters=self.config.n_clusters,
                        algorithm=self.config.clustering_algorithm,
                    )
                    cluster_labels = clusterer.fit_predict(primary_features)
                    state_labels = clusterer.label_states(primary_features, cluster_labels)
                    comm_path = clusterer.export(primary_features, cluster_labels, state_labels, graph_dir)
                    outputs["communities"] = comm_path
                except Exception as exc:
                    LOGGER.warning("Skipping clustering because it failed: %s", exc)
            else:
                LOGGER.warning("Skipping clustering: no primary feature data.")

        elapsed = time.perf_counter() - t0
        LOGGER.info("Pipeline complete in %.2f s  outputs: %s", elapsed, list(outputs.keys()))
        return outputs

    @staticmethod
    def _default_data_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "data"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m feature_engineering.pipeline",
        description="Run the feature engineering pipeline for a session.",
    )
    parser.add_argument("session_id", help="Session folder name (e.g. session_20240427_001)")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to the data directory (default: cognitive_system/data/)",
    )
    parser.add_argument(
        "--primary-window",
        default="30s",
        choices=["5s", "30s", "120s"],
        dest="primary_window",
        help="Window size used for clustering (default: 30s)",
    )
    parser.add_argument(
        "--graph-node-level",
        default="app",
        choices=NODE_LEVEL,
        dest="graph_node_level",
        help="Temporal graph node granularity: app, domain, or url (default: app)",
    )
    parser.add_argument(
        "--normalization",
        default="minmax",
        choices=["minmax", "zscore"],
    )
    parser.add_argument(
        "--clustering",
        default="kmeans",
        choices=["kmeans", "dbscan"],
    )
    parser.add_argument("--n-clusters", type=int, default=4)
    parser.add_argument(
        "--skip-graph",
        action="store_true",
        help="Compute features only; skip graph construction and clustering.",
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
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    config = PipelineConfig(
        primary_window_label=args.primary_window,
        graph_node_level=args.graph_node_level,
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
