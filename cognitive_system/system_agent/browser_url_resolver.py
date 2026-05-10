"""Best-effort active browser URL capture via Windows UI Automation.

This is a system-agent fallback for sessions where the browser extension is not
connected. It reads the active browser address bar without focusing it or
sending keystrokes. If UI Automation or the optional dependency is unavailable,
callers simply receive an empty URL. Linux browser URLs are supplied by the
browser extension focus/navigation events instead of OS address-bar scraping.
"""

from __future__ import annotations

import logging
import platform
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

_URL_SCHEMES = (
    "http://",
    "https://",
    "file://",
    "chrome://",
    "edge://",
    "about:",
    "moz-extension://",
    "chrome-extension://",
)
_DOMAIN_LIKE_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}([/:?#].*)?$", re.IGNORECASE)
_PLACEHOLDER_TOKENS = (
    "search",
    "address",
    "enter",
    "type",
)


class BrowserUrlResolver:
    """Resolve the URL of the foreground browser window when possible."""

    def __init__(self) -> None:
        self._is_windows = platform.system().lower() == "windows"
        self._missing_dependency_logged = False
        self._last_error: str | None = None

    def resolve(self, hwnd: int, process_name: str) -> str:
        if not self._is_windows or not hwnd:
            return ""

        try:
            from pywinauto import Desktop  # type: ignore[import]
        except ImportError:
            if not self._missing_dependency_logged:
                LOGGER.info(
                    "Browser URL capture requires optional dependency pywinauto; "
                    "browser titles will still be collected."
                )
                self._missing_dependency_logged = True
            return ""

        try:
            window = Desktop(backend="uia").window(handle=int(hwnd))
            for control in window.descendants(control_type="Edit"):
                url = _normalize_url(_control_value(control))
                if url:
                    return url
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if message != self._last_error:
                LOGGER.debug("Could not resolve browser URL for %s: %s", process_name, message)
                self._last_error = message
        return ""


def _control_value(control: Any) -> str:
    for getter in (
        lambda: control.get_value(),
        lambda: control.iface_value.CurrentValue,
        lambda: control.window_text(),
    ):
        try:
            value = getter()
        except Exception:
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    if any(lowered.startswith(scheme) for scheme in _URL_SCHEMES):
        return text

    if any(token in lowered for token in _PLACEHOLDER_TOKENS) and " " in lowered:
        return ""
    if "\\" in text or " " in text:
        return ""
    if _DOMAIN_LIKE_RE.match(text):
        return f"https://{text}"
    return ""
