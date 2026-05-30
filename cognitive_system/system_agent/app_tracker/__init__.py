"""App tracker package — platform-agnostic foreground window detection.

Public API:
    AppTracker          — orchestrator class (threading, debounce, callback)
    AppSnapshot         — immutable snapshot dataclass
    create_app_tracker_backend  — factory function
    probe_all_backends  — probe all backends and return status list
"""

from .base import AppSnapshot, ProbeResult
from .factory import create_app_tracker_backend, probe_all_backends
from .tracker import AppTracker

__all__ = [
    "AppTracker",
    "AppSnapshot",
    "ProbeResult",
    "create_app_tracker_backend",
    "probe_all_backends",
]
