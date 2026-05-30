"""Windows browser URL resolver via pywinauto UI Automation."""

from __future__ import annotations

import logging
import platform
import re
from typing import Any

from .base import BrowserUrlResolverBackend

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


class WindowsUrlResolverBackend(BrowserUrlResolverBackend):
    """Resolve browser URL via pywinauto UI Automation."""

    @classmethod
    def backend_name(cls) -> str:
        return "windows"

    @classmethod
    def is_candidate(cls) -> bool:
        return platform.system().lower() == "windows"

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        if not cls.is_candidate():
            return False, "Not a Windows platform"
        try:
            from pywinauto import Desktop  # noqa: F401
            return True, "pywinauto available"
        except ImportError:
            return False, "pywinauto not installed"
        except Exception as exc:
            return False, f"pywinauto error: {exc}"

    def __init__(self) -> None:
        self._missing_dependency_logged = False
        self._last_error: str | None = None

    def resolve(self, hwnd: int, process_name: str) -> str:
        if not hwnd:
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
