from __future__ import annotations

import argparse
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

MODE_EXPERIMENTAL = "experimental"
MODE_PRODUCTION = "production"
VALID_MODES = (MODE_EXPERIMENTAL, MODE_PRODUCTION)


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
    keyboard_tracking_enabled: bool = True
    mouse_tracking_enabled: bool = True

    websocket_host: str = field(default_factory=lambda: os.environ.get("WS_HOST", "localhost"))
    websocket_port: int = field(default_factory=lambda: _env_int("WS_PORT", 8765))
    http_host: str = field(default_factory=lambda: os.environ.get("HTTP_HOST", "localhost"))
    http_port: int = field(default_factory=lambda: _env_int("HTTP_PORT", 8080))

    session_broadcast_interval: float = 1.0
    app_poll_interval_seconds: float = 0.5
    browser_processes: tuple[str, ...] = (
        "chrome.exe",
        "msedge.exe",
        "firefox.exe",
        "brave.exe",
        "opera.exe",
    )

    device_id: str = field(
        default_factory=lambda: os.environ.get("DEVICE_ID", f"device_{uuid.uuid4().hex[:8]}")
    )
    data_dir: Path = field(default_factory=_default_data_dir)

    influxdb_url: str = field(default_factory=lambda: os.environ.get("INFLUXDB_URL", "http://localhost:8086"))
    influxdb_token: str = field(default_factory=lambda: os.environ.get("INFLUXDB_TOKEN", ""))
    influxdb_org: str = field(default_factory=lambda: os.environ.get("INFLUXDB_ORG", "cognitive_lab"))
    influxdb_behavior_bucket: str = "behavior_bucket"
    influxdb_keyboard_bucket: str = "keyboard_bucket"
    influxdb_mouse_bucket: str = "mouse_bucket"

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
    parser.add_argument("--mode", choices=VALID_MODES, default=os.environ.get("MODE"))
    parser.add_argument("--duration-minutes", type=int, default=_env_int("SESSION_DURATION_MINUTES", 30))
    parser.add_argument("--csv-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--influx-enabled", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--dual-task-enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--questionnaire-enabled", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--dual-task-interval-seconds", type=int, default=_env_int("DUAL_TASK_INTERVAL_SECONDS", 30))
    parser.add_argument("--dual-task-timeout-seconds", type=int, default=_env_int("DUAL_TASK_TIMEOUT_SECONDS", 3))
    parser.add_argument("--keyboard-tracking-enabled", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mouse-tracking-enabled", action=argparse.BooleanOptionalAction, default=True)
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
    dual_timeout = args.dual_task_timeout_seconds
    if dual_task_enabled and interactive:
        dual_interval = _prompt_int("Dual-task interval (seconds)", dual_interval, min_value=5)
        dual_timeout = _prompt_int("Dual-task timeout (seconds)", dual_timeout, min_value=1)
    elif dual_task_enabled:
        dual_interval = max(5, dual_interval)
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
        keyboard_tracking_enabled=args.keyboard_tracking_enabled,
        mouse_tracking_enabled=args.mouse_tracking_enabled,
    )

    config.data_dir.mkdir(parents=True, exist_ok=True)
    return config
