"""
Unsupervised cognitive-state segmentation of behavioral windows.

Clusters windows into four cognitive states using K-Means or DBSCAN,
then maps each cluster to a human-readable cognitive label via a
heuristic that ranks centroids on activity, load, and focus scores.

Cognitive state labels
----------------------
focused     — high keystroke/click activity, high focus_duration_ratio
overloaded  — high CPU + notification load alongside high activity
distracted  — medium activity, low focus, frequent context switches
idle        — near-zero activity across all streams

Output
------
data/<session_id>/graph/communities.csv
  window_id, cluster_id, cognitive_state
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

_META_COLS = {"window_id", "session_id", "window_start", "window_end", "cluster", "cognitive_state"}
_COGNITIVE_STATES = ("focused", "overloaded", "distracted", "idle")

# ── Activity/load feature groups used for centroid-based state mapping ────────
_ACTIVITY_FEATURES   = ["keystroke_rate", "click_rate", "movement_speed_mean"]
_LOAD_FEATURES       = ["cpu_mean", "notification_rate", "switch_rate"]
_FOCUS_FEATURES      = ["focus_duration_ratio"]
_ANTI_FOCUS_FEATURES = ["idle_ratio", "switch_rate"]


class CognitiveStateClusterer:
    """
    Fits K-Means (or DBSCAN) on a normalized feature DataFrame and
    returns cluster assignments with cognitive state labels.

    Parameters
    ----------
    n_clusters : int
        Number of clusters for K-Means. Ignored for DBSCAN.
    algorithm : "kmeans" | "dbscan"
    dbscan_eps : float
        DBSCAN neighbourhood radius (feature space, normalized 0-1).
    dbscan_min_samples : int
        DBSCAN minimum cluster size.
    random_state : int
        Reproducibility seed for K-Means.
    """

    def __init__(
        self,
        n_clusters: int = 4,
        algorithm: Literal["kmeans", "dbscan"] = "kmeans",
        dbscan_eps: float = 0.3,
        dbscan_min_samples: int = 5,
        random_state: int = 42,
    ) -> None:
        self.n_clusters       = n_clusters
        self.algorithm        = algorithm
        self.dbscan_eps       = dbscan_eps
        self.dbscan_min_samples = dbscan_min_samples
        self.random_state     = random_state
        self._model           = None
        self._centroids: Optional[np.ndarray] = None
        self._feature_cols: list[str] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def fit_predict(self, features_df: pd.DataFrame) -> pd.Series:
        """
        Cluster windows and return an integer cluster label per row.

        Parameters
        ----------
        features_df : normalized feature DataFrame (output of Normalizer.fit_transform).

        Returns
        -------
        pd.Series of int cluster IDs, indexed like features_df.
        """
        try:
            from sklearn.cluster import KMeans, DBSCAN  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "scikit-learn is required for clustering. "
                "Install with: pip install scikit-learn"
            ) from exc

        self._feature_cols = [c for c in features_df.columns if c not in _META_COLS]
        X = features_df[self._feature_cols].fillna(0).values.astype(float)

        if self.algorithm == "kmeans":
            k = min(self.n_clusters, len(X))
            model = KMeans(n_clusters=k, random_state=self.random_state, n_init="auto")
            labels = model.fit_predict(X)
            self._centroids = model.cluster_centers_
            self._model = model
        else:
            model = DBSCAN(eps=self.dbscan_eps, min_samples=self.dbscan_min_samples)
            labels = model.fit_predict(X)
            # Compute pseudo-centroids as cluster means.
            unique_labels = sorted(set(labels) - {-1})
            if unique_labels:
                self._centroids = np.stack(
                    [X[labels == lbl].mean(axis=0) for lbl in unique_labels]
                )
            self._model = model

        LOGGER.info(
            "Clustering: %s produced %d clusters from %d windows",
            self.algorithm, len(set(labels) - {-1}), len(X),
        )
        return pd.Series(labels, index=features_df.index, name="cluster_id")

    def label_states(
        self,
        features_df: pd.DataFrame,
        cluster_labels: pd.Series,
    ) -> pd.Series:
        """
        Map integer cluster IDs to cognitive state strings.

        Uses a heuristic ranking of cluster centroids on activity,
        cognitive load, and focus scores.

        Returns
        -------
        pd.Series of cognitive state strings, indexed like cluster_labels.
        """
        if self._centroids is None or not self._feature_cols:
            return cluster_labels.map(lambda x: "unknown").rename("cognitive_state")

        unique_clusters = sorted(int(c) for c in cluster_labels.unique() if c >= 0)
        if not unique_clusters:
            return cluster_labels.map(str).rename("cognitive_state")

        col_idx = {col: i for i, col in enumerate(self._feature_cols)}

        def _centroid_score(cluster_id: int, feature_list: list[str]) -> float:
            c = self._centroids[cluster_id]
            indices = [col_idx[f] for f in feature_list if f in col_idx]
            return float(np.mean(c[indices])) if indices else 0.0

        # Rank each cluster on three independent axes.
        activity_scores = {c: _centroid_score(c, _ACTIVITY_FEATURES)   for c in unique_clusters}
        load_scores     = {c: _centroid_score(c, _LOAD_FEATURES)        for c in unique_clusters}
        focus_scores    = {c: _centroid_score(c, _FOCUS_FEATURES) -
                              _centroid_score(c, _ANTI_FOCUS_FEATURES)  for c in unique_clusters}

        state_map: dict[int, str] = {}
        remaining = set(unique_clusters)

        # 1. Idle = lowest activity.
        idle_c = min(remaining, key=lambda c: activity_scores[c])
        state_map[idle_c] = "idle"
        remaining.discard(idle_c)

        if not remaining:
            state_map = {c: state_map.get(c, "focused") for c in unique_clusters}
        else:
            # 2. Focused = highest focus score among remaining.
            focused_c = max(remaining, key=lambda c: focus_scores[c])
            state_map[focused_c] = "focused"
            remaining.discard(focused_c)

            if remaining:
                # 3. Overloaded = highest load score among remaining.
                overloaded_c = max(remaining, key=lambda c: load_scores[c])
                state_map[overloaded_c] = "overloaded"
                remaining.discard(overloaded_c)

            # 4. Everything else = distracted.
            for c in remaining:
                state_map[c] = "distracted"

        # DBSCAN noise points (-1) → "unknown"
        state_map[-1] = "unknown"

        return cluster_labels.map(state_map).rename("cognitive_state")

    def export(
        self,
        features_df: pd.DataFrame,
        cluster_labels: pd.Series,
        state_labels: pd.Series,
        out_dir: Path,
    ) -> Path:
        """
        Write communities.csv to *out_dir*.

        Columns: window_id, cluster_id, cognitive_state
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        out = pd.DataFrame(
            {
                "window_id":       features_df["window_id"].values,
                "cluster_id":      cluster_labels.values,
                "cognitive_state": state_labels.values,
            }
        )
        path = out_dir / "communities.csv"
        out.to_csv(path, index=False)
        LOGGER.info(
            "Clustering: wrote %d window assignments to communities.csv  "
            "(%s)",
            len(out),
            out["cognitive_state"].value_counts().to_dict(),
        )
        return path
