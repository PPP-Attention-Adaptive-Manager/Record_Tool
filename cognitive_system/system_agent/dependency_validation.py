from __future__ import annotations

import importlib.util
import logging
import platform
import shutil

from .config import RuntimeConfig

LOGGER = logging.getLogger(__name__)


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def validate_runtime_dependencies(config: RuntimeConfig) -> None:
    missing: list[str] = []
    system_name = platform.system().lower()

    if not _module_available("websockets"):
        missing.append(
            "`websockets` is required for extension command transport. Install with: "
            "`pip install websockets`"
        )
    if not _module_available("aiohttp"):
        missing.append(
            "`aiohttp` is required for HTTP intake endpoints. Install with: "
            "`pip install aiohttp`"
        )

    if config.influx_enabled and not _module_available("influxdb_client"):
        missing.append(
            "Influx export is enabled but `influxdb-client` is missing. Install with: "
            "`pip install influxdb-client` or disable Influx at startup."
        )

    if (config.keyboard_tracking_enabled or config.mouse_tracking_enabled) and not _module_available("pynput"):
        missing.append(
            "Keyboard/mouse tracking is enabled but `pynput` is missing. Install with: "
            "`pip install pynput` or disable local input tracking."
        )

    if not _module_available("psutil"):
        missing.append(
            "`psutil` is required for active-application foreground detection. "
            "Install with: `pip install psutil`."
        )

    if system_name == "linux":
        if not (shutil.which("xprop") or shutil.which("xdotool")):
            LOGGER.warning(
                "Linux foreground-app names need `xprop` (x11-utils) or `xdotool`; "
                "browser focus pause/resume still works through the extension."
            )
        if config.notification_tracking_enabled and not shutil.which("dbus-monitor"):
            LOGGER.warning(
                "Linux notification tracking needs `dbus-monitor`; install your "
                "distribution's DBus tools package or disable notification tracking."
            )

    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise RuntimeError(
            "Dependency validation failed. Resolve the following before starting:\n"
            f"{details}\n"
            "Tip: install all agent deps with `pip install -r cognitive_system/system_agent/requirements.txt`."
        )
