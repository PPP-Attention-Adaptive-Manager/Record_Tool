from __future__ import annotations

import importlib.util

from .config import RuntimeConfig


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def validate_runtime_dependencies(config: RuntimeConfig) -> None:
    missing: list[str] = []

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

    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise RuntimeError(
            "Dependency validation failed. Resolve the following before starting:\n"
            f"{details}\n"
            "Tip: install all agent deps with `pip install -r cognitive_system/system_agent/requirements.txt`."
        )
