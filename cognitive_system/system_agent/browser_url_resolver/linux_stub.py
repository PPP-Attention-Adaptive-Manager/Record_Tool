"""Linux browser URL resolver stub.

On Linux, the browser extension is the primary and only reliable source of
browser URLs. There is no cross-platform equivalent to pywinauto's UI
Automation for reading browser address bars on Linux, especially under
Wayland where window contents are inaccessible.

This backend always returns an empty string. Callers should treat empty
URL as "no URL available" and fall back to window title or process name.
"""

from __future__ import annotations

import logging
import platform

from .base import BrowserUrlResolverBackend

LOGGER = logging.getLogger(__name__)


class LinuxUrlResolverStub(BrowserUrlResolverBackend):
    """Always returns empty string — extension is the primary URL source."""

    @classmethod
    def backend_name(cls) -> str:
        return "linux_stub"

    @classmethod
    def is_candidate(cls) -> bool:
        return platform.system().lower() == "linux"

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        return True, "stub — URLs come from browser extension"

    def __init__(self) -> None:
        self._logged = False

    def resolve(self, hwnd: int, process_name: str) -> str:
        if not self._logged:
            LOGGER.debug(
                "Browser URL resolver is a stub on Linux; "
                "URLs are provided by the browser extension."
            )
            self._logged = True
        return ""
