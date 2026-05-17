from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

MODE_EXPERIMENTAL = "experimental"
MODE_PRODUCTION = "production"
VALID_MODES = (MODE_EXPERIMENTAL, MODE_PRODUCTION)
DUAL_TASK_INTERVAL_REGULAR = "regular"
DUAL_TASK_INTERVAL_RANDOM = "random"
VALID_DUAL_TASK_INTERVAL_MODES = (DUAL_TASK_INTERVAL_REGULAR, DUAL_TASK_INTERVAL_RANDOM)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    return _repo_root() / "data"


def _shared_runtime_config_path() -> Path:
    return _repo_root() / "browser_agent_v2" / "config" / "runtime_config.json"


_SHARED_RUNTIME_CONFIG_CACHE: dict[str, Any] | None = None


def _load_shared_runtime_config() -> dict[str, Any]:
    global _SHARED_RUNTIME_CONFIG_CACHE
    if _SHARED_RUNTIME_CONFIG_CACHE is not None:
        return _SHARED_RUNTIME_CONFIG_CACHE

    path = _shared_runtime_config_path()
    if not path.exists():
        _SHARED_RUNTIME_CONFIG_CACHE = {}
        return _SHARED_RUNTIME_CONFIG_CACHE

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in shared runtime config: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Shared runtime config must contain a JSON object: {path}")

    _SHARED_RUNTIME_CONFIG_CACHE = payload
    return _SHARED_RUNTIME_CONFIG_CACHE


def _shared_get(*keys: str, default: Any) -> Any:
    current: Any = _load_shared_runtime_config()
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return default if current is None else current


def _shared_str(default: str, *keys: str) -> str:
    value = _shared_get(*keys, default=default)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _shared_int(default: int, *keys: str) -> int:
    value = _shared_get(*keys, default=default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _shared_bool(default: bool, *keys: str) -> bool:
    coerced = _coerce_bool(_shared_get(*keys, default=default))
    return default if coerced is None else coerced


def _shared_optional_bool(default: bool | None, *keys: str) -> bool | None:
    value = _shared_get(*keys, default=default)
    if value is None:
        return default
    return _coerce_bool(value)


@dataclass
class RuntimeConfig:
    mode: str
    session_duration_minutes: int
    csv_enabled: bool
    influx_enabled: bool
    dual_task_enabled: bool
    questionnaire_enabled: bool
    dual_task_interval_seconds: int
    dual_task_timeout_seconds: int
    dual_task_interval_mode: str = DUAL_TASK_INTERVAL_RANDOM
    dual_task_random_min_seconds: int = 180
    dual_task_random_max_seconds: int = 360
    dual_task_randomize_position: bool = field(
        default_factory=lambda: _shared_bool(True, "agent", "dual_task_randomize_position")
    )
    keyboard_tracking_enabled: bool = True
    mouse_tracking_enabled: bool = True
    notification_tracking_enabled: bool = True
    system_metrics_enabled: bool = True
    ui_overlay_enabled: bool = True

    websocket_host: str = field(
        default_factory=lambda: os.environ.get("WS_HOST")
        or _shared_str("localhost", "server", "websocket_host")
    )
    websocket_port: int = field(
        default_factory=lambda: _env_int("WS_PORT", _shared_int(8765, "server", "websocket_port"))
    )
    http_host: str = field(
        default_factory=lambda: os.environ.get("HTTP_HOST")
        or _shared_str("localhost", "server", "http_host")
    )
    http_port: int = field(
        default_factory=lambda: _env_int("HTTP_PORT", _shared_int(8080, "server", "http_port"))
    )

    session_broadcast_interval: float = 1.0
    app_poll_interval_seconds: float = 0.5
    browser_processes: tuple[str, ...] = (
        # Windows
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
        # Linux
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "ungoogled-chromium",
        "firefox",
        "firefox-esr",
        "microsoft-edge",
        "microsoft-edge-stable",
        "brave-browser",
        "brave",
        "opera",
        "opera-stable",
        "vivaldi",
        "vivaldi-snapshot",
    )

    device_id: str = field(
        default_factory=lambda: os.environ.get("DEVICE_ID", f"device_{uuid.uuid4().hex[:8]}")
    )
    data_dir: Path = field(default_factory=_default_data_dir)

    influxdb_url: str = field(
        default_factory=lambda: os.environ.get("INFLUXDB_URL")
        or _shared_str("http://localhost:8086", "influx", "url")
    )
    influxdb_token: str = field(default_factory=lambda: os.environ.get("INFLUXDB_TOKEN", ""))
    influxdb_org: str = field(
        default_factory=lambda: os.environ.get("INFLUXDB_ORG")
        or _shared_str("cognitive_lab", "influx", "org")
    )
    influxdb_behavior_bucket: str = field(
        default_factory=lambda: _shared_str("behavior_bucket", "influx", "behavior_bucket")
    )
    influxdb_keyboard_bucket: str = field(
        default_factory=lambda: _shared_str("keyboard_bucket", "influx", "keyboard_bucket")
    )
    influxdb_mouse_bucket: str = field(
        default_factory=lambda: _shared_str("mouse_bucket", "influx", "mouse_bucket")
    )
    influxdb_notification_bucket: str = field(
        default_factory=lambda: _shared_str("notification_bucket", "influx", "notification_bucket")
    )
    influxdb_system_bucket: str = field(
        default_factory=lambda: _shared_str("system_bucket", "influx", "system_bucket")
    )

    @property
    def session_duration_seconds(self) -> int:
        return int(self.session_duration_minutes * 60)

    @property
    def is_experimental(self) -> bool:
        return self.mode == MODE_EXPERIMENTAL


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cognitive System Agent startup configuration"
    )
    parser.add_argument("--non-interactive", action="store_true", help="Use CLI flags/env defaults without prompts.")
    parser.add_argument(
        "--mode",
        choices=VALID_MODES,
        default=os.environ.get("MODE") or _shared_str(MODE_EXPERIMENTAL, "agent", "mode"),
    )
    parser.add_argument(
        "--duration-minutes",
        type=int,
        default=_env_int(
            "SESSION_DURATION_MINUTES",
            _shared_int(30, "agent", "session_duration_minutes"),
        ),
    )
    parser.add_argument(
        "--csv-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "csv_enabled"),
    )
    parser.add_argument(
        "--influx-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(False, "agent", "influx_enabled"),
    )
    parser.add_argument(
        "--dual-task-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_optional_bool(None, "agent", "dual_task_enabled"),
    )
    parser.add_argument(
        "--questionnaire-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_optional_bool(None, "agent", "questionnaire_enabled"),
    )
    parser.add_argument(
        "--dual-task-interval-seconds",
        type=int,
        default=_env_int(
            "DUAL_TASK_INTERVAL_SECONDS",
            _shared_int(30, "agent", "dual_task_interval_seconds"),
        ),
    )
    parser.add_argument(
        "--dual-task-interval-mode",
        type=str.lower,
        choices=VALID_DUAL_TASK_INTERVAL_MODES,
        default=(
            os.environ.get("DUAL_TASK_INTERVAL_MODE")
            or _shared_str(DUAL_TASK_INTERVAL_RANDOM, "agent", "dual_task_interval_mode")
        ),
    )
    parser.add_argument(
        "--dual-task-random-min-seconds",
        type=int,
        default=_env_int(
            "DUAL_TASK_RANDOM_MIN_SECONDS",
            _shared_int(180, "agent", "dual_task_random_min_seconds"),
        ),
    )
    parser.add_argument(
        "--dual-task-random-max-seconds",
        type=int,
        default=_env_int(
            "DUAL_TASK_RANDOM_MAX_SECONDS",
            _shared_int(360, "agent", "dual_task_random_max_seconds"),
        ),
    )
    parser.add_argument(
        "--dual-task-timeout-seconds",
        type=int,
        default=_env_int(
            "DUAL_TASK_TIMEOUT_SECONDS",
            _shared_int(3, "agent", "dual_task_timeout_seconds"),
        ),
    )
    parser.add_argument(
        "--dual-task-randomize-position",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "dual_task_randomize_position"),
    )
    parser.add_argument(
        "--keyboard-tracking-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "keyboard_tracking_enabled"),
    )
    parser.add_argument(
        "--mouse-tracking-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "mouse_tracking_enabled"),
    )
    parser.add_argument(
        "--notification-tracking-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "notification_tracking_enabled"),
    )
    parser.add_argument(
        "--system-metrics-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "system_metrics_enabled"),
    )
    parser.add_argument(
        "--ui-overlay-enabled",
        action=argparse.BooleanOptionalAction,
        default=_shared_bool(True, "agent", "ui_overlay_enabled"),
    )
    return parser.parse_args(argv)


def _prompt_choice(prompt: str, options: tuple[str, ...], default: str) -> str:
    options_hint = "/".join(options)
    while True:
        raw = input(f"{prompt} ({options_hint}) [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in options:
            return raw
        print(f"Invalid choice: {raw!r}. Expected one of: {options_hint}.")


def _prompt_int(prompt: str, default: int, min_value: int) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if value < min_value:
            print(f"Value must be >= {min_value}.")
            continue
        return value


def _prompt_bool(prompt: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} ({hint}): ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "1", "true"}:
            return True
        if raw in {"n", "no", "0", "false"}:
            return False
        print("Please answer yes or no.")


def build_runtime_config(argv: Sequence[str] | None = None) -> RuntimeConfig:
    args = _parse_args(argv)
    interactive = not args.non_interactive

    mode_default = args.mode or MODE_EXPERIMENTAL
    mode = _prompt_choice("Mode", VALID_MODES, mode_default) if interactive else mode_default
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode!r}. Expected one of {VALID_MODES}.")

    duration = args.duration_minutes
    if interactive:
        duration = _prompt_int("Session duration (minutes)", duration, min_value=1)
    if duration <= 0:
        raise ValueError("Session duration must be a positive integer.")

    csv_enabled = args.csv_enabled
    if interactive:
        csv_enabled = _prompt_bool("Enable CSV export", csv_enabled)

    influx_enabled = args.influx_enabled
    if interactive:
        influx_enabled = _prompt_bool("Enable InfluxDB export", influx_enabled)

    if not csv_enabled and not influx_enabled:
        raise ValueError("At least one export sink must be enabled (CSV and/or InfluxDB).")

    default_dual_task = args.dual_task_enabled
    default_questionnaire = args.questionnaire_enabled
    if default_dual_task is None:
        default_dual_task = mode == MODE_EXPERIMENTAL
    if default_questionnaire is None:
        default_questionnaire = mode == MODE_EXPERIMENTAL

    if mode == MODE_PRODUCTION:
        dual_task_enabled = False
        questionnaire_enabled = False
        if interactive:
            print("Production mode selected: dual-task and questionnaire are forced OFF.")
    else:
        dual_task_enabled = default_dual_task
        questionnaire_enabled = default_questionnaire
        if interactive:
            dual_task_enabled = _prompt_bool("Enable dual-task reaction probe", dual_task_enabled)
            questionnaire_enabled = _prompt_bool("Enable session-end questionnaire", questionnaire_enabled)

    dual_interval = args.dual_task_interval_seconds
    dual_interval_mode = args.dual_task_interval_mode.strip().lower()
    dual_random_min = args.dual_task_random_min_seconds
    dual_random_max = args.dual_task_random_max_seconds
    dual_timeout = args.dual_task_timeout_seconds
    if dual_interval_mode not in VALID_DUAL_TASK_INTERVAL_MODES:
        raise ValueError(
            f"Invalid dual-task interval mode: {dual_interval_mode!r}. "
            f"Expected one of {VALID_DUAL_TASK_INTERVAL_MODES}."
        )
    if dual_task_enabled and interactive:
        dual_interval_mode = _prompt_choice(
            "Dual-task interval mode",
            VALID_DUAL_TASK_INTERVAL_MODES,
            dual_interval_mode,
        )
        if dual_interval_mode == DUAL_TASK_INTERVAL_RANDOM:
            dual_random_min = _prompt_int("Dual-task random minimum interval (seconds)", dual_random_min, min_value=5)
            dual_random_max = _prompt_int(
                "Dual-task random maximum interval (seconds)",
                dual_random_max,
                min_value=dual_random_min,
            )
        else:
            dual_interval = _prompt_int("Dual-task interval (seconds)", dual_interval, min_value=5)
        dual_timeout = _prompt_int("Dual-task timeout (seconds)", dual_timeout, min_value=1)
    elif dual_task_enabled:
        dual_interval = max(5, dual_interval)
        dual_random_min = max(5, dual_random_min)
        dual_random_max = max(dual_random_min, dual_random_max)
        dual_timeout = max(1, dual_timeout)

    config = RuntimeConfig(
        mode=mode,
        session_duration_minutes=duration,
        csv_enabled=csv_enabled,
        influx_enabled=influx_enabled,
        dual_task_enabled=dual_task_enabled,
        questionnaire_enabled=questionnaire_enabled,
        dual_task_interval_seconds=dual_interval,
        dual_task_timeout_seconds=dual_timeout,
        dual_task_interval_mode=dual_interval_mode,
        dual_task_random_min_seconds=dual_random_min,
        dual_task_random_max_seconds=dual_random_max,
        dual_task_randomize_position=bool(args.dual_task_randomize_position),
        keyboard_tracking_enabled=args.keyboard_tracking_enabled,
        mouse_tracking_enabled=args.mouse_tracking_enabled,
        notification_tracking_enabled=args.notification_tracking_enabled,
        system_metrics_enabled=args.system_metrics_enabled,
        ui_overlay_enabled=args.ui_overlay_enabled,
    )

    config.data_dir.mkdir(parents=True, exist_ok=True)
    return config


def build_default_runtime_config() -> RuntimeConfig:
    """Return the shared-config-driven defaults without CLI prompts."""
    return build_runtime_config(["--non-interactive"])
