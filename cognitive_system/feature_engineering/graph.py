"""Backward-compatible import shim for the event-based temporal graph builder."""

from .graph_builder import GraphBuilder, GraphConfig, NODE_LEVEL

__all__ = ["GraphBuilder", "GraphConfig", "NODE_LEVEL"]
