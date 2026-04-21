"""Timestamp helpers used across components."""

from __future__ import annotations

from datetime import datetime, timezone
import time


def ns_to_iso8601(timestamp_ns: int) -> str:
    """Convert epoch nanoseconds to UTC ISO8601 string."""
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def now_ns() -> int:
    """Current epoch time in nanoseconds."""
    return time.time_ns()
