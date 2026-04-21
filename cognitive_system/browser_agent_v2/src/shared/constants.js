export const SYSTEM_AGENT_WS   = "ws://localhost:8765";
export const SYSTEM_AGENT_HTTP = "http://localhost:8080";

export const STATES = Object.freeze({
  IDLE:      "idle",
  RECORDING: "recording",
  PAUSED:    "paused",
});

export const MSG = Object.freeze({
  // System -> Extension
  START_RECORDING:        "start_recording",
  PAUSE_RECORDING:        "pause_recording",
  RESUME_RECORDING:       "resume_recording",
  STOP_RECORDING:         "stop_recording",
  SESSION_UPDATE:         "session_update",
  OPEN_QUESTIONNAIRE:     "open_questionnaire",
  HEARTBEAT_ACK:          "heartbeat_ack",
  QUESTIONNAIRE_RECEIVED: "questionnaire_received",
  SESSION_EXPIRED:        "session_expired",

  // Extension -> System
  BROWSER_EVENT_BATCH:    "browser_event_batch",
  QUESTIONNAIRE_RESULTS:  "questionnaire_results",
  HEARTBEAT:              "heartbeat",
});

export const EV = Object.freeze({
  NEW_TAB:    "new_tab",
  TAB_SWITCH: "tab_switch",
  TAB_CLOSE:  "tab_close",
  TAB_HIDDEN: "tab_hidden",
  SCROLL:     "scroll",
  NAVIGATION: "navigation",
  IDLE:       "idle",
  ACTIVE:     "active",
  FOCUS:      "focus",
});

export const CFG = Object.freeze({
  SCROLL_FLUSH_MS:  2_000,
  BATCH_FLUSH_MS:   5_000,
  HEARTBEAT_MS:    10_000,
  RECONNECT_MS:     3_000,
  ALARM_BATCH:     "batch_flush",
  ALARM_HEARTBEAT: "heartbeat",
});
