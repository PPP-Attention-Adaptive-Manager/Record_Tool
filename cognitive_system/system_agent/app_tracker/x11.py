"""X11 app tracker backend using python-xlib."""

from __future__ import annotations

import logging
import os
import platform
from typing import Optional

from shared.time_utils import now_ns

from .base import AppSnapshot, AppTrackerBackend, ProbeResult

LOGGER = logging.getLogger(__name__)


class X11AppBackend(AppTrackerBackend):
    """Foreground window detection via X11 (python-xlib).

    Uses XGetInputFocus to find the focused window, then walks the window
    tree to find the top-level client window and reads its WM_NAME /
    _NET_WM_NAME properties along with _NET_WM_PID.
    """

    @classmethod
    def backend_name(cls) -> str:
        return "x11"

    @classmethod
    def is_candidate(cls) -> bool:
        return (
            platform.system().lower() == "linux"
            and os.environ.get("DISPLAY") is not None
            and os.environ.get("WAYLAND_DISPLAY") is None
        )

    @classmethod
    def probe(cls) -> ProbeResult:
        if not cls.is_candidate():
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="Not an X11 session (no DISPLAY or WAYLAND_DISPLAY is set)",
            )
        try:
            from Xlib import display  # noqa: F401
            d = display.Display()
            d.screen_count()
            d.close()
            return ProbeResult(
                available=True,
                backend_name=cls.backend_name(),
                detail="X11 display accessible",
            )
        except ImportError:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail="python-xlib not installed",
                guidance="pip install python-xlib",
            )
        except Exception as exc:
            return ProbeResult(
                available=False,
                backend_name=cls.backend_name(),
                detail=f"X11 display error: {exc}",
            )

    def __init__(self) -> None:
        from Xlib import display
        from Xlib.X import InputFocus
        from Xlib.Xatom import WINDOW

        self._display = display.Display()
        self._root = self._display.screen().root
        self._atom_net_wm_name = self._display.intern_atom("_NET_WM_NAME", only_if_exists=True)
        self._atom_wm_name = self._display.intern_atom("WM_NAME", only_if_exists=True)
        self._atom_net_wm_pid = self._display.intern_atom("_NET_WM_PID", only_if_exists=True)
        self._atom_wm_class = self._display.intern_atom("WM_CLASS", only_if_exists=True)

    def _get_window_name(self, window) -> str:
        for atom in (self._atom_net_wm_name, self._atom_wm_name):
            if atom == self._display.get_atom("NONE"):
                continue
            try:
                prop = window.get_full_property(atom, 0)
                if prop and prop.value:
                    return prop.value.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
        return ""

    def _get_window_pid(self, window) -> Optional[int]:
        try:
            if self._atom_net_wm_pid == self._display.get_atom("NONE"):
                return None
            prop = window.get_full_property(self._atom_net_wm_pid, 0)
            if prop and prop.value and len(prop.value) >= 4:
                import struct
                return struct.unpack("I", bytes(prop.value[:4]))[0]
        except Exception:
            pass
        return None

    def _get_wm_class(self, window) -> Optional[str]:
        """Read WM_CLASS property. Returns the instance name (first string)."""
        try:
            prop = window.get_full_property(self._atom_wm_class, 0)
            if prop and prop.value:
                # WM_CLASS is two null-terminated strings: instance, class
                parts = prop.value.split(b"\x00")
                if parts and parts[0]:
                    return parts[0].decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        return None

    def _get_top_level_window(self, window):
        """Walk up the window tree to find the top-level client window."""
        current = window
        while True:
            try:
                parent = current.query_tree().parent
                if parent is None or parent == self._root:
                    break
                wm_state = parent.get_full_property(
                    self._display.intern_atom("WM_STATE", only_if_exists=True), 0
                )
                if wm_state and wm_state.value:
                    current = parent
                else:
                    break
            except Exception:
                break
        return current

    def _get_process_name(self, pid: Optional[int]) -> str:
        if pid is None:
            return "unknown"
        try:
            import psutil
            return psutil.Process(pid).name().lower()
        except Exception:
            return "unknown"

    def capture_snapshot(self, browser_processes: set[str]) -> AppSnapshot:
        try:
            focus_result = self._display.get_input_focus()
            window = focus_result.focus

            if not window:
                return AppSnapshot(
                    timestamp_ns=now_ns(),
                    process_name="unknown",
                    window_title="",
                    pid=None,
                    is_browser=False,
                )

            top_window = self._get_top_level_window(window)
            window_title = self._get_window_name(top_window)
            pid = self._get_window_pid(top_window)

            if pid is not None:
                process_name = self._get_process_name(pid)
            else:
                wm_class = self._get_wm_class(top_window)
                process_name = wm_class.lower() if wm_class else "unknown"

            is_browser = process_name in browser_processes

            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name=process_name,
                window_title=window_title,
                pid=pid,
                is_browser=is_browser,
            )
        except Exception as exc:
            LOGGER.debug("X11 capture error: %s", exc)
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

    def cleanup(self) -> None:
        try:
            self._display.close()
        except Exception:
            pass
