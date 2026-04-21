"""HTTP localhost communication bridge between extension(s) and system agent."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from shared.time_utils import now_ns, ns_to_iso8601

LOGGER = logging.getLogger(__name__)


@dataclass
class ExtensionClient:
    device_id: str
    browser_id: str
    last_seen_ns: int
    clock_offset_ms: Optional[float] = None
    command_queue: Deque[dict] = field(default_factory=deque)


class CommunicationServer:
    """Receives extension batches and exposes queued commands via heartbeat polling."""

    def __init__(
        self,
        host: str,
        port: int,
        heartbeat_timeout_sec: int,
        session_manager: Any,
    ) -> None:
        self._host = host
        self._port = port
        self._heartbeat_timeout_ns = heartbeat_timeout_sec * 1_000_000_000
        self._session_manager = session_manager
        self._clients: Dict[str, ExtensionClient] = {}
        self._lock = threading.Lock()
        self._http_server = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None

        self._app = Flask("cognitive-system-communication")
        self._register_routes()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._http_server = make_server(self._host, self._port, self._app, threaded=True)
        self._thread = threading.Thread(target=self._http_server.serve_forever, daemon=True, name="comm-http")
        self._thread.start()

        self._stop_event.clear()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="comm-cleanup",
        )
        self._cleanup_thread.start()
        LOGGER.info("Communication server listening on http://%s:%s", self._host, self._port)

    def stop(self) -> None:
        self._stop_event.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2.0)
        if self._http_server:
            self._http_server.shutdown()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def list_clients(self) -> Dict[str, ExtensionClient]:
        with self._lock:
            return {device_id: client for device_id, client in self._clients.items()}

    def queue_command(
        self,
        command: str,
        *,
        session_id: str,
        payload: Optional[dict] = None,
        target_device_id: Optional[str] = None,
        target_browser_id: Optional[str] = None,
    ) -> int:
        with self._lock:
            command_payload = {
                "command": command,
                "session_id": session_id,
                "payload": payload or {},
                "issued_at_ms": int(time.time() * 1000),
            }

            targets: List[str] = []
            if target_device_id:
                if target_device_id in self._clients:
                    targets = [target_device_id]
            elif target_browser_id:
                targets = [
                    device_id
                    for device_id, client in self._clients.items()
                    if target_browser_id.lower() in client.browser_id.lower()
                ]
            else:
                targets = list(self._clients.keys())

            for device_id in targets:
                self._clients[device_id].command_queue.append(command_payload)
            return len(targets)

    def _register_routes(self) -> None:
        @self._app.after_request
        def _add_headers(response):  # type: ignore[no-untyped-def]
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
            return response

        @self._app.route("/v1/health", methods=["GET"])
        def health() -> Any:
            return jsonify({"status": "ok", "server_time_ns": now_ns()})

        @self._app.route("/v1/extensions/heartbeat", methods=["POST", "OPTIONS"])
        def heartbeat() -> Any:
            if request.method == "OPTIONS":
                return ("", 204)

            payload = request.get_json(silent=True) or {}
            device_id = str(payload.get("device_id") or "").strip()
            browser_id = str(payload.get("browser_id") or "").strip()
            ext_ts_ms = payload.get("extension_timestamp_ms")

            if not device_id or not browser_id:
                return jsonify({"status": "error", "error": "device_id and browser_id are required"}), 400

            system_now_ns = now_ns()
            with self._lock:
                client = self._clients.get(device_id)
                if client is None:
                    client = ExtensionClient(
                        device_id=device_id,
                        browser_id=browser_id,
                        last_seen_ns=system_now_ns,
                    )
                    self._clients[device_id] = client
                    LOGGER.info("Extension connected device_id=%s browser_id=%s", device_id, browser_id)
                else:
                    client.browser_id = browser_id
                    client.last_seen_ns = system_now_ns

                if ext_ts_ms is not None:
                    self._update_clock_offset(client, ext_ts_ms, system_now_ns)

                queued_commands = list(client.command_queue)
                client.command_queue.clear()

            self._session_manager.on_extension_heartbeat(device_id=device_id, browser_id=browser_id)
            return jsonify(
                {
                    "status": "ok",
                    "server_timestamp_ms": int(system_now_ns / 1_000_000),
                    "commands": queued_commands,
                }
            )

        @self._app.route("/v1/extensions/events", methods=["POST", "OPTIONS"])
        def extension_events() -> Any:
            if request.method == "OPTIONS":
                return ("", 204)

            payload = request.get_json(silent=True) or {}
            device_id = str(payload.get("device_id") or "").strip()
            browser_id = str(payload.get("browser_id") or "").strip()
            session_id = str(payload.get("session_id") or "").strip()
            events = payload.get("events") or []

            if not device_id or not browser_id or not isinstance(events, list):
                return jsonify({"status": "error", "error": "invalid payload"}), 400

            with self._lock:
                client = self._clients.get(device_id)
                if not client:
                    client = ExtensionClient(
                        device_id=device_id,
                        browser_id=browser_id,
                        last_seen_ns=now_ns(),
                    )
                    self._clients[device_id] = client
                client.last_seen_ns = now_ns()
                client.browser_id = browser_id

            normalized_events = []
            for raw in events:
                if not isinstance(raw, dict):
                    continue
                event = dict(raw)
                extension_ts_ms = event.get("timestamp_ms")
                timestamp_ns = self._extension_to_system_ns(device_id, extension_ts_ms)
                event["timestamp_ns"] = timestamp_ns
                event["timestamp"] = ns_to_iso8601(timestamp_ns)
                event["device_id"] = device_id
                event["browser_id"] = browser_id
                if session_id:
                    event["session_id"] = session_id
                normalized_events.append(event)

            accepted, dropped = self._session_manager.handle_browser_event_batch(
                device_id=device_id,
                browser_id=browser_id,
                session_id=session_id,
                events=normalized_events,
            )
            return jsonify({"status": "ok", "accepted": accepted, "dropped": dropped})

    def _update_clock_offset(self, client: ExtensionClient, ext_ts_ms: Any, system_now_ns: int) -> None:
        try:
            ext_ts_ms_value = float(ext_ts_ms)
        except (TypeError, ValueError):
            return

        system_now_ms = system_now_ns / 1_000_000
        estimate = system_now_ms - ext_ts_ms_value
        if client.clock_offset_ms is None:
            client.clock_offset_ms = estimate
        else:
            alpha = 0.2
            client.clock_offset_ms = (1 - alpha) * client.clock_offset_ms + alpha * estimate

    def _extension_to_system_ns(self, device_id: str, extension_timestamp_ms: Any) -> int:
        with self._lock:
            client = self._clients.get(device_id)

        if extension_timestamp_ms is None or not client:
            return now_ns()

        try:
            ext_ts_ms = float(extension_timestamp_ms)
        except (TypeError, ValueError):
            return now_ns()

        if client.clock_offset_ms is None:
            return now_ns()

        return int((ext_ts_ms + client.clock_offset_ms) * 1_000_000)

    def _cleanup_loop(self) -> None:
        while not self._stop_event.wait(2.0):
            current_ns = now_ns()
            stale = []
            with self._lock:
                for device_id, client in list(self._clients.items()):
                    if current_ns - client.last_seen_ns > self._heartbeat_timeout_ns:
                        stale.append(device_id)
                for device_id in stale:
                    self._clients.pop(device_id, None)

            for device_id in stale:
                LOGGER.warning("Extension heartbeat timeout device_id=%s", device_id)
                self._session_manager.on_extension_disconnect(device_id=device_id)

