from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


CSV_COLUMNS = [
    "session_id",
    "user_id",
    "event_id",
    "event_type",
    "timestamp",
    "duration_since_last_event",
    "source",
    "tab_id",
    "window_id",
    "full_url",
    "domain",
    "path",
    "query_string",
    "title",
    "scroll_delta_cumulative",
    "scroll_depth_last",
    "scroll_depth_max",
    "scroll_event_count",
    "tab_active",
    "visibility_state",
    "chrome_in_foreground",
    "site_type",
    "task_hint",
    "app_name",
    "window_title",
    "duration",
    "reaction_time",
    "dual_task_success",
    "dual_task_error",
    "missed_response",
    "mental_demand",
    "physical_demand",
    "temporal_demand",
    "performance",
    "effort",
    "frustration",
    "stress_self_report",
    "valence",
    "arousal",
    "input_device",
    "input_action",
    "key_value",
    "button",
    "pressed",
    "pointer_x",
    "pointer_y",
    "wheel_delta_x",
    "wheel_delta_y",
]

BOOLEAN_COLUMNS = {
    "tab_active",
    "chrome_in_foreground",
}

SYSTEM_EVENT_TYPES = {"start_session", "end_session", "app_focus"}
INPUT_EVENT_TYPES = {"keyboard_input", "mouse_input"}
DUAL_TASK_EVENT_TYPES = {"dual_task"}
QUESTIONNAIRE_EVENT_TYPES = {"questionnaire"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_ms() -> int:
    return int(utc_now().timestamp() * 1000)


def generate_session_id(when: datetime | None = None) -> str:
    instant = when or utc_now()
    return f"sess_{instant.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def generate_event_id() -> str:
    return f"evt_{secrets.token_hex(4)}"


def _escape_measurement(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _escape_tag(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
        .replace("=", "\\=")
    )


def _escape_field_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_float(value: Any) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.0"


def _as_int_bool(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return 1 if bool(value) else 0


@dataclass(slots=True)
class UnifiedEvent:
    payload: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UnifiedEvent":
        normalized = {column: payload.get(column) for column in CSV_COLUMNS}
        normalized["event_id"] = payload.get("event_id") or generate_event_id()
        normalized["timestamp"] = payload.get("timestamp") or now_ms()
        return cls(normalized)

    def to_csv_row(self) -> dict[str, Any]:
        row = {column: self.payload.get(column) for column in CSV_COLUMNS}
        for column in BOOLEAN_COLUMNS:
            row[column] = _as_int_bool(row.get(column))
        return row

    def to_influx_line(self) -> str:
        event_type = self.payload.get("event_type") or "unknown"
        timestamp_ms = int(self.payload.get("timestamp") or now_ms())
        timestamp_ns = timestamp_ms * 1_000_000

        if event_type in DUAL_TASK_EVENT_TYPES:
            return self._dual_task_line(timestamp_ns)
        if event_type in QUESTIONNAIRE_EVENT_TYPES:
            return self._questionnaire_line(timestamp_ns)
        if self.payload.get("source") == "system" or event_type in SYSTEM_EVENT_TYPES | INPUT_EVENT_TYPES:
            return self._system_line(timestamp_ns)
        return self._behavior_line(timestamp_ns)

    def _behavior_line(self, timestamp_ns: int) -> str:
        tags = {
            "session_id": self.payload.get("session_id") or "none",
            "user_id": self.payload.get("user_id") or "anonymous",
            "event_type": self.payload.get("event_type") or "unknown",
            "domain": self.payload.get("domain") or "unknown",
            "site_type": self.payload.get("site_type") or "unknown",
            "task_hint": self.payload.get("task_hint") or "unknown",
        }
        fields = {
            "event_id": self.payload.get("event_id") or "",
            "full_url": self.payload.get("full_url") or "",
            "path": self.payload.get("path") or "",
            "query_string": self.payload.get("query_string") or "",
            "title": self.payload.get("title") or "",
            "visibility_state": self.payload.get("visibility_state") or "visible",
            "duration_since_last_event": float(self.payload.get("duration_since_last_event") or 0.0),
            "scroll_delta_cumulative": float(self.payload.get("scroll_delta_cumulative") or 0.0),
            "scroll_depth_last": float(self.payload.get("scroll_depth_last") or 0.0),
            "scroll_depth_max": float(self.payload.get("scroll_depth_max") or 0.0),
            "scroll_event_count": int(self.payload.get("scroll_event_count") or 0),
            "tab_active": _as_int_bool(self.payload.get("tab_active")) or 0,
            "chrome_in_foreground": _as_int_bool(self.payload.get("chrome_in_foreground")) or 0,
        }
        if self.payload.get("tab_id") is not None:
            fields["tab_id"] = int(self.payload["tab_id"])
        if self.payload.get("window_id") is not None:
            fields["window_id"] = int(self.payload["window_id"])
        return self._line_protocol("behavior_events", tags, fields, timestamp_ns)

    def _system_line(self, timestamp_ns: int) -> str:
        tags = {
            "session_id": self.payload.get("session_id") or "none",
            "user_id": self.payload.get("user_id") or "anonymous",
            "event_type": self.payload.get("event_type") or "unknown",
            "app_name": self.payload.get("app_name") or "unknown",
        }
        fields = {
            "event_id": self.payload.get("event_id") or "",
            "window_title": self.payload.get("window_title") or "",
            "duration": float(self.payload.get("duration") or 0.0),
        }
        if self.payload.get("input_device"):
            fields["input_device"] = self.payload.get("input_device") or ""
        if self.payload.get("input_action"):
            fields["input_action"] = self.payload.get("input_action") or ""
        if self.payload.get("key_value"):
            fields["key_value"] = self.payload.get("key_value") or ""
        if self.payload.get("button"):
            fields["button"] = self.payload.get("button") or ""
        if self.payload.get("pressed") is not None:
            fields["pressed"] = _as_int_bool(self.payload.get("pressed")) or 0
        if self.payload.get("pointer_x") is not None:
            fields["pointer_x"] = int(self.payload.get("pointer_x") or 0)
        if self.payload.get("pointer_y") is not None:
            fields["pointer_y"] = int(self.payload.get("pointer_y") or 0)
        if self.payload.get("wheel_delta_x") is not None:
            fields["wheel_delta_x"] = int(self.payload.get("wheel_delta_x") or 0)
        if self.payload.get("wheel_delta_y") is not None:
            fields["wheel_delta_y"] = int(self.payload.get("wheel_delta_y") or 0)
        return self._line_protocol("system_events", tags, fields, timestamp_ns)

    def _dual_task_line(self, timestamp_ns: int) -> str:
        tags = {
            "session_id": self.payload.get("session_id") or "none",
            "user_id": self.payload.get("user_id") or "anonymous",
        }
        fields = {
            "event_id": self.payload.get("event_id") or "",
            "reaction_time": int(self.payload.get("reaction_time") or -1),
            "dual_task_success": int(self.payload.get("dual_task_success") or 0),
            "dual_task_error": int(self.payload.get("dual_task_error") or 0),
            "missed_response": int(self.payload.get("missed_response") or 0),
        }
        return self._line_protocol("dual_task_events", tags, fields, timestamp_ns)

    def _questionnaire_line(self, timestamp_ns: int) -> str:
        tags = {
            "session_id": self.payload.get("session_id") or "none",
            "user_id": self.payload.get("user_id") or "anonymous",
        }
        fields = {
            "event_id": self.payload.get("event_id") or "",
            "mental_demand": int(self.payload.get("mental_demand") or 0),
            "physical_demand": int(self.payload.get("physical_demand") or 0),
            "temporal_demand": int(self.payload.get("temporal_demand") or 0),
            "performance": int(self.payload.get("performance") or 0),
            "effort": int(self.payload.get("effort") or 0),
            "frustration": int(self.payload.get("frustration") or 0),
            "stress_self_report": int(self.payload.get("stress_self_report") or 0),
            "valence": int(self.payload.get("valence") or 0),
            "arousal": int(self.payload.get("arousal") or 0),
        }
        return self._line_protocol("questionnaire_events", tags, fields, timestamp_ns)

    def _line_protocol(
        self,
        measurement: str,
        tags: dict[str, str],
        fields: dict[str, Any],
        timestamp_ns: int,
    ) -> str:
        tag_str = ",".join(
            f"{_escape_tag(str(key))}={_escape_tag(str(value))}"
            for key, value in tags.items()
        )
        field_parts: list[str] = []
        for key, value in fields.items():
            if isinstance(value, str):
                field_parts.append(
                    f'{_escape_tag(str(key))}="{_escape_field_string(value)}"'
                )
            elif isinstance(value, int):
                field_parts.append(f"{_escape_tag(str(key))}={value}i")
            else:
                field_parts.append(f"{_escape_tag(str(key))}={_format_float(value)}")
        return f"{_escape_measurement(measurement)},{tag_str} {','.join(field_parts)} {timestamp_ns}"
