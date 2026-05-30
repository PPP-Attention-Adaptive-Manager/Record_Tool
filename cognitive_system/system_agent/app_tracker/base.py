"""Abstract base class for platform-specific app tracker backends."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSnapshot:
    """Immutable snapshot of the foreground window state."""

    timestamp_ns: int
    process_name: str
    window_title: str
    pid: Optional[int]
    is_browser: bool
    url: str = ""

    @property
    def app_name(self) -> str:
        return self.process_name or "unknown"


@dataclass(frozen=True)
class ProbeResult:
    """Result of a backend capability probe."""

    available: bool
    backend_name: str
    detail: str = ""
    guidance: str = ""


class AppTrackerBackend(ABC):
    """Base class for platform-specific foreground window detection."""

    @classmethod
    def backend_name(cls) -> str:
        """Human-readable name for this backend."""
        return cls.__name__

    @classmethod
    def is_candidate(cls) -> bool:
        """Quick environment check: should this backend even be considered?

        Returns True if the current platform/environment could potentially
        support this backend. Does not import heavy dependencies.
        """
        return True

    @classmethod
    def probe(cls) -> ProbeResult:
        """Full probe: attempt to initialise and verify the backend works.

        May import dependencies and make test calls. Should not raise
        exceptions — return a ProbeResult with available=False instead.
        """
        return ProbeResult(
            available=True,
            backend_name=cls.backend_name(),
            detail="base implementation",
        )

    @abstractmethod
    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        """Capture the current foreground window state.

        Args:
            browser_processes: Set of process names (lowercased) to treat as browsers.

        Returns:
            AppSnapshot with the current foreground window info.
        """
        ...

    def cleanup(self) -> None:
        """Optional cleanup called when the tracker is stopped.

        Default is a no-op. Override if the backend needs to release resources
        (e.g. unload a KWin script, close X11 display, etc.).
        """
