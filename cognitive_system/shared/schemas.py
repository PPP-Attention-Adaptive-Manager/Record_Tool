"""
Canonical field schemas used by both system agent and tests.
Each schema lists the exact CSV column order for its file type.
"""
from typing import Dict, List

BEHAVIOR_FIELDS: List[str] = [
    "timestamp", "session_id", "device_id", "event_type",
    "url", "title", "tab_id",
    "scroll_delta_y", "scroll_total_y", "duration_ms", "extra",
]

KEYBOARD_FIELDS: List[str] = [
    "timestamp", "session_id", "device_id", "event_type",
    "key", "key_code", "modifiers", "interval_ms",
]

MOUSE_FIELDS: List[str] = [
    "timestamp", "session_id", "device_id", "event_type",
    "x", "y", "button", "delta_x", "delta_y", "speed",
]

LABEL_FIELDS: List[str] = [
    "timestamp", "session_id", "device_id",
    "mental_demand", "physical_demand", "temporal_demand",
    "performance", "effort", "frustration",
    "stress_self_report", "valence", "arousal", "notes",
]

ALL_SCHEMAS: Dict[str, List[str]] = {
    "behavior": BEHAVIOR_FIELDS,
    "keyboard": KEYBOARD_FIELDS,
    "mouse": MOUSE_FIELDS,
    "labels": LABEL_FIELDS,
}
