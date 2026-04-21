import asyncio
import logging
import sys
import os
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "system_agent.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_agent.config import config, MODE_EXPERIMENTAL
from system_agent.session_manager import SessionManager, SessionState
from system_agent.data_writer import DataWriter
from system_agent.keyboard_tracker import KeyboardTracker
from system_agent.mouse_tracker import MouseTracker
from system_agent.extension_server import ExtensionServer
from system_agent.dual_task import DualTask


class CognitiveSystemAgent:
    """Master controller — single source of truth for session state and timing."""

    def __init__(self):
        self.config = config
        self.loop: asyncio.AbstractEventLoop = None
        self.running = False

        self.data_writer = DataWriter(config)
        self.session_manager = SessionManager(
            config=config,
            broadcast_callback=self._broadcast,
        )
        self.extension_server = ExtensionServer(
            config=config,
            on_browser_events=self._handle_browser_events,
            on_questionnaire_results=self._handle_questionnaire_results,
            on_heartbeat=self._handle_heartbeat,
        )
        self.keyboard_tracker = KeyboardTracker(on_event=self._handle_keyboard_event)
        self.mouse_tracker = MouseTracker(
            on_event=self._handle_mouse_event,
            aggregation_interval=config.mouse_aggregation_interval,
        )
        self.dual_task: DualTask = None
        if config.mode == MODE_EXPERIMENTAL:
            self.dual_task = DualTask(on_response=self._handle_dual_task_response)

    # ------------------------------------------------------------------
    # Broadcast helper
    # ------------------------------------------------------------------

    async def _broadcast(self, message: dict):
        await self.extension_server.broadcast(message)

    # ------------------------------------------------------------------
    # Incoming event handlers
    # ------------------------------------------------------------------

    async def _handle_browser_events(self, events: list):
        for event in events:
            self.data_writer.write_behavior_event(event)

    async def _handle_questionnaire_results(self, results: dict):
        results["timestamp"] = time.time()
        self.data_writer.write_labels(results)
        self.data_writer.end_session()
        logger.info("Questionnaire results saved, session files closed")
        await self._broadcast({
            "type": "questionnaire_received",
            "session_id": self.session_manager.session_id,
        })

    async def _handle_heartbeat(self, data: dict):
        await self._broadcast({
            "type": "heartbeat_ack",
            **self.session_manager.get_status_dict(),
        })

    def _handle_keyboard_event(self, event: dict):
        if self.session_manager.state == SessionState.RUNNING:
            self.data_writer.write_keyboard_event(event)

    def _handle_mouse_event(self, event: dict):
        if self.session_manager.state == SessionState.RUNNING:
            self.data_writer.write_mouse_event(event)

    def _handle_dual_task_response(self, response: dict):
        if self.session_manager.state == SessionState.RUNNING:
            self.data_writer.write_behavior_event(response)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self):
        if self.session_manager.state != SessionState.IDLE:
            print("[!] A session is already active.")
            return

        session_id = self.session_manager.start()
        self.data_writer.start_session(session_id)
        self.keyboard_tracker.start()
        self.mouse_tracker.start()

        if self.dual_task:
            self.dual_task.start()

        await self._broadcast({
            "type": "start_recording",
            "session_id": session_id,
            "mode": self.config.mode,
            "duration": self.config.session_duration_minutes * 60,
        })

        self.session_manager.start_broadcast(self.loop)
        self.loop.create_task(self._session_watchdog())

        print(f"\n[SESSION STARTED]  id={session_id}")
        print(f"  mode     : {self.config.mode.upper()}")
        print(f"  duration : {self.config.session_duration_minutes} min\n")

    async def pause_session(self):
        if self.session_manager.state != SessionState.RUNNING:
            print("[!] No running session to pause.")
            return
        self.session_manager.pause()
        await self._broadcast({"type": "pause_recording"})
        print("[SESSION PAUSED]")

    async def resume_session(self):
        if self.session_manager.state != SessionState.PAUSED:
            print("[!] No paused session to resume.")
            return
        self.session_manager.resume()
        await self._broadcast({"type": "resume_recording"})
        print("[SESSION RESUMED]")

    async def stop_session(self, trigger_questionnaire: bool = True):
        if self.session_manager.state not in (SessionState.RUNNING, SessionState.PAUSED):
            print("[!] No active session to stop.")
            return

        self.keyboard_tracker.stop()
        self.mouse_tracker.stop()
        if self.dual_task:
            self.dual_task.stop()

        session_id = self.session_manager.session_id
        self.session_manager.stop()
        self.session_manager.stop_broadcast()

        await self._broadcast({"type": "stop_recording", "session_id": session_id})

        if trigger_questionnaire and self.config.mode == MODE_EXPERIMENTAL:
            await asyncio.sleep(0.3)
            await self._broadcast({"type": "open_questionnaire", "session_id": session_id})
            logger.info("Questionnaire triggered — waiting for browser submission")
        else:
            self.data_writer.end_session()

        print(f"\n[SESSION STOPPED]  id={session_id}\n")

    async def _session_watchdog(self):
        while True:
            state = self.session_manager.state
            if state not in (SessionState.RUNNING, SessionState.PAUSED):
                break
            if self.session_manager.is_expired():
                logger.info("Session expired — auto-stopping")
                await self.stop_session()
                break
            await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Interactive CLI
    # ------------------------------------------------------------------

    async def _cli_loop(self):
        _BANNER = (
            "\n"
            "╔══════════════════════════════════════╗\n"
            "║   Cognitive System Agent  v2.0       ║\n"
            "╚══════════════════════════════════════╝\n"
            f"  Mode    : {self.config.mode.upper()}\n"
            f"  WS      : ws://{self.config.websocket_host}:{self.config.websocket_port}\n"
            f"  HTTP    : http://{self.config.http_host}:{self.config.http_port}\n"
            "\n"
            "  Commands: [s]tart  [p]ause  [r]esume  [e]nd  [status]  [q]uit\n"
        )
        print(_BANNER)

        while self.running:
            try:
                cmd = await self.loop.run_in_executor(None, input, "> ")
                cmd = cmd.strip().lower()
            except (EOFError, KeyboardInterrupt):
                self.running = False
                break

            if cmd in ("s", "start"):
                await self.start_session()
            elif cmd in ("p", "pause"):
                await self.pause_session()
            elif cmd in ("r", "resume"):
                await self.resume_session()
            elif cmd in ("e", "end", "stop"):
                await self.stop_session()
            elif cmd == "status":
                print(self.session_manager.get_status_dict())
            elif cmd in ("q", "quit", "exit"):
                self.running = False
            else:
                print("  Unknown command. Use: s p r e status q")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self):
        self.loop = asyncio.get_event_loop()
        self.running = True
        try:
            await self.extension_server.start()
            await self._cli_loop()
        finally:
            sm = self.session_manager
            if sm.state in (SessionState.RUNNING, SessionState.PAUSED):
                await self.stop_session(trigger_questionnaire=False)
            self.data_writer.close()
            await self.extension_server.stop()
            logger.info("Cognitive System Agent shut down cleanly")


def main():
    agent = CognitiveSystemAgent()
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
