from __future__ import annotations

import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import SessionController, create_app  # noqa: E402


def _client(tmp_path: Path):
    controller = SessionController(
        data_dir=tmp_path / "data",
        schema_path=ROOT / "schemas" / "event_schema.json",
    )
    app = create_app(controller)
    app.testing = True
    return app.test_client()


def test_health_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "healthy"
    assert body["version"] == "1.0.0"


def test_session_start_events_and_stop(tmp_path: Path) -> None:
    client = _client(tmp_path)

    start = client.post(
        "/session/start",
        json={"user_id": "P001", "duration_minutes": 30, "enable_influx": False},
    )
    assert start.status_code == 200
    started = start.get_json()
    assert started["session_id"].startswith("sess_")

    event = {
        "event_id": "evt_deadbeef",
        "event_type": "switch",
        "timestamp": 1713725422123,
        "duration_since_last_event": 45.2,
        "source": "browser",
        "tab_id": 42,
        "window_id": 1,
        "full_url": "https://github.com/user/repo",
        "domain": "github.com",
        "path": "/user/repo",
        "query_string": "",
        "title": "Repository",
        "scroll_delta_cumulative": 0,
        "scroll_depth_last": 0.0,
        "scroll_depth_max": 0.0,
        "scroll_event_count": 0,
        "tab_active": True,
        "visibility_state": "visible",
        "chrome_in_foreground": True,
        "site_type": "development",
        "task_hint": "coding",
        "user_id": "P001",
    }

    ingest = client.post(
        "/events",
        json={"session_id": started["session_id"], "events": [event]},
    )
    assert ingest.status_code == 200
    assert ingest.get_json()["accepted"] == 1

    stop = client.post("/session/stop", json={"session_id": started["session_id"]})
    assert stop.status_code == 200
    stopped = stop.get_json()
    assert stopped["session_id"] == started["session_id"]
    assert stopped["events_written"] >= 3

    csv_path = Path(started["csv_path"])
    session_dir = Path(started["session_dir"])
    keyboard_csv_path = Path(started["keyboard_csv_path"])
    mouse_csv_path = Path(started["mouse_csv_path"])
    assert session_dir.exists()
    assert csv_path.exists()
    assert keyboard_csv_path.exists()
    assert mouse_csv_path.exists()
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["event_type"] for row in rows] == ["start_session", "switch", "end_session"]


def test_invalid_event_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    start = client.post("/session/start", json={"user_id": "P001", "duration_minutes": 30})
    session_id = start.get_json()["session_id"]

    bad_event = {
        "event_id": "evt_deadbeef",
        "event_type": "switch",
        "timestamp": 1713725422123,
        "user_id": "P001",
        "session_id": session_id,
        "source": "browser",
        "scroll_depth_last": 3,
    }

    response = client.post("/events", json={"session_id": session_id, "events": [bad_event]})
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "invalid_payload"
