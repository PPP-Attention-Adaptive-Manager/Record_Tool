import asyncio
import json
import logging
import time
from typing import Set, Optional, Callable, Dict

logger = logging.getLogger(__name__)

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets package not installed")

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning("aiohttp package not installed")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class ExtensionServer:
    """Dual-server: WebSocket for real-time commands, HTTP for batch data intake."""

    def __init__(
        self,
        config,
        on_browser_events: Callable,
        on_questionnaire_results: Callable,
        on_heartbeat: Callable,
    ):
        self.config = config
        self.on_browser_events = on_browser_events
        self.on_questionnaire_results = on_questionnaire_results
        self.on_heartbeat = on_heartbeat

        self._ws_clients: Set = set()
        self._ws_server = None
        self._http_runner = None

    async def start(self):
        tasks = []
        if WEBSOCKETS_AVAILABLE:
            tasks.append(self._start_websocket())
        else:
            logger.error("websockets not available - extension cannot connect")
        if AIOHTTP_AVAILABLE:
            tasks.append(self._start_http())
        else:
            logger.error("aiohttp not available - HTTP endpoint disabled")
        if tasks:
            await asyncio.gather(*tasks)

    async def _start_websocket(self):
        self._ws_server = await websockets.serve(
            self._ws_handler,
            self.config.websocket_host,
            self.config.websocket_port,
            ping_interval=20,
            ping_timeout=10,
        )
        logger.info(
            f"WebSocket server: ws://{self.config.websocket_host}:{self.config.websocket_port}"
        )

    async def _start_http(self):
        app = web.Application()
        app.router.add_post("/events", self._http_events)
        app.router.add_post("/questionnaire", self._http_questionnaire)
        app.router.add_get("/health", self._http_health)
        app.router.add_options("/{path:.*}", self._http_preflight)
        self._http_runner = web.AppRunner(app)
        await self._http_runner.setup()
        site = web.TCPSite(
            self._http_runner, self.config.http_host, self.config.http_port
        )
        await site.start()
        logger.info(
            f"HTTP server: http://{self.config.http_host}:{self.config.http_port}"
        )

    # ------------------------------------------------------------------
    # WebSocket handlers
    # ------------------------------------------------------------------

    async def _ws_handler(self, websocket, path=None):
        self._ws_clients.add(websocket)
        logger.info(f"Extension connected ({len(self._ws_clients)} active)")
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    await self._dispatch_ws(msg)
                except json.JSONDecodeError:
                    logger.warning(f"Bad JSON from extension: {raw[:80]}")
        except Exception as exc:
            logger.debug(f"WS connection closed: {exc}")
        finally:
            self._ws_clients.discard(websocket)
            logger.info(f"Extension disconnected ({len(self._ws_clients)} active)")

    async def _dispatch_ws(self, msg: Dict):
        t = msg.get("type")
        if t == "browser_event_batch":
            await self.on_browser_events(msg.get("events", []))
        elif t == "questionnaire_results":
            await self.on_questionnaire_results(msg.get("results", {}))
        elif t == "heartbeat":
            await self.on_heartbeat(msg)
        else:
            logger.debug(f"Unknown WS message type: {t}")

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _http_events(self, req: "web.Request"):
        try:
            data = await req.json()
            await self.on_browser_events(data.get("events", []))
            return web.json_response({"status": "ok"}, headers=_CORS_HEADERS)
        except Exception as exc:
            logger.error(f"HTTP /events error: {exc}")
            return web.json_response({"status": "error", "message": str(exc)},
                                     status=500, headers=_CORS_HEADERS)

    async def _http_questionnaire(self, req: "web.Request"):
        try:
            data = await req.json()
            await self.on_questionnaire_results(data)
            return web.json_response({"status": "ok"}, headers=_CORS_HEADERS)
        except Exception as exc:
            logger.error(f"HTTP /questionnaire error: {exc}")
            return web.json_response({"status": "error", "message": str(exc)},
                                     status=500, headers=_CORS_HEADERS)

    async def _http_health(self, req: "web.Request"):
        return web.json_response({"status": "ok", "ts": time.time()},
                                 headers=_CORS_HEADERS)

    async def _http_preflight(self, req: "web.Request"):
        return web.Response(headers=_CORS_HEADERS)

    # ------------------------------------------------------------------
    # Broadcast to all connected extension clients
    # ------------------------------------------------------------------

    async def broadcast(self, message: Dict):
        if not self._ws_clients:
            return
        payload = json.dumps(message)
        dead: Set = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def stop(self):
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
        if self._http_runner:
            await self._http_runner.cleanup()
        logger.info("ExtensionServer stopped")
