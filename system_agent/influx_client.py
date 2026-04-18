from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Dict, Iterable, List

import requests


def now_ns() -> int:
    """Return the current timestamp in nanoseconds."""
    return time.time_ns()


def _escape_measurement(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _escape_tag(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
        .replace("=", "\\=")
    )


def _escape_field_key(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _format_field_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value}i"
    if isinstance(value, float):
        if not math.isfinite(value):
            value = 0.0
        return f"{value:.6f}".rstrip("0").rstrip(".") or "0"

    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


class InfluxBatchClient:
    """Buffered InfluxDB v2 writer using line protocol."""

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        measurement: str = "behavior_events",
        batch_size: int = 100,
        flush_interval: float = 3.0,
        max_retries: int = 3,
        request_timeout: float = 10.0,
        max_buffer_lines: int = 5000,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.org = org
        self.bucket = bucket
        self.measurement = measurement
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_buffer_lines = max_buffer_lines

        self._write_url = f"{self.url}/api/v2/write"
        self._buffer: List[str] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="influx-batch-flusher",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._flush_thread.start()
        logging.info("Influx batch writer started with %.1fs interval", self.flush_interval)

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self._flush_thread.join(timeout=self.flush_interval + 1.0)
        self.flush()
        self._started = False
        logging.info("Influx batch writer stopped")

    def enqueue_event(self, event: Dict[str, Any]) -> None:
        line = self.event_to_line(event)
        if not line:
            return

        should_flush = False
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) > self.max_buffer_lines:
                # Keep the newest data if connection is down for a long time.
                self._buffer = self._buffer[-self.max_buffer_lines :]
            should_flush = len(self._buffer) >= self.batch_size

        if should_flush:
            self.flush()

    def enqueue_events(self, events: Iterable[Dict[str, Any]]) -> None:
        for event in events:
            self.enqueue_event(event)

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            lines = list(self._buffer)
            self._buffer.clear()

        payload = "\n".join(lines)
        if self._write_with_retry(payload):
            return

        logging.error("Influx write failed after retries; re-queueing %d lines", len(lines))
        with self._lock:
            self._buffer = lines + self._buffer
            if len(self._buffer) > self.max_buffer_lines:
                self._buffer = self._buffer[-self.max_buffer_lines :]

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(self.flush_interval):
            self.flush()

    def _write_with_retry(self, payload: str) -> bool:
        params = {
            "org": self.org,
            "bucket": self.bucket,
            "precision": "ns",
        }
        headers = {
            "Authorization": f"Token {self.token}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }

        backoff_seconds = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    self._write_url,
                    params=params,
                    headers=headers,
                    data=payload.encode("utf-8"),
                    timeout=self.request_timeout,
                )
                if 200 <= response.status_code < 300:
                    return True

                logging.warning(
                    "Influx write failed (attempt %d/%d): HTTP %s %s",
                    attempt,
                    self.max_retries,
                    response.status_code,
                    response.text.strip(),
                )
            except requests.RequestException as exc:
                logging.warning(
                    "Influx connection error (attempt %d/%d): %s",
                    attempt,
                    self.max_retries,
                    exc,
                )

            if attempt < self.max_retries:
                time.sleep(backoff_seconds)
                backoff_seconds *= 2

        return False

    def event_to_line(self, event: Dict[str, Any]) -> str:
        measurement = _escape_measurement(event.get("measurement", self.measurement))
        tags = event.get("tags", {})
        fields = event.get("fields", {})
        timestamp = int(event.get("timestamp", now_ns()))

        if not fields:
            return ""

        tag_part = ""
        if tags:
            rendered_tags = []
            for key, value in tags.items():
                if value is None:
                    continue
                rendered_tags.append(
                    f"{_escape_tag(str(key))}={_escape_tag(str(value))}"
                )
            if rendered_tags:
                tag_part = "," + ",".join(rendered_tags)

        field_pairs = []
        for key, value in fields.items():
            if value is None:
                continue
            field_pairs.append(
                f"{_escape_field_key(str(key))}={_format_field_value(value)}"
            )

        if not field_pairs:
            return ""

        field_part = ",".join(field_pairs)
        return f"{measurement}{tag_part} {field_part} {timestamp}"

