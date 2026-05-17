"""Abstract base class for platform-specific browser URL resolvers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

LOGGER = logging.getLogger(__name__)


class BrowserUrlResolverBackend(ABC):
    """Base class for platform-specific browser URL resolution."""

    @classmethod
    def backend_name(cls) -> str:
        return cls.__name__

    @classmethod
    def is_candidate(cls) -> bool:
        return True

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        return True, "base implementation"

    @abstractmethod
    def resolve(self, hwnd: int, process_name: str) -> str:
        """Attempt to resolve the URL of the foreground browser window.

        Args:
            hwnd: Window handle (platform-specific; on Linux this is unused).
            process_name: Lowercased process name (e.g. "chrome.exe").

        Returns:
            The resolved URL, or empty string if unavailable.
        """
        ...
