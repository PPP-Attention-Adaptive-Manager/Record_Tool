"""Abstract base class for platform-specific notification tracker backends."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)


class NotificationTrackerBackend(ABC):
    """Base class for platform-specific notification tracking."""

    @classmethod
    def backend_name(cls) -> str:
        return cls.__name__

    @classmethod
    def is_candidate(cls) -> bool:
        return True

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        """Return (available, detail)."""
        return True, "base implementation"

    @abstractmethod
    def start(self, on_event: Callable[[dict], None]) -> None:
        """Start tracking notifications.

        Args:
            on_event: Callback invoked with a dict for each notification event.
                Expected keys: timestamp, app_source, notif_id,
                interaction_type, response_latency_ms
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
