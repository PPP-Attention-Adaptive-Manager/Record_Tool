from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Dict, Optional, Set

try:
    import websockets
except ImportError:  # handled by dependency validation
    websockets = None  # type: ignore[assignment]

try:
    from aiohttp import web
except ImportError:  # handled by dependency validation
    web = None  # type: ignore[assignment]

from .config import RuntimeConfig

LOGGER = logging.getLogger(__name__)

JsonDict = Dict[str, object]


_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class ExtensionServer:
    """
    WebSocket: command/control + realtime event intake.
    HTTP: fallback batch intake and questionnaire submission.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        on_browser_events: Callable[[list[dict]], Awaitable[None]],
        on_questionnaire_results: Callable[[dict], Awaitable[None]],
        on_heartbeat: Callable[[dict], Awaitable[Optional[JsonDict]]],
    ) -> None:
        if websockets is None or web is None:
            raise RuntimeError(
                "ExtensionServer requires `websockets` and `aiohttp`. "
                "Install with `pip install websockets aiohttp`."
            )

        self._config = config
        self._on_browser_events = on_browser_events
        self._on_questionnaire_results = on_questionnaire_results
        self._on_heartbeat = on_heartbeat

        self._ws_clients: Set = set()
        self._ws_server = None
        self._http_runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        await asyncio.gather(self._start_websocket(), self._start_http())

    async def stop(self) -> None:
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
        LOGGER.info("Extension server stopped")

    async def broadcast(self, message: JsonDict) -> None:
        if not self._ws_clients:
            return
        payload = json.dumps(message)
        disconnected: Set = set()
        for client in list(self._ws_clients):
            try:
                await client.send(payload)
            except Exception:
                disconnected.add(client)
        self._ws_clients -= disconnected

    async def _start_websocket(self) -> None:
        self._ws_server = await websockets.serve(
            self._ws_handler,
            self._config.websocket_host,
            self._config.websocket_port,
            ping_interval=20,
            ping_timeout=20,
        )
        LOGGER.info(
            "WebSocket listening on ws://%s:%s",
            self._config.websocket_host,
            self._config.websocket_port,
        )

    async def _start_http(self) -> None:
        app = web.Application()
        app.router.add_post("/events", self._http_events)
        app.router.add_post("/questionnaire", self._http_questionnaire)
        app.router.add_get("/health", self._http_health)
        app.router.add_options("/{path:.*}", self._http_preflight)

        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, self._config.http_host, self._config.http_port)
        await site.start()
        LOGGER.info(
            "HTTP listening on http://%s:%s",
            self._config.http_host,
            self._config.http_port,
        )

    async def _ws_handler(self, websocket) -> None:
        self._ws_clients.add(websocket)
        LOGGER.info("Extension connected (%s clients)", len(self._ws_clients))
        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    LOGGER.warning("Discarding invalid JSON message from extension")
                    continue
                await self._dispatch_ws_message(websocket, message)
        except Exception as exc:
            LOGGER.debug("WebSocket closed: %s", exc)
        finally:
            self._ws_clients.discard(websocket)
            LOGGER.info("Extension disconnected (%s clients)", len(self._ws_clients))

    async def _dispatch_ws_message(self, websocket, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == "browser_event_batch":
            await self._on_browser_events(message.get("events", []))
            return
        if msg_type == "questionnaire_results":
            await self._on_questionnaire_results(message.get("results", {}))
            return
        if msg_type == "heartbeat":
            response = await self._on_heartbeat(message)
            if response:
                await websocket.send(json.dumps(response))
            return
        LOGGER.debug("Unknown WS message type: %s", msg_type)

    async def _http_events(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            await self._on_browser_events(payload.get("events", []))
            return web.json_response({"status": "ok"}, headers=_CORS_HEADERS)
        except Exception as exc:
            LOGGER.exception("HTTP /events failed: %s", exc)
            return web.json_response(
                {"status": "error", "message": str(exc)},
                status=500,
                headers=_CORS_HEADERS,
            )

    async def _http_questionnaire(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            await self._on_questionnaire_results(payload)
            return web.json_response({"status": "ok"}, headers=_CORS_HEADERS)
        except Exception as exc:
            LOGGER.exception("HTTP /questionnaire failed: %s", exc)
            return web.json_response(
                {"status": "error", "message": str(exc)},
                status=500,
                headers=_CORS_HEADERS,
            )

    async def _http_health(self, _: web.Request) -> web.Response:
        return web.json_response({"status": "ok"}, headers=_CORS_HEADERS)

    async def _http_preflight(self, _: web.Request) -> web.Response:
        return web.Response(headers=_CORS_HEADERS)
