"""Active application tracking (OS-level)."""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Set

try:
    from ctypes import wintypes
except ImportError:  # pragma: no cover - defensive on non-Windows Python builds
    wintypes = None  # type: ignore[assignment]

try:
    import psutil
except ImportError:  # handled by startup validation
    psutil = None  # type: ignore[assignment]

from shared.time_utils import now_ns
from system_agent.browser_url_resolver import BrowserUrlResolver

LOGGER = logging.getLogger(__name__)

# Minimum seconds between consecutive active_app_change emissions.
# Prevents high-frequency spam when window titles change rapidly (e.g. browser tabs).
_MIN_CHANGE_INTERVAL_SEC: float = 1.0
_ACTIVE_WINDOW_RE = re.compile(r"window id #\s*(0x[0-9a-fA-F]+|\d+)", re.IGNORECASE)
_XPROP_PID_RE = re.compile(r"_NET_WM_PID\([^)]*\)\s*=\s*(\d+)")
_QUOTED_VALUE_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _parse_app_context(process_name: str, window_title: str) -> str:
    """Return the most useful context string for an active-app event."""

    title = (window_title or "").strip()
    if not title:
        return ""
    proc = process_name.lower()
    # code/code.exe is VSCode; guard also covers titles that explicitly say
    # "Visual Studio Code".
    if proc in {"code", "code.exe", "codium", "codium.exe"} or (
        "code" in proc and "visual studio code" in title.lower()
    ):
        parts = [p.strip() for p in title.split(" - ")]
        first = parts[0].lstrip("\u25cf").strip()
        if first and first.lower() not in {"visual studio code"}:
            return first
        return ""
    return title


def _normalize_process_name(value: str) -> str:
    text = str(value or "").strip().strip('"').lower()
    if not text:
        return ""
    text = text.replace("\\", "/")
    return text.rsplit("/", 1)[-1]


def _process_aliases(value: str) -> set[str]:
    base = _normalize_process_name(value)
    if not base:
        return set()
    aliases = {base}
    if base.endswith(".exe"):
        aliases.add(base[:-4])
    else:
        aliases.add(f"{base}.exe")
    return aliases


def _xprop_strings(line: str) -> list[str]:
    values: list[str] = []
    for match in _QUOTED_VALUE_RE.finditer(line):
        raw = match.group(0)
        try:
            import ast

            values.append(str(ast.literal_eval(raw)))
        except Exception:
            values.append(match.group(1).replace('\\"', '"').replace("\\\\", "\\"))
    return values


def _xprop_string(output: str, property_name: str) -> str:
    prefix = f"{property_name}("
    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        strings = _xprop_strings(line)
        if strings:
            return strings[0]
        if "=" in line:
            value = line.split("=", 1)[1].strip()
            return "" if value == "not found." else value
    return ""


def _xprop_string_list(output: str, property_name: str) -> list[str]:
    prefix = f"{property_name}("
    for line in output.splitlines():
        if line.startswith(prefix):
            return _xprop_strings(line)
    return []


@dataclass(frozen=True)
class AppSnapshot:
    timestamp_ns: int
    process_name: str
    window_title: str
    pid: int | None
    is_browser: bool
    url: str = ""

    @property
    def app_name(self) -> str:
        return self.process_name or "unknown"

    @property
    def context(self) -> str:
        """Parsed, human-useful context extracted from the window title."""
        return _parse_app_context(self.process_name, self.window_title)


class AppTracker:
    """Polls active OS window and emits change notifications."""

    def __init__(
        self,
        poll_interval_sec: float,
        browser_processes: Set[str],
        on_change: Callable[[AppSnapshot], None],
    ) -> None:
        self._poll_interval_sec = poll_interval_sec
        self._browser_processes: set[str] = set()
        for name in browser_processes:
            self._browser_processes.update(_process_aliases(name))
        self._on_change = on_change
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_snapshot: Optional[AppSnapshot] = None
        self._url_resolver = BrowserUrlResolver()
        self._warned_once: set[str] = set()

        self._platform = platform.system().lower()
        self._is_windows = self._platform == "windows"
        self._is_linux = self._platform == "linux"
        if not (self._is_windows or self._is_linux):
            LOGGER.warning("AppTracker does not support %s; fallback will be 'unknown'.", self._platform)

    @property
    def current_snapshot(self) -> Optional[AppSnapshot]:
        return self._last_snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="app-tracker", daemon=True)
        self._thread.start()

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout_sec)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            snapshot = self._capture_snapshot()
            if self._has_changed(snapshot):
                self._last_snapshot = snapshot
                try:
                    self._on_change(snapshot)
                except Exception as exc:  # pragma: no cover - callback safety
                    LOGGER.exception("AppTracker callback failed: %s", exc)

            time.sleep(self._poll_interval_sec)

    def _has_changed(self, snapshot: AppSnapshot) -> bool:
        previous = self._last_snapshot
        if previous is None:
            return True
        elapsed_sec = (snapshot.timestamp_ns - previous.timestamp_ns) / 1_000_000_000
        if previous.process_name == snapshot.process_name:
            if (
                snapshot.is_browser
                and elapsed_sec >= _MIN_CHANGE_INTERVAL_SEC
                and (
                    (snapshot.url and snapshot.url != previous.url)
                    or (not snapshot.url and not previous.url and snapshot.window_title != previous.window_title)
                )
            ):
                return True
            return False
        # Debounce: drop events that arrive faster than the minimum interval
        # even if the process name did change (rapid alt-tab sequences).
        if elapsed_sec < _MIN_CHANGE_INTERVAL_SEC:
            return False
        return True

    def _capture_snapshot(self) -> AppSnapshot:
        if self._is_windows:
            return self._capture_windows()
        if self._is_linux:
            return self._capture_linux()
        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name="unknown",
            window_title="unsupported-platform",
            pid=None,
            is_browser=False,
        )

    def _capture_windows(self) -> AppSnapshot:
        if psutil is None:
            raise RuntimeError(
                "AppTracker requires `psutil` for foreground process detection. "
                "Install with `pip install psutil`."
            )
        if wintypes is None:
            raise RuntimeError("Windows foreground tracking requires ctypes.wintypes.")

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="",
                pid=None,
                is_browser=False,
            )

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        title_buffer = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, title_buffer, 1024)
        window_title = title_buffer.value

        process_name = "unknown"
        proc_pid = int(pid.value) if pid.value else None
        if proc_pid:
            try:
                process_name = psutil.Process(proc_pid).name().lower()
            except Exception:
                process_name = "unknown"

        is_browser = self._is_browser_process(process_name)
        url = self._url_resolver.resolve(int(hwnd), process_name) if is_browser else ""

        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name=process_name,
            window_title=window_title,
            pid=proc_pid,
            is_browser=is_browser,
            url=url,
        )

    def _capture_linux(self) -> AppSnapshot:
        if psutil is None:
            raise RuntimeError(
                "AppTracker requires `psutil` for foreground process detection. "
                "Install with `pip install psutil`."
            )

        if not os.environ.get("DISPLAY"):
            reason = "wayland-active-window-unavailable" if os.environ.get("WAYLAND_DISPLAY") else "display-unavailable"
            self._warn_once(
                "linux-no-display",
                "Linux foreground app tracking needs an X11 DISPLAY. Browser focus pause/resume still works "
                "through the extension, but non-browser app names may be unknown on this desktop session.",
            )
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title=reason,
                pid=None,
                is_browser=False,
            )

        window_id = self._linux_active_window_id()
        if not window_id:
            return AppSnapshot(
                timestamp_ns=now_ns(),
                process_name="unknown",
                window_title="no-active-window",
                pid=None,
                is_browser=False,
            )

        pid, title, wm_class = self._linux_window_metadata(window_id)
        process_name = "unknown"
        if pid:
            try:
                process_name = psutil.Process(pid).name().lower()
            except Exception:
                process_name = "unknown"
        if process_name == "unknown":
            process_name = self._process_from_wm_class(wm_class) or "unknown"

        return AppSnapshot(
            timestamp_ns=now_ns(),
            process_name=process_name,
            window_title=title,
            pid=pid,
            is_browser=self._is_browser_process(process_name, wm_class),
            url="",
        )

    def _linux_active_window_id(self) -> str:
        if shutil.which("xprop"):
            output = self._run_command(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
            match = _ACTIVE_WINDOW_RE.search(output)
            if match:
                window_id = match.group(1)
                if window_id not in {"0", "0x0"}:
                    return window_id

        if shutil.which("xdotool"):
            output = self._run_command(["xdotool", "getactivewindow"]).strip()
            if output and output != "0":
                return output

        self._warn_once(
            "linux-no-window-tool",
            "Could not read the active X11 window. Install `x11-utils` (xprop) or `xdotool` "
            "for Linux foreground app tracking.",
        )
        return ""

    def _linux_window_metadata(self, window_id: str) -> tuple[int | None, str, str]:
        pid: int | None = None
        title = ""
        wm_class = ""

        if shutil.which("xprop"):
            output = self._run_command(
                ["xprop", "-id", window_id, "_NET_WM_PID", "_NET_WM_NAME", "WM_NAME", "WM_CLASS"]
            )
            pid_match = _XPROP_PID_RE.search(output)
            if pid_match:
                try:
                    pid = int(pid_match.group(1))
                except ValueError:
                    pid = None
            title = _xprop_string(output, "_NET_WM_NAME") or _xprop_string(output, "WM_NAME")
            wm_class = " ".join(_xprop_string_list(output, "WM_CLASS"))

        if not title and shutil.which("xdotool"):
            title = self._run_command(["xdotool", "getwindowname", window_id]).strip()
        if pid is None and shutil.which("xdotool"):
            pid_output = self._run_command(["xdotool", "getwindowpid", window_id]).strip()
            try:
                pid = int(pid_output)
            except ValueError:
                pid = None

        return pid, title, wm_class

    def _is_browser_process(self, process_name: str, wm_class: str = "") -> bool:
        candidates = _process_aliases(process_name)
        for token in re.split(r"[\s,]+", wm_class or ""):
            candidates.update(_process_aliases(token))
        return bool(candidates & self._browser_processes)

    @staticmethod
    def _process_from_wm_class(wm_class: str) -> str:
        for token in reversed(re.split(r"[\s,]+", wm_class or "")):
            normalized = _normalize_process_name(token)
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _run_command(cmd: list[str]) -> str:
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1.0,
            )
        except Exception:
            return ""
        return result.stdout if result.returncode == 0 else ""

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned_once:
            return
        self._warned_once.add(key)
        LOGGER.warning(message)
