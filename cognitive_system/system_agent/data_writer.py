from __future__ import annotations

import csv
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import RuntimeConfig

LOGGER = logging.getLogger(__name__)

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
except ImportError:  # handled by startup dependency validation
    InfluxDBClient = None  # type: ignore[assignment]
    Point = None  # type: ignore[assignment]
    WritePrecision = None  # type: ignore[assignment]
    SYNCHRONOUS = None  # type: ignore[assignment]


def _as_epoch_seconds(value: Any) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.time()


class DataWriter:
    """Atomic writes to configured sinks (CSV and optional InfluxDB)."""

    _CSV_SCHEMAS = {
        "behavior": [
            "timestamp",         # epoch seconds (= end_time for context_end events)
            "session_id",
            "device_id",
            "event_type",
            "app_name",
            "window_title",      # OS-level window title
            "url",
            "title",
            "tab_id",
            "start_time",        # context open time  (epoch seconds, context_end only)
            "end_time",          # context close time (epoch seconds, context_end only)
            "scroll_delta_y",
            "scroll_total_y",
            "duration_ms",       # end_time - start_time in ms  (context_end only)
            "reaction_time_ms",
            "miss",
            "error",
            "extra",
        ],
        "dual_task": [
            "timestamp",
            "session_id",
            "device_id",
            "reaction_time_ms",
            "success",
            "error",
            "app_name",
        ],
        "keyboard": [
            "timestamp",
            "session_id",
            "device_id",
            "event_type",
            "key",
            "interval_ms",
        ],
        "mouse": [
            "timestamp",
            "session_id",
            "device_id",
            "event_type",
            "x",
            "y",
            "button",
            "delta_x",
            "delta_y",
            "speed",
        ],
        "labels": [
            "timestamp",
            "session_id",
            "device_id",
            "mental_demand",
            "physical_demand",
            "temporal_demand",
            "performance",
            "effort",
            "frustration",
            "stress_self_report",
            "valence",
            "arousal",
        ],
    }

    _STREAM_TO_BUCKET = {
        "behavior": "influxdb_behavior_bucket",
        "keyboard": "influxdb_keyboard_bucket",
        "mouse": "influxdb_mouse_bucket",
        "labels": "influxdb_behavior_bucket",
        "dual_task": "influxdb_behavior_bucket",
    }

    _STREAM_TO_MEASUREMENT = {
        "behavior": "behavior_event",
        "keyboard": "keyboard_event",
        "mouse": "mouse_event",
        "labels": "label_event",
        "dual_task": "dual_task_event",
    }

    def __init__(self, config: RuntimeConfig):
        self._config = config
        self._lock = threading.Lock()

        self._session_id: Optional[str] = None
        self._session_dir: Optional[Path] = None
        self._csv_writers: Dict[str, csv.DictWriter] = {}
        self._csv_handles: Dict[str, Any] = {}

        self._influx_client = None
        self._influx_writer = None
        if self._config.influx_enabled:
            self._init_influx()

    def _init_influx(self) -> None:
        if not self._config.influxdb_token:
            raise RuntimeError(
                "Influx export is enabled but INFLUXDB_TOKEN is empty. "
                "Set INFLUXDB_TOKEN or disable Influx export."
            )
        if InfluxDBClient is None:
            raise RuntimeError(
                "Influx export is enabled but influxdb-client is unavailable. "
                "Install it with `pip install influxdb-client`."
            )
        try:
            self._influx_client = InfluxDBClient(
                url=self._config.influxdb_url,
                token=self._config.influxdb_token,
                org=self._config.influxdb_org,
                timeout=5_000,
            )
            self._influx_writer = self._influx_client.write_api(write_options=SYNCHRONOUS)
            LOGGER.info("InfluxDB writer connected to %s", self._config.influxdb_url)
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize InfluxDB writer: {exc}") from exc

    def start_session(self, session_id: str) -> None:
        with self._lock:
            self._close_csv_handles_locked()
            self._session_id = session_id
            self._session_dir = self._config.data_dir / session_id
            self._session_dir.mkdir(parents=True, exist_ok=True)

            if self._config.csv_enabled:
                for stream, columns in self._CSV_SCHEMAS.items():
                    path = self._session_dir / f"{stream}.csv"
                    handle = path.open("w", newline="", encoding="utf-8")
                    writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                    writer.writeheader()
                    self._csv_handles[stream] = handle
                    self._csv_writers[stream] = writer
            LOGGER.info("Session outputs initialized at %s", self._session_dir)

    def end_session(self) -> None:
        with self._lock:
            self._close_csv_handles_locked()
            self._session_id = None
            self._session_dir = None

    def close(self) -> None:
        self.end_session()
        if self._influx_client:
            self._influx_client.close()

    def _close_csv_handles_locked(self) -> None:
        for handle in self._csv_handles.values():
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        self._csv_handles.clear()
        self._csv_writers.clear()

    def write_behavior_event(self, event: Dict[str, Any]) -> None:
        normalized = self._normalize_event(event)
        self._write_stream("behavior", normalized)

    def write_keyboard_event(self, event: Dict[str, Any]) -> None:
        normalized = self._normalize_event(event)
        self._write_stream("keyboard", normalized)

    def write_mouse_event(self, event: Dict[str, Any]) -> None:
        normalized = self._normalize_event(event)
        self._write_stream("mouse", normalized)

    def write_labels(self, labels: Dict[str, Any]) -> None:
        normalized = self._normalize_event(labels)
        self._write_stream("labels", normalized)

    def write_dual_task_event(self, event: Dict[str, Any]) -> None:
        """Write a dual-task probe result to dual_task.csv (never to behavior.csv)."""
        normalized = self._normalize_event(event)
        self._write_stream("dual_task", normalized)

    def _normalize_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(event)
        payload["timestamp"] = _as_epoch_seconds(payload.get("timestamp"))
        payload.setdefault("session_id", self._session_id)
        payload.setdefault("device_id", self._config.device_id)
        return payload

    def _write_stream(self, stream: str, event: Dict[str, Any]) -> None:
        self._write_csv(stream, event)
        self._write_influx(stream, event)

    def _write_csv(self, stream: str, event: Dict[str, Any]) -> None:
        if not self._config.csv_enabled:
            return
        with self._lock:
            writer = self._csv_writers.get(stream)
            handle = self._csv_handles.get(stream)
            if not writer or not handle:
                return

            row = dict(event)
            if stream == "behavior":
                row["extra"] = self._build_behavior_extra(event)

            writer.writerow(row)
            handle.flush()

    def _build_behavior_extra(self, event: Dict[str, Any]) -> str:
        # If the caller already set a non-empty `extra` (e.g. parsed VSCode filename),
        # preserve it instead of overwriting with an auto-computed JSON string.
        existing = event.get("extra")
        if existing is not None and str(existing).strip():
            return str(existing)
        # Fall back: serialize any unknown fields into a JSON blob.
        known = set(self._CSV_SCHEMAS["behavior"])
        extras = {
            key: value
            for key, value in event.items()
            if key not in known and value not in ("", None)
        }
        if not extras:
            return ""
        return json.dumps(extras, ensure_ascii=True, separators=(",", ":"))

    def _write_influx(self, stream: str, event: Dict[str, Any]) -> None:
        if not self._config.influx_enabled:
            return
        if not self._influx_writer or not Point or not WritePrecision:
            return

        measurement = self._STREAM_TO_MEASUREMENT[stream]
        bucket_attr = self._STREAM_TO_BUCKET[stream]
        bucket = getattr(self._config, bucket_attr)
        timestamp_ns = int(_as_epoch_seconds(event.get("timestamp")) * 1_000_000_000)

        point = Point(measurement)
        for tag_key in ("session_id", "device_id", "event_type"):
            tag_value = event.get(tag_key)
            if tag_value not in (None, ""):
                point.tag(tag_key, str(tag_value))

        for field_key, field_value in event.items():
            if field_key in {"timestamp", "session_id", "device_id", "event_type"}:
                continue
            if field_value in (None, ""):
                continue
            if isinstance(field_value, bool):
                point.field(field_key, field_value)
            elif isinstance(field_value, int):
                point.field(field_key, int(field_value))
            elif isinstance(field_value, float):
                point.field(field_key, float(field_value))
            else:
                point.field(field_key, str(field_value))

        point.time(timestamp_ns, WritePrecision.NS)
        self._influx_writer.write(
            bucket=bucket,
            org=self._config.influxdb_org,
            record=point,
        )
