"""Browser URL resolver — platform-agnostic address bar reading."""

from __future__ import annotations

import logging
from typing import Type

from .base import BrowserUrlResolverBackend

LOGGER = logging.getLogger(__name__)

_BACKEND_REGISTRY: list[Type[BrowserUrlResolverBackend]] = []


def _register_backends() -> None:
    global _BACKEND_REGISTRY
    if _BACKEND_REGISTRY:
        return

    try:
        from .windows import WindowsUrlResolverBackend
        _BACKEND_REGISTRY.append(WindowsUrlResolverBackend)
    except ImportError:
        pass

    try:
        from .linux_stub import LinuxUrlResolverStub
        _BACKEND_REGISTRY.append(LinuxUrlResolverStub)
    except ImportError:
        pass


def create_url_resolver_backend() -> BrowserUrlResolverBackend:
    """Create the best available URL resolver backend."""
    _register_backends()

    for backend_cls in _BACKEND_REGISTRY:
        try:
            available, detail = backend_cls.probe()
            if available:
                LOGGER.info("Selected URL resolver backend: %s", backend_cls.backend_name())
                return backend_cls()
            else:
                LOGGER.debug("URL resolver backend %s not available: %s", backend_cls.backend_name(), detail)
        except Exception as exc:
            LOGGER.debug("URL resolver backend %s probe failed: %s", backend_cls.backend_name(), exc)

    return _NullBackend()


class _NullBackend(BrowserUrlResolverBackend):
    def resolve(self, hwnd: int, process_name: str) -> str:
        return ""
