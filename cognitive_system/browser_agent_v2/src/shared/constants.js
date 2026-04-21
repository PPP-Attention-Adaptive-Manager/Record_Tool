export const SYSTEM_AGENT_WS = "ws://localhost:8765";
export const SYSTEM_AGENT_HTTP = "http://localhost:8080";

export const SESSION_STATE = Object.freeze({
  INACTIVE: "inactive",
  RUNNING: "running",
  PAUSED: "paused",
});

export const MSG = Object.freeze({
  START_RECORDING: "start_recording",
  RESUME_RECORDING: "resume_recording",
  PAUSE_RECORDING: "pause_recording",
  STOP_RECORDING: "stop_recording",
  SESSION_UPDATE: "session_update",
  HEARTBEAT_ACK: "heartbeat_ack",
  OPEN_QUESTIONNAIRE: "open_questionnaire",
  DUAL_TASK_PROBE: "dual_task_probe",
  QUESTIONNAIRE_RECEIVED: "questionnaire_received",

  HEARTBEAT: "heartbeat",
  BROWSER_EVENT_BATCH: "browser_event_batch",
  QUESTIONNAIRE_RESULTS: "questionnaire_results",
});

export const EVENT_TYPE = Object.freeze({
  NEW_TAB: "new_tab",
  TAB_SWITCH: "tab_switch",
  TAB_CLOSE: "tab_close",
  TAB_HIDDEN: "tab_hidden",
  NAVIGATION: "navigation",
  SCROLL: "scroll",
  IDLE: "idle",
  ACTIVE: "active",
  DUAL_TASK: "dual_task",
});

export const CFG = Object.freeze({
  HEARTBEAT_INTERVAL_MINUTES: 0.16, // ~10s
  FLUSH_INTERVAL_MINUTES: 0.08, // ~5s
  RECONNECT_MS: 3000,
  ALARM_HEARTBEAT: "heartbeat",
  ALARM_FLUSH: "flush_events",
});

