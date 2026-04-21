"""Session-based CSV export."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Dict

LOGGER = logging.getLogger(__name__)


@dataclass
class _CsvChannel:
    file_handle: Any
    writer: csv.DictWriter


class CsvExporter:
    """Writes synchronized event streams into per-session CSV files."""

    _CHANNEL_HEADERS = {
        "behavior": [
            "timestamp",
            "timestamp_ns",
            "session_id",
            "device_id",
            "browser_id",
            "event_type",
            "active_app",
            "active_url",
            "domain",
            "path",
            "payload_json",
        ],
        "keyboard": [
            "timestamp",
            "timestamp_ns",
            "session_id",
            "device_id",
            "event_type",
            "key_event_type",
            "key_code",
            "active_app",
            "active_url",
            "payload_json",
        ],
        "mouse": [
            "timestamp",
            "timestamp_ns",
            "session_id",
            "device_id",
            "event_type",
            "x",
            "y",
            "velocity",
            "click",
            "button",
            "active_app",
            "active_url",
            "payload_json",
        ],
        "labels": [
            "timestamp",
            "timestamp_ns",
            "session_id",
            "device_id",
            "event_type",
            "mental_demand",
            "physical_demand",
            "temporal_demand",
            "performance",
            "effort",
            "frustration",
            "stress_self_report",
            "valence",
            "arousal",
            "payload_json",
        ],
    }

    _STREAM_TO_FILE = {
        "behavior": "behavior.csv",
        "keyboard": "keyboard.csv",
        "mouse": "mouse.csv",
        "labels": "labels.csv",
    }

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root
        self._data_root.mkdir(parents=True, exist_ok=True)
        self._session_dir: Path | None = None
        self._channels: Dict[str, _CsvChannel] = {}
        self._lock = Lock()

    @property
    def session_dir(self) -> Path | None:
        return self._session_dir

    def start_session(self, session_id: str) -> Path:
        with self._lock:
            self.close_session()
            self._session_dir = self._data_root / session_id
            self._session_dir.mkdir(parents=True, exist_ok=True)
            for stream, filename in self._STREAM_TO_FILE.items():
                file_path = self._session_dir / filename
                file_handle = file_path.open("w", newline="", encoding="utf-8")
                writer = csv.DictWriter(file_handle, fieldnames=self._CHANNEL_HEADERS[stream])
                writer.writeheader()
                self._channels[stream] = _CsvChannel(file_handle=file_handle, writer=writer)

            LOGGER.info("CSV export initialized at %s", self._session_dir)
            return self._session_dir

    def close_session(self) -> None:
        with self._lock:
            for channel in self._channels.values():
                channel.file_handle.flush()
                channel.file_handle.close()
            self._channels.clear()
            self._session_dir = None

    def write_event(self, stream: str, event: Dict[str, Any]) -> None:
        if stream not in self._STREAM_TO_FILE:
            raise ValueError(f"Unknown CSV stream={stream!r}")

        with self._lock:
            channel = self._channels.get(stream)
            if not channel:
                return

            headers = self._CHANNEL_HEADERS[stream]
            row = {key: event.get(key, "") for key in headers}
            payload = {
                key: value
                for key, value in event.items()
                if key not in headers and value is not None
            }
            row["payload_json"] = json.dumps(payload, separators=(",", ":")) if payload else ""
            channel.writer.writerow(row)
            channel.file_handle.flush()

