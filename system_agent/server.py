from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request
from jsonschema import Draft7Validator

from csv_writer import CsvEventWriter
from influx_client import InfluxBatchClient
from models import UnifiedEvent, generate_event_id, generate_session_id, now_ms, utc_now


APP_VERSION = "1.0.0"


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details

    def to_response(self) -> tuple[dict[str, Any], int]:
        payload = {"error": self.code, "message": self.message}
        payload.update(self.details)
        return payload, self.status_code


@dataclass(slots=True)
class ActiveSession:
    session_id: str
    user_id: str
    started_at: datetime
    expires_at: datetime
    session_dir: Path
    events_csv_path: Path
    keyboard_csv_path: Path
    mouse_csv_path: Path
    events_writer: CsvEventWriter
    keyboard_writer: CsvEventWriter
    mouse_writer: CsvEventWriter
    enable_influx: bool
    events_written: int = 0


class SessionController:
    def __init__(
        self,
        data_dir: Path,
        schema_path: Path,
        influx_client: InfluxBatchClient | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.influx_client = influx_client
        self.started_at = time.monotonic()
        self._session: ActiveSession | None = None
        self._completed_sessions: dict[str, ActiveSession] = {}
        self._lock = threading.RLock()
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        self._validator = Draft7Validator(schema)

    def start_session(
        self,
        user_id: str,
        duration_minutes: int = 60,
        enable_influx: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self._session is not None:
                raise SessionError(
                    "session_active",
                    "Stop current session before starting new one",
                    409,
                    current_session_id=self._session.session_id,
                )

            now = utc_now()
            session_id = generate_session_id(now)
            session_dir = self.data_dir / session_id
            events_csv_path = session_dir / "events.csv"
            keyboard_csv_path = session_dir / "keyboard.csv"
            mouse_csv_path = session_dir / "mouse.csv"
            active = ActiveSession(
                session_id=session_id,
                user_id=user_id,
                started_at=now,
                expires_at=now + timedelta(minutes=duration_minutes),
                session_dir=session_dir,
                events_csv_path=events_csv_path,
                keyboard_csv_path=keyboard_csv_path,
                mouse_csv_path=mouse_csv_path,
                events_writer=CsvEventWriter(events_csv_path),
                keyboard_writer=CsvEventWriter(keyboard_csv_path),
                mouse_writer=CsvEventWriter(mouse_csv_path),
                enable_influx=enable_influx,
            )
            self._session = active

            self._append_event(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "event_id": generate_event_id(),
                    "event_type": "start_session",
                    "timestamp": now_ms(),
                    "source": "system",
                },
                validate=True,
            )
            return {
                "session_id": active.session_id,
                "started_at": active.started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "expires_at": active.expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "session_dir": str(active.session_dir),
                "csv_path": str(active.events_csv_path),
                "events_csv_path": str(active.events_csv_path),
                "keyboard_csv_path": str(active.keyboard_csv_path),
                "mouse_csv_path": str(active.mouse_csv_path),
            }

    def stop_session(self, session_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            active = self._require_active_session()
            if session_id and session_id != active.session_id:
                raise SessionError(
                    "session_mismatch",
                    "Requested session does not match active session",
                    409,
                )

            stopped_at = utc_now()
            duration_seconds = max(
                0.0,
                (stopped_at - active.started_at).total_seconds(),
            )
            self._append_event(
                {
                    "session_id": active.session_id,
                    "user_id": active.user_id,
                    "event_id": generate_event_id(),
                    "event_type": "end_session",
                    "timestamp": now_ms(),
                    "source": "system",
                    "duration": duration_seconds,
                },
                validate=True,
            )

            response = {
                "session_id": active.session_id,
                "stopped_at": stopped_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "duration_seconds": duration_seconds,
                "events_written": active.events_written,
            }
            self._completed_sessions[active.session_id] = active
            self._session = None
            return response

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            if self._session is None:
                return {"active": False}

            return {
                "active": True,
                "session_id": self._session.session_id,
                "started_at": self._session.started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "expires_at": self._session.expires_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "events_received": self._session.events_written,
                "session_dir": str(self._session.session_dir),
                "csv_path": str(self._session.events_csv_path),
                "events_csv_path": str(self._session.events_csv_path),
                "keyboard_csv_path": str(self._session.keyboard_csv_path),
                "mouse_csv_path": str(self._session.mouse_csv_path),
                "enable_influx": self._session.enable_influx,
            }

    def health(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "version": APP_VERSION,
            "uptime_seconds": round(time.monotonic() - self.started_at, 3),
        }

    def ingest_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = payload.get("session_id")
        events = payload.get("events")
        if not isinstance(events, list) or not events:
            raise SessionError(
                "invalid_payload",
                "Request body must include a non-empty events array",
                400,
            )
        if len(events) > 50:
            raise SessionError(
                "invalid_payload",
                "Batch size cannot exceed 50 events",
                400,
            )

        accepted = 0
        resolved_session_id = session_id
        for index, raw_event in enumerate(events):
            if not isinstance(raw_event, dict):
                raise SessionError(
                    "invalid_payload",
                    f"Event at index {index} must be an object",
                    400,
                )
            event = dict(raw_event)
            event.setdefault("session_id", session_id)
            try:
                target_session = self._append_event(event, validate=True)
            except SessionError as exc:
                if exc.code == "invalid_payload":
                    raise SessionError(
                        "invalid_payload",
                        exc.message,
                        400,
                        details=f"{exc.message} at index {index}",
                    ) from exc
                raise
            accepted += 1
            resolved_session_id = target_session.session_id

        return {"accepted": accepted, "session_id": resolved_session_id}

    def append_system_event(self, event: dict[str, Any]) -> bool:
        with self._lock:
            if self._session is None:
                return False
        self._append_event(event, validate=True)
        return True

    def build_system_focus_event(
        self,
        app_name: str,
        window_title: str,
        duration: float,
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._session is None:
                return None
            return {
                "session_id": self._session.session_id,
                "user_id": self._session.user_id,
                "event_id": generate_event_id(),
                "event_type": "app_focus",
                "timestamp": now_ms(),
                "duration_since_last_event": duration,
                "source": "system",
                "app_name": app_name or "unknown",
                "window_title": window_title or "",
                "duration": duration,
            }

    def build_system_input_event(
        self,
        *,
        event_type: str,
        app_name: str,
        window_title: str,
        input_device: str,
        input_action: str,
        key_value: str | None = None,
        button: str | None = None,
        pressed: bool | None = None,
        pointer_x: int | None = None,
        pointer_y: int | None = None,
        wheel_delta_x: int | None = None,
        wheel_delta_y: int | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._session is None:
                return None
            return {
                "session_id": self._session.session_id,
                "user_id": self._session.user_id,
                "event_id": generate_event_id(),
                "event_type": event_type,
                "timestamp": now_ms(),
                "source": "system",
                "app_name": app_name or "unknown",
                "window_title": window_title or "",
                "input_device": input_device,
                "input_action": input_action,
                "key_value": key_value,
                "button": button,
                "pressed": pressed,
                "pointer_x": pointer_x,
                "pointer_y": pointer_y,
                "wheel_delta_x": wheel_delta_x,
                "wheel_delta_y": wheel_delta_y,
            }

    def _append_event(self, payload: dict[str, Any], validate: bool) -> ActiveSession:
        with self._lock:
            target_session = self._resolve_target_session(payload)
            if payload.get("session_id") != target_session.session_id:
                raise SessionError(
                    "session_mismatch",
                    "Event session_id does not match active session",
                    409,
                )
            if payload.get("user_id") != target_session.user_id:
                raise SessionError(
                    "invalid_payload",
                    "Event user_id does not match active session user",
                    400,
                )
            if validate:
                self._validate_event(payload)

            event = UnifiedEvent.from_payload(payload)
            self._writer_for_event(target_session, event).append_event(event)
            target_session.events_written += 1

            if target_session.enable_influx and self.influx_client is not None:
                self.influx_client.enqueue_line(event.to_influx_line())
            return target_session

    def _writer_for_event(self, session: ActiveSession, event: UnifiedEvent) -> CsvEventWriter:
        event_type = event.payload.get("event_type")
        if event_type == "keyboard_input":
            return session.keyboard_writer
        if event_type == "mouse_input":
            return session.mouse_writer
        return session.events_writer

    def _resolve_target_session(self, payload: dict[str, Any]) -> ActiveSession:
        session_id = payload.get("session_id")
        event_type = payload.get("event_type")

        if self._session is not None and session_id == self._session.session_id:
            return self._session

        if event_type == "questionnaire" and session_id in self._completed_sessions:
            return self._completed_sessions[session_id]

        raise SessionError("no_active_session", "No active session", 404)

    def _require_active_session(self) -> ActiveSession:
        return self._require_active_session_with_message("No session to stop")

    def _require_active_session_with_message(self, message: str) -> ActiveSession:
        if self._session is None:
            raise SessionError("no_active_session", message, 404)
        return self._session

    def _validate_event(self, payload: dict[str, Any]) -> None:
        error = next(self._validator.iter_errors(payload), None)
        if error is None:
            return
        field = ".".join(str(part) for part in error.absolute_path)
        if field:
            message = f"{field}: {error.message}"
        else:
            message = error.message
        logging.warning("Rejected malformed event payload: %s", message)
        raise SessionError("invalid_payload", message, 400)


def create_app(
    controller: SessionController,
    shutdown_callback: Callable[[], None] | None = None,
) -> Flask:
    app = Flask(__name__)

    @app.errorhandler(SessionError)
    def _handle_session_error(error: SessionError) -> tuple[Any, int]:
        payload, status_code = error.to_response()
        return jsonify(payload), status_code

    @app.get("/health")
    def health() -> Any:
        return jsonify(controller.health())

    @app.get("/session/status")
    def session_status() -> Any:
        return jsonify(controller.get_status())

    @app.post("/session/start")
    def session_start() -> Any:
        payload = request.get_json(silent=True) or {}
        user_id = str(payload.get("user_id", "")).strip()
        try:
            duration_minutes = int(payload.get("duration_minutes", 60))
        except (TypeError, ValueError) as exc:
            raise SessionError("invalid_payload", "duration_minutes must be an integer", 400) from exc
        enable_influx = bool(payload.get("enable_influx", False))

        if not user_id:
            raise SessionError("invalid_payload", "user_id is required", 400)
        if duration_minutes < 1 or duration_minutes > 180:
            raise SessionError("invalid_payload", "duration_minutes must be 1-180", 400)

        status = controller.start_session(
            user_id=user_id,
            duration_minutes=duration_minutes,
            enable_influx=enable_influx,
        )
        return jsonify(status)

    @app.post("/session/stop")
    def session_stop() -> Any:
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id")
        return jsonify(controller.stop_session(session_id))

    @app.post("/events")
    def ingest_events() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise SessionError("invalid_payload", "Request body must be a JSON object", 400)
        return jsonify(controller.ingest_payload(payload))

    @app.post("/shutdown")
    def shutdown() -> Any:
        payload = request.get_json(silent=True) or {}
        force = bool(payload.get("force", False))

        if controller.get_status().get("active") and not force:
            raise SessionError(
                "session_active",
                "Stop the active session before shutting down the core",
                409,
            )

        if force and controller.get_status().get("active"):
            controller.stop_session()

        if shutdown_callback is not None:
            shutdown_callback()
        return jsonify({"status": "shutting_down"})

    return app
