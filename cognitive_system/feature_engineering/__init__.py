"""
Feature engineering pipeline for multi-stream cognitive behavioral data.

Pipeline stages
---------------
RAW CSVs  ->  WindowEngine  ->  FeatureExtractor
     |                              |
behavior.csv                  features_*.csv
     |                              |
TemporalGraphBuilder          communities.csv
     |
nodes.csv / edges.csv / temporal_edges.csv

Entry point
-----------
>>> from feature_engineering.pipeline import FeaturePipeline, PipelineConfig
>>> FeaturePipeline().run("session_20240427_001")
"""

from .windowing import WindowConfig, WindowEngine, MICRO_5S, MESO_30S, MACRO_120S, DEFAULT_WINDOW_CONFIGS
from .features import FeatureConfig, FeatureExtractor, Normalizer
from .graph_builder import GraphBuilder, GraphConfig, NODE_LEVEL
from .clustering import CognitiveStateClusterer

# pipeline is intentionally NOT imported here to avoid a sys.modules
# conflict when the module is run directly with  python -m feature_engineering.pipeline

__all__ = [
    "WindowConfig", "WindowEngine",
    "MICRO_5S", "MESO_30S", "MACRO_120S", "DEFAULT_WINDOW_CONFIGS",
    "FeatureConfig", "FeatureExtractor", "Normalizer",
    "GraphBuilder", "GraphConfig", "NODE_LEVEL",
    "CognitiveStateClusterer",
]
