"""Notification tracker — platform-agnostic notification event tracking."""

from __future__ import annotations

import logging
import platform
from typing import Callable, Optional, Type

from .base import NotificationTrackerBackend

LOGGER = logging.getLogger(__name__)

_BACKEND_REGISTRY: list[Type[NotificationTrackerBackend]] = []


def _register_backends() -> None:
    global _BACKEND_REGISTRY
    if _BACKEND_REGISTRY:
        return

    try:
        from .windows import WindowsNotificationBackend
        _BACKEND_REGISTRY.append(WindowsNotificationBackend)
    except ImportError:
        pass

    try:
        from .linux_dbus import LinuxNotificationBackend
        _BACKEND_REGISTRY.append(LinuxNotificationBackend)
    except ImportError:
        pass


def create_notification_backend() -> NotificationTrackerBackend:
    """Create the best available notification backend."""
    _register_backends()

    for backend_cls in _BACKEND_REGISTRY:
        try:
            available, detail = backend_cls.probe()
            if available:
                LOGGER.info("Selected notification backend: %s", backend_cls.backend_name())
                return backend_cls()
            else:
                LOGGER.debug("Notification backend %s not available: %s", backend_cls.backend_name(), detail)
        except Exception as exc:
            LOGGER.debug("Notification backend %s probe failed: %s", backend_cls.backend_name(), exc)

    LOGGER.warning("No notification backend available — notifications will not be tracked")
    return _NullBackend()


class _NullBackend(NotificationTrackerBackend):
    """No-op backend when no platform-specific backend is available."""

    def start(self, on_event: Callable[[dict], None]) -> None:
        pass

    def stop(self) -> None:
        pass
