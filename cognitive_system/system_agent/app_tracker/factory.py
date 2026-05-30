"""Factory for creating app tracker backends based on the current environment.

Probes available backends in priority order and returns the first one that
reports itself as available.
"""

from __future__ import annotations

import logging
from typing import Optional, Type

from .base import AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)

# Ordered by preference. The factory tries each in sequence and picks the
# first backend whose probe() reports available=True.
BACKEND_REGISTRY: list[Type[AppTrackerBackend]] = []


def _register_backends() -> None:
    """Register backends in priority order.

    We import lazily so that a missing platform-specific dependency (e.g.
    python-xlib on a Wayland-only system) doesn't break the entire module.
    """
    global BACKEND_REGISTRY
    if BACKEND_REGISTRY:
        return

    # Windows — always first on Windows
    try:
        from .windows import WindowsAppBackend
        BACKEND_REGISTRY.append(WindowsAppBackend)
    except ImportError:
        pass

    # Linux backends
    try:
        from .x11 import X11AppBackend
        BACKEND_REGISTRY.append(X11AppBackend)
    except ImportError:
        pass

    try:
        from .gnome_wayland import GnomeWaylandAppBackend
        BACKEND_REGISTRY.append(GnomeWaylandAppBackend)
    except ImportError:
        pass

    try:
        from .kde_wayland import KdeWaylandAppBackend
        BACKEND_REGISTRY.append(KdeWaylandAppBackend)
    except ImportError:
        pass

    try:
        from .sway import SwayAppBackend
        BACKEND_REGISTRY.append(SwayAppBackend)
    except ImportError:
        pass

    try:
        from .hyprland import HyprlandAppBackend
        BACKEND_REGISTRY.append(HyprlandAppBackend)
    except ImportError:
        pass

    # Fallback — always last
    try:
        from .proc_fallback import ProcFallbackBackend
        BACKEND_REGISTRY.append(ProcFallbackBackend)
    except ImportError:
        pass


def probe_all_backends() -> list[ProbeResult]:
    """Probe all registered backends and return their status.

    Returns a list of ProbeResult for every backend in the registry, in
    priority order.
    """
    _register_backends()
    results = []
    for backend_cls in BACKEND_REGISTRY:
        try:
            result = backend_cls.probe()
        except Exception as exc:
            result = ProbeResult(
                available=False,
                backend_name=backend_cls.backend_name(),
                detail=f"Probe crashed: {exc}",
            )
        results.append(result)
    return results


def get_best_backend() -> Optional[AppTrackerBackend]:
    """Return an instance of the best available backend, or None."""
    _register_backends()

    for backend_cls in BACKEND_REGISTRY:
        try:
            result = backend_cls.probe()
            if result.available:
                LOGGER.info("Selected app tracker backend: %s", backend_cls.backend_name())
                return backend_cls()
            else:
                LOGGER.debug("Backend %s not available: %s", backend_cls.backend_name(), result.detail)
        except Exception as exc:
            LOGGER.debug("Backend %s probe failed: %s", backend_cls.backend_name(), exc)

    LOGGER.warning("No app tracker backend is available — all probes failed")
    return None


def create_app_tracker_backend(
    preferred: str | None = None,
) -> AppTrackerBackend:
    """Create the best available backend, optionally forcing a specific one.

    Args:
        preferred: If set, try this backend first (by backend_name). If it
            fails, fall back to the normal priority order.

    Returns:
        An AppTrackerBackend instance.

    Raises:
        RuntimeError: If no backend is available.
    """
    _register_backends()

    if preferred:
        for backend_cls in BACKEND_REGISTRY:
            if backend_cls.backend_name() == preferred:
                result = backend_cls.probe()
                if result.available:
                    LOGGER.info("Using preferred backend: %s", preferred)
                    return backend_cls()
                else:
                    LOGGER.warning(
                        "Preferred backend %s not available (%s), falling back",
                        preferred,
                        result.detail,
                    )
                    break

    backend = get_best_backend()
    if backend is None:
        raise RuntimeError(
            "No app tracker backend is available. "
            "On Linux, install python-xlib (X11) or dasbus (Wayland). "
            "On Windows, ensure psutil is installed."
        )
    return backend
