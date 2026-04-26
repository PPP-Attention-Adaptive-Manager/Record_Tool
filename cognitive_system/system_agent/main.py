from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from typing import Optional

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
LOGGER = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_agent.app_tracker import AppSnapshot, AppTracker
from system_agent.config import RuntimeConfig, build_runtime_config
from system_agent.context_tracker import ContextFrame, ContextTracker
from system_agent.data_writer import DataWriter
from system_agent.dependency_validation import validate_runtime_dependencies
from system_agent.dual_task_manager import DualTaskManager
from system_agent.extension_server import ExtensionServer
from system_agent.keyboard_tracker import KeyboardTracker
from system_agent.mouse_tracker import MouseTracker
from system_agent.session_manager import SessionManager


# ── Validation: drop events that are missing required fields ──────────────────
_REQUIRED_BEHAVIOR_FIELDS = ("timestamp", "session_id", "event_type")


def _validate_behavior_event(event: dict) -> bool:
    for field in _REQUIRED_BEHAVIOR_FIELDS:
        if not event.get(field):
            LOGGER.warning("Dropping malformed event (missing '%s'): %s", field, event)
            return False
    return True


# ── Browser events that trigger a context switch in the ContextTracker ────────
_CTX_SWITCH_TYPES = frozenset({"navigation", "tab_switch", "new_tab"})
_CTX_CLOSE_TYPES = frozenset({"tab_close"})
_CTX_IDLE_TYPE = "idle"
_CTX_ACTIVE_TYPE = "active"


class CognitiveSystemAgent:
    """Master orchestrator for session timing and extension control."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        self.data_writer = DataWriter(config)
        self.session_manager = SessionManager(
            mode=config.mode,
            duration_minutes=config.session_duration_minutes,
        )
        self.extension_server = ExtensionServer(
            config=config,
            on_browser_events=self._handle_browser_events,
            on_questionnaire_results=self._handle_questionnaire_results,
            on_heartbeat=self._handle_heartbeat,
        )

        self.keyboard_tracker = KeyboardTracker(
            on_event=self._handle_keyboard_event,
            enabled=config.keyboard_tracking_enabled,
        )
        self.mouse_tracker = MouseTracker(
            on_event=self._handle_mouse_event,
            enabled=config.mouse_tracking_enabled,
        )
        self.app_tracker = AppTracker(
            poll_interval_sec=config.app_poll_interval_seconds,
            browser_processes=set(config.browser_processes),
            on_change=self._on_active_app_change,
        )

        self._dual_task_mgr = DualTaskManager()
        self._context_tracker = ContextTracker(on_finalized=self._on_context_finalized)
        self._hotkey_listener = None  # pynput GlobalHotKeys instance

        self._browser_foreground = False
        self._latest_app_snapshot: Optional[AppSnapshot] = None

        self._status_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._dual_task_task: Optional[asyncio.Task] = None

        self._session_finished = asyncio.Event()
        self._awaiting_questionnaire = False
        self._pending_questionnaire_session_id: Optional[str] = None
        self._questionnaire_done = asyncio.Event()

    # ------------------------------------------------------------------
    # Incoming extension callbacks
    # ------------------------------------------------------------------

    async def _handle_browser_events(self, events: list[dict]) -> None:
        current_session = self.session_manager.session_id
        if not current_session:
            return

        current_snap = self._latest_app_snapshot
        app_name = current_snap.app_name if current_snap else "unknown"

        for raw in events:
            if not isinstance(raw, dict):
                continue
            event = dict(raw)
            event.setdefault("timestamp", time.time())
            event.setdefault("session_id", current_session)
            event.setdefault("device_id", self.config.device_id)
            event.setdefault("app_name", app_name)
            if event.get("session_id") != current_session:
                continue

            ts = float(event["timestamp"])
            etype = event.get("event_type", "")

            # ── Context switch events ─────────────────────────────────────────
            # These close the previous context and open a new one.
            # The finalized event (with duration_ms) is emitted by ContextTracker.
            if etype in _CTX_SWITCH_TYPES:
                self._context_tracker.switch_context(ContextFrame(
                    session_id=current_session,
                    device_id=self.config.device_id,
                    app_name=app_name,
                    window_title=event.get("title", ""),
                    url=event.get("url", ""),
                    tab_id=str(event.get("tab_id", "")),
                    start_time=ts,
                ))
                continue  # finalized event carries the data; skip raw write

            # ── Tab close: no new context (next switch will open one) ─────────
            if etype in _CTX_CLOSE_TYPES:
                self._context_tracker.close_context(ts)
                continue

            # ── Idle: user became inactive — track idle as its own context ────
            if etype == _CTX_IDLE_TYPE:
                self._context_tracker.switch_context(ContextFrame(
                    session_id=current_session,
                    device_id=self.config.device_id,
                    app_name="idle",
                    window_title="",
                    url="",
                    tab_id="",
                    start_time=ts,
                ))
                continue

            # ── Active: user returned from idle — resume browser context ──────
            if etype == _CTX_ACTIVE_TYPE:
                self._context_tracker.switch_context(ContextFrame(
                    session_id=current_session,
                    device_id=self.config.device_id,
                    app_name=app_name,
                    window_title=current_snap.window_title if current_snap else "",
                    url="",
                    tab_id="",
                    start_time=ts,
                ))
                continue

            # ── All other events (scroll, tab_hidden, …) written directly ─────
            if not _validate_behavior_event(event):
                continue
            self.data_writer.write_behavior_event(event)

    async def _handle_questionnaire_results(self, results: dict) -> None:
        if not self._awaiting_questionnaire:
            LOGGER.warning("Ignoring questionnaire payload because no questionnaire is pending.")
            return

        payload = dict(results)
        payload.setdefault("timestamp", time.time())
        payload.setdefault("session_id", self._pending_questionnaire_session_id)
        payload.setdefault("device_id", self.config.device_id)

        if payload.get("session_id") != self._pending_questionnaire_session_id:
            LOGGER.warning(
                "Ignoring questionnaire for unexpected session_id=%s (expected=%s)",
                payload.get("session_id"),
                self._pending_questionnaire_session_id,
            )
            return

        self.data_writer.write_labels(payload)
        self.data_writer.end_session()
        self._awaiting_questionnaire = False
        self._pending_questionnaire_session_id = None
        self._questionnaire_done.set()
        LOGGER.info("Questionnaire received and persisted")

    async def _handle_heartbeat(self, _: dict) -> dict:
        return {
            "type": "heartbeat_ack",
            **self.session_manager.snapshot().to_dict(),
        }

    # ── Context tracker callback (sync — called from ContextTracker._emit) ──

    def _on_context_finalized(self, event: dict) -> None:
        """Receive a finalized context event and persist it to behavior.csv."""
        if not self.session_manager.active:
            return
        if _validate_behavior_event(event):
            self.data_writer.write_behavior_event(event)

    # ------------------------------------------------------------------
    # Local tracker callbacks
    # ------------------------------------------------------------------

    def _handle_keyboard_event(self, event: dict) -> None:
        if not self.session_manager.active:
            return
        self.data_writer.write_keyboard_event(event)

    def _handle_mouse_event(self, event: dict) -> None:
        if not self.session_manager.active:
            return
        self.data_writer.write_mouse_event(event)

    def _on_active_app_change(self, snapshot: AppSnapshot) -> None:
        self._latest_app_snapshot = snapshot
        self._browser_foreground = bool(snapshot.is_browser)

        if self.loop is None:
            return

        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._apply_browser_foreground(snapshot))
        )

    async def _apply_browser_foreground(self, snapshot: AppSnapshot) -> None:
        if not self.session_manager.active:
            return

        command = self.session_manager.set_browser_foreground(snapshot.is_browser)
        session_id = self.session_manager.session_id

        # Switch the active context to the newly focused app.
        # ContextTracker closes the old context (emitting a finalized event with
        # duration_ms) and opens a new one for this snapshot.
        if session_id:
            ts = time.time()
            self._context_tracker.switch_context(ContextFrame(
                session_id=session_id,
                device_id=self.config.device_id,
                app_name=snapshot.app_name,
                window_title=snapshot.window_title,
                url="",
                tab_id="",
                start_time=ts,
            ))

        if command:
            await self._send_recording_command(command)
        await self._broadcast_status()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self) -> None:
        if self.session_manager.active:
            raise RuntimeError("Cannot start: session already active.")

        self._session_finished.clear()
        self._questionnaire_done.clear()
        self._awaiting_questionnaire = False
        self._pending_questionnaire_session_id = None

        session_id = self.session_manager.start_session()
        self.data_writer.start_session(session_id)

        self.keyboard_tracker.start()
        self.mouse_tracker.start()

        # Open the initial context so the very first interval has a start_time.
        snap = self._latest_app_snapshot
        self._context_tracker.open_context(ContextFrame(
            session_id=session_id,
            device_id=self.config.device_id,
            app_name=snap.app_name if snap else "unknown",
            window_title=snap.window_title if snap else "",
            url="",
            tab_id="",
            start_time=time.time(),
        ))

        # Apply current browser focus immediately; emits start/resume when foreground.
        initial_command = self.session_manager.set_browser_foreground(self._browser_foreground)
        if initial_command:
            await self._send_recording_command(initial_command)

        self._status_task = asyncio.create_task(self._status_broadcast_loop(), name="status-broadcast")
        self._watchdog_task = asyncio.create_task(self._session_watchdog_loop(), name="session-watchdog")
        if self.config.dual_task_enabled:
            self._dual_task_task = asyncio.create_task(self._dual_task_loop(), name="dual-task")

        await self._broadcast_status()

        print("\n[SESSION STARTED]")
        print(f"  session_id       : {session_id}")
        print(f"  mode             : {self.config.mode}")
        print(f"  duration_minutes : {self.config.session_duration_minutes}")
        print("  recording control: automatic (browser foreground driven)\n")

    async def stop_session(self, *, reason: str, open_questionnaire: bool = True) -> None:
        if not self.session_manager.active:
            return

        session_id = self.session_manager.session_id
        LOGGER.info("Stopping session %s reason=%s", session_id, reason)

        await self._cancel_task(self._status_task)
        self._status_task = None
        await self._cancel_task(self._watchdog_task)
        self._watchdog_task = None
        await self._cancel_task(self._dual_task_task)
        self._dual_task_task = None

        self.keyboard_tracker.stop()
        self.mouse_tracker.stop()

        # Force-close the active context BEFORE deactivating the session so the
        # finalized event can still be written (session_id is still valid here).
        self._context_tracker.force_close()

        await self.extension_server.broadcast(
            {
                "type": "stop_recording",
                "session_id": session_id,
            }
        )

        self.session_manager.stop_session()
        await self._broadcast_status()

        should_open_questionnaire = (
            open_questionnaire
            and self.config.is_experimental
            and self.config.questionnaire_enabled
            and session_id is not None
        )
        if should_open_questionnaire:
            self._awaiting_questionnaire = True
            self._pending_questionnaire_session_id = session_id
            await self.extension_server.broadcast(
                {
                    "type": "open_questionnaire",
                    "session_id": session_id,
                }
            )
            print("Questionnaire opened in browser. Waiting for submission...")
        else:
            self.data_writer.end_session()

        self._session_finished.set()
        print(f"[SESSION STOPPED] reason={reason}\n")

    async def _status_broadcast_loop(self) -> None:
        while self.session_manager.active:
            await self._broadcast_status()
            # ── Part 6: overwrite the same terminal line every second ─────────
            remaining = self.session_manager.get_remaining()
            mins = int(remaining) // 60
            secs = int(remaining) % 60
            print(f"\rTime left: {mins:02d}:{secs:02d}  ", end="", flush=True)
            await asyncio.sleep(self.config.session_broadcast_interval)

    async def _session_watchdog_loop(self) -> None:
        while self.session_manager.active:
            if self.session_manager.is_expired():
                await self.stop_session(reason="duration_expired", open_questionnaire=True)
                return
            await asyncio.sleep(0.5)

    async def _dual_task_loop(self) -> None:
        interval = max(5, self.config.dual_task_interval_seconds)
        timeout_ms = int(max(1, self.config.dual_task_timeout_seconds) * 1000)

        while self.session_manager.active:
            await asyncio.sleep(interval)
            if not self.session_manager.active:
                return
            snapshot = self.session_manager.snapshot()
            if snapshot.state != "running":
                continue

            # ── Part 4: show probe in OS window (tkinter), not in the browser ─
            probe_id = f"probe_{uuid.uuid4().hex[:8]}"
            result = await self.loop.run_in_executor(
                None,
                lambda: self._dual_task_mgr.run_probe(probe_id, timeout_ms),
            )
            if not snapshot.session_id:
                continue
            current_snap = self._latest_app_snapshot
            # ── Part 4: dual_task goes to dual_task.csv, never behavior.csv ──
            dt_event = {
                "timestamp": time.time(),
                "session_id": snapshot.session_id,
                "device_id": self.config.device_id,
                "reaction_time_ms": result.reaction_time_ms,
                "success": result.success,
                "error": result.error,
                "app_name": current_snap.app_name if current_snap else "unknown",
            }
            # Part 5: minimal validation — drop events without identity fields
            if dt_event.get("session_id") and dt_event.get("timestamp"):
                self.data_writer.write_dual_task_event(dt_event)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    async def _send_recording_command(self, command: str) -> None:
        payload = {
            "type": command,
            "session_id": self.session_manager.session_id,
            "mode": self.config.mode,
            "duration": self.config.session_duration_seconds,
        }
        await self.extension_server.broadcast(payload)

    async def _broadcast_status(self) -> None:
        await self.extension_server.broadcast(
            {
                "type": "session_update",
                **self.session_manager.snapshot().to_dict(),
            }
        )

    @staticmethod
    async def _cancel_task(task: Optional[asyncio.Task]) -> None:
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _wait_for_user_start(self) -> bool:
        print("Press Enter to start the session, or type 'q' to quit.")
        raw = await self.loop.run_in_executor(None, input, "> ")
        return raw.strip().lower() not in {"q", "quit", "exit"}

    async def _wait_for_questionnaire_if_needed(self) -> None:
        if not self._awaiting_questionnaire:
            return
        try:
            await asyncio.wait_for(self._questionnaire_done.wait(), timeout=900)
        except asyncio.TimeoutError:
            LOGGER.warning("Questionnaire timeout (15 minutes). Closing session files.")
            self.data_writer.end_session()
            self._awaiting_questionnaire = False
            self._pending_questionnaire_session_id = None

    # ── Part 5: global hotkey to trigger questionnaire mid-session ────────────

    def _setup_questionnaire_hotkey(self) -> None:
        # pynput is already a project dependency (keyboard + mouse tracking).
        # The 'keyboard' package is NOT used — it is unreliable on Windows without
        # admin rights and has no conda/venv wheel on some platforms.
        try:
            from pynput import keyboard as _pynput_kb  # type: ignore[import]

            def _trigger() -> None:
                if not self.session_manager.active or self._awaiting_questionnaire:
                    return
                if self.loop:
                    self.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self._open_questionnaire_now())
                    )

            # GlobalHotKeys maps pynput key-combo strings to callbacks.
            # "<ctrl>+<shift>+q" matches Ctrl+Shift+Q on all platforms.
            self._hotkey_listener = _pynput_kb.GlobalHotKeys(
                {"<ctrl>+<shift>+q": _trigger}
            )
            self._hotkey_listener.start()
            LOGGER.info("Questionnaire hotkey registered: Ctrl+Shift+Q (pynput)")
        except Exception as exc:
            LOGGER.warning("Could not register questionnaire hotkey: %s", exc)
            self._hotkey_listener = None

    async def _open_questionnaire_now(self) -> None:
        if not self.session_manager.active or self._awaiting_questionnaire:
            return
        session_id = self.session_manager.session_id
        if not session_id:
            return
        self._awaiting_questionnaire = True
        self._pending_questionnaire_session_id = session_id
        await self.extension_server.broadcast(
            {"type": "open_questionnaire", "session_id": session_id}
        )
        print("\n[QUESTIONNAIRE] Triggered via hotkey. Waiting for submission in browser...")

    def _print_banner(self) -> None:
        print(
            "\n"
            "==========================================\n"
            "   Cognitive System Agent (Orchestrator)\n"
            "==========================================\n"
            f"Mode                 : {self.config.mode}\n"
            f"Session duration     : {self.config.session_duration_minutes} min\n"
            f"CSV export           : {self.config.csv_enabled}\n"
            f"Influx export        : {self.config.influx_enabled}\n"
            f"Dual-task            : {self.config.dual_task_enabled}\n"
            f"Questionnaire        : {self.config.questionnaire_enabled}\n"
            f"WebSocket endpoint   : ws://{self.config.websocket_host}:{self.config.websocket_port}\n"
            f"HTTP endpoint        : http://{self.config.http_host}:{self.config.http_port}\n"
            f"Data directory       : {self.config.data_dir}\n"
        )

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        try:
            await self.extension_server.start()
            self.app_tracker.start()
            self._print_banner()
            self._setup_questionnaire_hotkey()  # Part 5: Ctrl+Shift+Q

            start = await self._wait_for_user_start()
            if not start:
                print("Session not started.")
                return

            await self.start_session()
            print("Session running. Recording pauses/resumes automatically with browser foreground.")
            print("Use Ctrl+C to stop early.\n")

            while not self._session_finished.is_set():
                await asyncio.sleep(0.5)

            await self._wait_for_questionnaire_if_needed()
        finally:
            try:
                await self.stop_session(reason="shutdown", open_questionnaire=False)
            except Exception:
                pass
            # Stop the pynput hotkey listener (if it was registered successfully)
            if self._hotkey_listener is not None:
                try:
                    self._hotkey_listener.stop()
                except Exception:
                    pass
                self._hotkey_listener = None
            self.app_tracker.stop()
            self.keyboard_tracker.stop()
            self.mouse_tracker.stop()
            self.data_writer.close()
            await self.extension_server.stop()
            LOGGER.info("System agent shut down cleanly")


def main(argv: Optional[list[str]] = None) -> None:
    try:
        config = build_runtime_config(argv)
        validate_runtime_dependencies(config)
    except Exception as exc:
        print(f"\n[FATAL] {exc}\n")
        raise SystemExit(1) from exc

    agent = CognitiveSystemAgent(config)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")


if __name__ == "__main__":
    main()

