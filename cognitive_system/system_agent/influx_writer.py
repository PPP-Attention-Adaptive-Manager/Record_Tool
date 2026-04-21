"""InfluxDB line protocol writer."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable

from shared.time_utils import now_ns

try:
    from influxdb_client import InfluxDBClient
    from influxdb_client.client.write_api import SYNCHRONOUS
except ImportError:  # pragma: no cover - optional runtime dependency
    InfluxDBClient = None  # type: ignore[assignment]
    SYNCHRONOUS = None  # type: ignore[assignment]

from .config import InfluxSettings

LOGGER = logging.getLogger(__name__)


STREAM_TO_BUCKET = {
    "behavior": "behavior_bucket",
    "labels": "behavior_bucket",
    "keyboard": "keyboard_bucket",
    "mouse": "mouse_bucket",
}

STREAM_TO_MEASUREMENT = {
    "behavior": "behavior_event",
    "labels": "label_event",
    "keyboard": "keyboard_event",
    "mouse": "mouse_event",
}

TAG_KEYS = {"session_id", "device_id", "browser_id", "event_type"}
EXCLUDE_KEYS = {"timestamp", "timestamp_ns", "session_id", "device_id", "browser_id"}


def _escape_key(value: str) -> str:
    return value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _escape_tag_value(value: str) -> str:
    return _escape_key(value)


def _escape_string_field(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _field_to_lp(key: str, value: Any) -> str | None:
    escaped_key = _escape_key(key)
    if isinstance(value, bool):
        return f"{escaped_key}={'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{escaped_key}={value}i"
    if isinstance(value, float):
        return f"{escaped_key}={value}"
    if isinstance(value, (dict, list)):
        return f"{escaped_key}={_escape_string_field(json.dumps(value, separators=(',', ':')))}"
    if value is None:
        return None
    return f"{escaped_key}={_escape_string_field(str(value))}"


class InfluxWriter:
    """Writes one complete event per line-protocol record."""

    def __init__(self, settings: InfluxSettings) -> None:
        self._settings = settings
        self._enabled = bool(settings.token and settings.url and settings.org and InfluxDBClient)
        self._client = None
        self._writer = None

        if self._enabled:
            self._client = InfluxDBClient(url=settings.url, token=settings.token, org=settings.org)
            self._writer = self._client.write_api(write_options=SYNCHRONOUS)
            LOGGER.info("InfluxDB writer enabled for org=%s url=%s", settings.org, settings.url)
        else:
            LOGGER.warning(
                "InfluxDB writer disabled. Set INFLUX_URL/INFLUX_TOKEN/INFLUX_ORG and install influxdb-client."
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def close(self) -> None:
        if self._client:
            self._client.close()

    def write_event(self, stream: str, event: Dict[str, Any]) -> None:
        bucket_attr = STREAM_TO_BUCKET.get(stream)
        measurement = STREAM_TO_MEASUREMENT.get(stream)
        if not bucket_attr or not measurement:
            raise ValueError(f"Unknown stream={stream!r}")

        bucket = getattr(self._settings, bucket_attr)
        timestamp_ns = int(event.get("timestamp_ns") or now_ns())
        line = self._to_line_protocol(measurement=measurement, timestamp_ns=timestamp_ns, event=event)

        if not self._enabled:
            return

        try:
            assert self._writer is not None
            self._writer.write(bucket=bucket, org=self._settings.org, record=line)
        except Exception as exc:  # pragma: no cover - network/influx errors
            LOGGER.exception("Influx write failed for stream=%s: %s", stream, exc)

    def _to_line_protocol(self, measurement: str, timestamp_ns: int, event: Dict[str, Any]) -> str:
        tags = []
        for key in TAG_KEYS:
            value = event.get(key)
            if value is None or value == "":
                continue
            tags.append(f"{_escape_key(key)}={_escape_tag_value(str(value))}")

        fields: Iterable[str | None] = (
            _field_to_lp(key, value)
            for key, value in event.items()
            if key not in EXCLUDE_KEYS and key not in TAG_KEYS and value is not None
        )
        field_values = [item for item in fields if item]
        if not field_values:
            field_values = ["value=1i"]

        tag_part = f",{','.join(tags)}" if tags else ""
        return f"{_escape_key(measurement)}{tag_part} {','.join(field_values)} {timestamp_ns}"

