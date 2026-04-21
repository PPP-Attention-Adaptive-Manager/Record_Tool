import { SYSTEM_AGENT_WS, STATES, MSG, EV, CFG } from "../shared/constants.js";
import { nowSec } from "../shared/utils.js";

// ---------------------------------------------------------------------------
// State (lives in service-worker memory; survives for the session duration)
// ---------------------------------------------------------------------------

let state = STATES.IDLE;
let sessionInfo = {
  session_id: null,
  elapsed_time: 0,
  remaining_time: 0,
  duration: 0,
  state: STATES.IDLE,
};
let deviceId = null;
let ws = null;
let eventBuffer = [];
let reconnectTimer = null;

// ---------------------------------------------------------------------------
// Device ID (persisted across extension restarts)
// ---------------------------------------------------------------------------

async function ensureDeviceId() {
  if (deviceId) return deviceId;
  const stored = await chrome.storage.local.get("device_id");
  if (stored.device_id) {
    deviceId = stored.device_id;
  } else {
    deviceId = "browser_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    await chrome.storage.local.set({ device_id: deviceId });
  }
  return deviceId;
}

// ---------------------------------------------------------------------------
// WebSocket connection
// ---------------------------------------------------------------------------

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  try {
    ws = new WebSocket(SYSTEM_AGENT_WS);

    ws.onopen = () => {
      console.log("[BG] WebSocket connected to system agent");
      clearTimeout(reconnectTimer);
      sendHeartbeat();
    };

    ws.onmessage = (ev) => {
      try {
        handleSystemMsg(JSON.parse(ev.data));
      } catch (e) {
        console.warn("[BG] Bad JSON:", e);
      }
    };

    ws.onclose = () => {
      console.log("[BG] WebSocket closed — will retry");
      scheduleReconnect();
    };

    ws.onerror = () => {
      // error fires before close; close handles retry
    };
  } catch (e) {
    console.warn("[BG] WebSocket creation failed:", e);
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectWS, CFG.RECONNECT_MS);
}

function sendWS(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Heartbeat
// ---------------------------------------------------------------------------

function sendHeartbeat() {
  sendWS({ type: MSG.HEARTBEAT, device_id: deviceId, timestamp: nowSec() });
}

// Keep-alive via chrome.alarms (MV3 service worker may sleep)
chrome.alarms.create(CFG.ALARM_HEARTBEAT, { periodInMinutes: 0.16 }); // ~10 s

// ---------------------------------------------------------------------------
// Dispatch incoming system-agent messages
// ---------------------------------------------------------------------------

function handleSystemMsg(msg) {
  switch (msg.type) {
    case MSG.START_RECORDING:
      state = STATES.RECORDING;
      sessionInfo = {
        session_id: msg.session_id,
        duration: msg.duration || 0,
        elapsed_time: 0,
        remaining_time: msg.duration || 0,
        state: STATES.RECORDING,
      };
      persistState();
      notifyPopup({ type: "state_change", state, sessionInfo });
      break;

    case MSG.PAUSE_RECORDING:
      state = STATES.PAUSED;
      sessionInfo.state = STATES.PAUSED;
      persistState();
      notifyPopup({ type: "state_change", state, sessionInfo });
      break;

    case MSG.RESUME_RECORDING:
      state = STATES.RECORDING;
      sessionInfo.state = STATES.RECORDING;
      persistState();
      notifyPopup({ type: "state_change", state, sessionInfo });
      break;

    case MSG.STOP_RECORDING:
      state = STATES.IDLE;
      flushEvents();
      clearSessionInfo();
      persistState();
      notifyPopup({ type: "state_change", state, sessionInfo });
      break;

    case MSG.SESSION_UPDATE:
    case MSG.HEARTBEAT_ACK:
      sessionInfo = { ...sessionInfo, ...msg };
      state = msg.state || state;
      persistState();
      notifyPopup({ type: "session_update", sessionInfo });
      break;

    case MSG.OPEN_QUESTIONNAIRE:
      openQuestionnaire(msg.session_id);
      break;

    case MSG.SESSION_EXPIRED:
      state = STATES.IDLE;
      clearSessionInfo();
      persistState();
      notifyPopup({ type: "state_change", state, sessionInfo });
      break;

    default:
      break;
  }
}

function clearSessionInfo() {
  sessionInfo = { session_id: null, elapsed_time: 0, remaining_time: 0, duration: 0, state: STATES.IDLE };
}

function persistState() {
  chrome.storage.session.set({ cogState: { state, sessionInfo } }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Notify popup (if open)
// ---------------------------------------------------------------------------

function notifyPopup(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}

// ---------------------------------------------------------------------------
// Event recording
// ---------------------------------------------------------------------------

function record(obj) {
  if (state !== STATES.RECORDING) return;
  obj.timestamp   = nowSec();
  obj.session_id  = sessionInfo.session_id;
  obj.device_id   = deviceId;
  eventBuffer.push(obj);
}

function flushEvents() {
  if (eventBuffer.length === 0) return;
  const batch = eventBuffer.splice(0);
  sendWS({
    type:       MSG.BROWSER_EVENT_BATCH,
    events:     batch,
    session_id: sessionInfo.session_id,
    device_id:  deviceId,
  });
}

// ---------------------------------------------------------------------------
// Tab listeners
// ---------------------------------------------------------------------------

chrome.tabs.onCreated.addListener((tab) => {
  record({ event_type: EV.NEW_TAB, tab_id: String(tab.id), url: tab.url || "", title: tab.title || "" });
});

chrome.tabs.onActivated.addListener(async (info) => {
  try {
    const tab = await chrome.tabs.get(info.tabId);
    record({ event_type: EV.TAB_SWITCH, tab_id: String(tab.id), url: tab.url || "", title: tab.title || "" });
  } catch (_) {}
});

chrome.tabs.onRemoved.addListener((tabId) => {
  record({ event_type: EV.TAB_CLOSE, tab_id: String(tabId), url: "", title: "" });
});

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status === "complete" && tab.active) {
    record({ event_type: EV.NAVIGATION, tab_id: String(tabId), url: tab.url || "", title: tab.title || "" });
  }
});

// ---------------------------------------------------------------------------
// Idle detection
// ---------------------------------------------------------------------------

chrome.idle.setDetectionInterval(30);
chrome.idle.onStateChanged.addListener((s) => {
  if      (s === "idle")   record({ event_type: EV.IDLE });
  else if (s === "active") record({ event_type: EV.ACTIVE });
});

// ---------------------------------------------------------------------------
// Alarm handler (batch flush + heartbeat keepalive)
// ---------------------------------------------------------------------------

chrome.alarms.create(CFG.ALARM_BATCH, { periodInMinutes: CFG.BATCH_FLUSH_MS / 60_000 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === CFG.ALARM_BATCH) {
    flushEvents();
  } else if (alarm.name === CFG.ALARM_HEARTBEAT) {
    sendHeartbeat();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectWS();
    }
  }
});

// ---------------------------------------------------------------------------
// Messages from content scripts and popup
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "scroll_event") {
    record({
      event_type:     EV.SCROLL,
      tab_id:         String(sender.tab?.id || ""),
      url:            sender.tab?.url || "",
      scroll_delta_y: msg.scroll_delta_y || 0,
      scroll_total_y: msg.scroll_total_y || 0,
    });
    sendResponse({ ok: true });
  }
  else if (msg.type === "tab_hidden") {
    record({
      event_type: EV.TAB_HIDDEN,
      tab_id:     String(sender.tab?.id || ""),
      url:        sender.tab?.url || "",
    });
    sendResponse({ ok: true });
  }
  else if (msg.type === "get_state") {
    sendResponse({ state, sessionInfo, deviceId });
  }
  else if (msg.type === "questionnaire_submit") {
    sendWS({
      type: MSG.QUESTIONNAIRE_RESULTS,
      results: {
        ...msg.results,
        session_id: sessionInfo.session_id,
        device_id:  deviceId,
        timestamp:  nowSec(),
      },
    });
    sendResponse({ ok: true });
  }
  return true;
});

// ---------------------------------------------------------------------------
// Questionnaire tab
// ---------------------------------------------------------------------------

function openQuestionnaire(sessionId) {
  const url = chrome.runtime.getURL(
    `src/questionnaire/questionnaire.html?session_id=${encodeURIComponent(sessionId || "")}`
  );
  chrome.tabs.create({ url });
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

(async () => {
  await ensureDeviceId();
  // Restore session state if service worker restarted mid-session
  const stored = await chrome.storage.session.get("cogState");
  if (stored.cogState) {
    state       = stored.cogState.state || STATES.IDLE;
    sessionInfo = stored.cogState.sessionInfo || sessionInfo;
  }
  connectWS();
  console.log("[BG] Cognitive Behavior Collector initialized — device:", deviceId);
})();
