import os
import csv
import time
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    INFLUXDB_AVAILABLE = True
except ImportError:
    INFLUXDB_AVAILABLE = False
    logger.warning("influxdb-client not installed - InfluxDB writes disabled")


class DataWriter:
    """Writes all events to InfluxDB and session CSV files. One event = one write."""

    _CSV_SCHEMAS = {
        "behavior": [
            "timestamp", "session_id", "device_id", "event_type",
            "url", "title", "tab_id",
            "scroll_delta_y", "scroll_total_y", "duration_ms", "extra",
        ],
        "keyboard": [
            "timestamp", "session_id", "device_id", "event_type",
            "key", "key_code", "modifiers", "interval_ms",
        ],
        "mouse": [
            "timestamp", "session_id", "device_id", "event_type",
            "x", "y", "button", "delta_x", "delta_y", "speed",
        ],
        "labels": [
            "timestamp", "session_id", "device_id",
            "mental_demand", "physical_demand", "temporal_demand",
            "performance", "effort", "frustration",
            "stress_self_report", "valence", "arousal", "notes",
        ],
    }

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._session_id: Optional[str] = None
        self._csv_writers: Dict[str, csv.DictWriter] = {}
        self._csv_files: Dict[str, Any] = {}
        self._influx_client = None
        self._write_api = None
        if INFLUXDB_AVAILABLE:
            self._init_influxdb()

    def _init_influxdb(self):
        try:
            self._influx_client = InfluxDBClient(
                url=self.config.influxdb_url,
                token=self.config.influxdb_token,
                org=self.config.influxdb_org,
                timeout=5_000,
            )
            self._write_api = self._influx_client.write_api(write_options=SYNCHRONOUS)
            logger.info("InfluxDB client initialized")
        except Exception as exc:
            logger.error(f"InfluxDB init failed: {exc}")
            self._influx_client = None
            self._write_api = None

    def start_session(self, session_id: str):
        with self._lock:
            self._session_id = session_id
            session_dir = os.path.join(self.config.data_dir, session_id)
            os.makedirs(session_dir, exist_ok=True)
            for name, headers in self._CSV_SCHEMAS.items():
                path = os.path.join(session_dir, f"{name}.csv")
                f = open(path, "w", newline="", encoding="utf-8")
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()
                self._csv_files[name] = f
                self._csv_writers[name] = writer
            logger.info(f"CSV files opened for session: {session_id}")

    def end_session(self):
        with self._lock:
            for f in self._csv_files.values():
                try:
                    f.flush(); f.close()
                except Exception:
                    pass
            self._csv_files.clear()
            self._csv_writers.clear()
            self._session_id = None

    def write_behavior_event(self, event: dict):
        self._inject_ids(event)
        self._write_csv("behavior", event)
        self._influx_write_behavior(event)

    def write_keyboard_event(self, event: dict):
        self._inject_ids(event)
        self._write_csv("keyboard", event)
        self._influx_write_keyboard(event)

    def write_mouse_event(self, event: dict):
        self._inject_ids(event)
        self._write_csv("mouse", event)
        self._influx_write_mouse(event)

    def write_labels(self, labels: dict):
        self._inject_ids(labels)
        labels.setdefault("timestamp", time.time())
        self._write_csv("labels", labels)
        logger.info(f"Labels saved for session {self._session_id}")

    def _inject_ids(self, event: dict):
        event.setdefault("session_id", self._session_id)
        event.setdefault("device_id", self.config.device_id)

    def _write_csv(self, name: str, row: dict):
        with self._lock:
            writer = self._csv_writers.get(name)
            if writer is None:
                return
            try:
                writer.writerow(row)
                self._csv_files[name].flush()
            except Exception as exc:
                logger.error(f"CSV write error ({name}): {exc}")

    def _influx_write_behavior(self, event: dict):
        if not self._write_api:
            return
        try:
            ns = int(event.get("timestamp", time.time()) * 1e9)
            point = (
                Point("browser_event")
                .tag("session_id", event.get("session_id", ""))
                .tag("device_id", event.get("device_id", ""))
                .tag("event_type", event.get("event_type", ""))
                .field("url", str(event.get("url", "")))
                .field("title", str(event.get("title", "")))
                .field("scroll_delta_y", float(event.get("scroll_delta_y", 0)))
                .field("scroll_total_y", float(event.get("scroll_total_y", 0)))
                .field("duration_ms", float(event.get("duration_ms", 0)))
                .time(ns, WritePrecision.NANOSECONDS)
            )
            self._write_api.write(bucket=self.config.influxdb_behavior_bucket,
                                  org=self.config.influxdb_org, record=point)
        except Exception as exc:
            logger.debug(f"InfluxDB behavior write: {exc}")

    def _influx_write_keyboard(self, event: dict):
        if not self._write_api:
            return
        try:
            ns = int(event.get("timestamp", time.time()) * 1e9)
            point = (
                Point("keyboard_event")
                .tag("session_id", event.get("session_id", ""))
                .tag("device_id", event.get("device_id", ""))
                .tag("event_type", event.get("event_type", ""))
                .field("key", str(event.get("key", "")))
                .field("interval_ms", float(event.get("interval_ms", 0)))
                .time(ns, WritePrecision.NANOSECONDS)
            )
            self._write_api.write(bucket=self.config.influxdb_keyboard_bucket,
                                  org=self.config.influxdb_org, record=point)
        except Exception as exc:
            logger.debug(f"InfluxDB keyboard write: {exc}")

    def _influx_write_mouse(self, event: dict):
        if not self._write_api:
            return
        try:
            ns = int(event.get("timestamp", time.time()) * 1e9)
            point = (
                Point("mouse_event")
                .tag("session_id", event.get("session_id", ""))
                .tag("device_id", event.get("device_id", ""))
                .tag("event_type", event.get("event_type", ""))
                .field("x", float(event.get("x", 0)))
                .field("y", float(event.get("y", 0)))
                .field("speed", float(event.get("speed", 0)))
                .time(ns, WritePrecision.NANOSECONDS)
            )
            self._write_api.write(bucket=self.config.influxdb_mouse_bucket,
                                  org=self.config.influxdb_org, record=point)
        except Exception as exc:
            logger.debug(f"InfluxDB mouse write: {exc}")

    def close(self):
        self.end_session()
        if self._influx_client:
            try:
                self._influx_client.close()
            except Exception:
                pass
