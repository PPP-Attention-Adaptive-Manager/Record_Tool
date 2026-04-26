import {
  CFG,
  EVENT_TYPE,
  MSG,
  SESSION_STATE,
} from "../shared/constants.js";
import { buildSystemAgentUrls, loadRuntimeConfig } from "../shared/runtime_config.js";
import { nowSec } from "../shared/utils.js";

function defaultSessionStatus() {
  return {
    session_id: null,
    mode: null,
    state: SESSION_STATE.INACTIVE,
    elapsed_time: 0,
    remaining_time: 0,
    duration: 0,
    recording_active: false,
  };
}

let sessionStatus = defaultSessionStatus();
let recordingActive = false;
let connectionOnline = false;
let eventBuffer = [];
let deviceId = null;
let ws = null;
let reconnectTimer = null;
let systemAgentHttp = "http://localhost:8080";
let systemAgentWs = "ws://localhost:8765";

async function ensureDeviceId() {
  if (deviceId) return deviceId;
  const stored = await chrome.storage.local.get("device_id");
  if (stored.device_id) {
    deviceId = stored.device_id;
    return deviceId;
  }
  deviceId =
    "browser_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  await chrome.storage.local.set({ device_id: deviceId });
  return deviceId;
}

async function restoreState() {
  try {
    const stored = await chrome.storage.session.get("collectorState");
    if (!stored.collectorState) return;
    if (stored.collectorState.sessionStatus) {
      sessionStatus = { ...defaultSessionStatus(), ...stored.collectorState.sessionStatus };
    }
    if (typeof stored.collectorState.recordingActive === "boolean") {
      recordingActive = stored.collectorState.recordingActive;
    }
    if (typeof stored.collectorState.connectionOnline === "boolean") {
      connectionOnline = stored.collectorState.connectionOnline;
    }
  } catch (_) {}
}

function persistState() {
  chrome.storage.session
    .set({
      collectorState: {
        sessionStatus,
        recordingActive,
        connectionOnline,
      },
    })
    .catch(() => {});
}

function notifyPopup() {
  chrome.runtime
    .sendMessage({
      type: "session_snapshot",
      sessionStatus,
      recordingActive,
      connectionOnline,
      deviceId,
    })
    .catch(() => {});
}

function sendWS(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectWS, CFG.RECONNECT_MS);
}

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  try {
    ws = new WebSocket(systemAgentWs);
    ws.onopen = () => {
      connectionOnline = true;
      persistState();
      notifyPopup();
      sendHeartbeat();
    };
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleSystemMessage(msg);
      } catch (error) {
        console.warn("[BG] invalid system message", error);
      }
    };
    ws.onclose = () => {
      connectionOnline = false;
      persistState();
      notifyPopup();
      scheduleReconnect();
    };
    ws.onerror = () => {};
  } catch (error) {
    connectionOnline = false;
    persistState();
    notifyPopup();
    scheduleReconnect();
  }
}

function sendHeartbeat() {
  sendWS({
    type: MSG.HEARTBEAT,
    device_id: deviceId,
    extension_timestamp_ms: Date.now(),
  });
}

function mergeSessionStatus(payload) {
  sessionStatus = {
    ...sessionStatus,
    ...payload,
  };
  if (typeof payload.recording_active === "boolean") {
    recordingActive = payload.recording_active;
  }
  sessionStatus.recording_active = recordingActive;
}

function applyInactiveState() {
  sessionStatus = defaultSessionStatus();
  recordingActive = false;
}

function handleSystemMessage(msg) {
  switch (msg.type) {
    case MSG.START_RECORDING:
      mergeSessionStatus({
        session_id: msg.session_id,
        mode: msg.mode ?? sessionStatus.mode,
        duration: msg.duration ?? sessionStatus.duration,
        state: SESSION_STATE.RUNNING,
        recording_active: true,
      });
      break;
    case MSG.RESUME_RECORDING:
      mergeSessionStatus({
        state: SESSION_STATE.RUNNING,
        recording_active: true,
      });
      break;
    case MSG.PAUSE_RECORDING:
      mergeSessionStatus({
        state: SESSION_STATE.PAUSED,
        recording_active: false,
      });
      flushEvents();
      break;
    case MSG.STOP_RECORDING:
      flushEvents();
      applyInactiveState();
      break;
    case MSG.SESSION_UPDATE:
    case MSG.HEARTBEAT_ACK:
      mergeSessionStatus({
        session_id: msg.session_id ?? sessionStatus.session_id,
        mode: msg.mode ?? sessionStatus.mode,
        state: msg.state ?? sessionStatus.state,
        elapsed_time: msg.elapsed_time ?? sessionStatus.elapsed_time,
        remaining_time: msg.remaining_time ?? sessionStatus.remaining_time,
        duration: msg.duration ?? sessionStatus.duration,
        recording_active:
          typeof msg.recording_active === "boolean"
            ? msg.recording_active
            : recordingActive,
      });
      break;
    case MSG.OPEN_QUESTIONNAIRE:
      openQuestionnaire(msg.session_id);
      break;
    case MSG.DUAL_TASK_PROBE:
      triggerDualTaskProbe(msg);
      break;
    default:
      break;
  }

  persistState();
  notifyPopup();
}

function enqueueEvent(event, options = {}) {
  const force = Boolean(options.force);
  if (!sessionStatus.session_id) return;
  if (!force && !recordingActive) return;

  eventBuffer.push({
    timestamp: nowSec(),
    session_id: sessionStatus.session_id,
    device_id: deviceId,
    ...event,
  });
}

function flushEvents() {
  if (!eventBuffer.length || !sessionStatus.session_id) return;
  const batch = eventBuffer.splice(0, eventBuffer.length);
  const ok = sendWS({
    type: MSG.BROWSER_EVENT_BATCH,
    session_id: sessionStatus.session_id,
    device_id: deviceId,
    events: batch,
  });
  if (!ok) {
    eventBuffer = batch.concat(eventBuffer);
  }
}

async function triggerDualTaskProbe(msg) {
  if (!sessionStatus.session_id) return;
  const probeId = msg.probe_id || "";
  const timeoutMs = Number(msg.timeout_ms || 3000);
  try {
    const tabs = await chrome.tabs.query({
      active: true,
      lastFocusedWindow: true,
    });
    const activeTab = tabs[0];
    if (!activeTab || activeTab.id == null) {
      enqueueEvent(
        {
          event_type: EVENT_TYPE.DUAL_TASK,
          probe_id: probeId,
          reaction_time_ms: 0,
          miss: true,
          error: true,
          extra: "no_active_tab",
        },
        { force: true }
      );
      flushEvents();
      return;
    }

    const response = await chrome.tabs.sendMessage(activeTab.id, {
      type: "show_dual_task_probe",
      probe_id: probeId,
      timeout_ms: timeoutMs,
    });

    if (!response || response.ok !== true) {
      enqueueEvent(
        {
          event_type: EVENT_TYPE.DUAL_TASK,
          probe_id: probeId,
          reaction_time_ms: 0,
          miss: true,
          error: true,
          extra: response?.reason || "probe_rejected",
        },
        { force: true }
      );
      flushEvents();
    }
  } catch (_) {
    enqueueEvent(
      {
        event_type: EVENT_TYPE.DUAL_TASK,
        probe_id: probeId,
        reaction_time_ms: 0,
        miss: true,
        error: true,
        extra: "probe_dispatch_failed",
      },
      { force: true }
    );
    flushEvents();
  }
}

function openQuestionnaire(sessionId) {
  const safeSession = encodeURIComponent(sessionId || "");
  const url = chrome.runtime.getURL(
    `src/questionnaire/questionnaire.html?session_id=${safeSession}`
  );
  chrome.tabs.create({ url });
}

chrome.tabs.onCreated.addListener((tab) => {
  enqueueEvent({
    event_type: EVENT_TYPE.NEW_TAB,
    tab_id: String(tab.id || ""),
    url: tab.url || "",
    title: tab.title || "",
  });
});

chrome.tabs.onActivated.addListener(async (info) => {
  try {
    const tab = await chrome.tabs.get(info.tabId);
    enqueueEvent({
      event_type: EVENT_TYPE.TAB_SWITCH,
      tab_id: String(tab.id || ""),
      url: tab.url || "",
      title: tab.title || "",
    });
  } catch (_) {}
});

chrome.tabs.onRemoved.addListener((tabId) => {
  enqueueEvent({
    event_type: EVENT_TYPE.TAB_CLOSE,
    tab_id: String(tabId),
  });
});

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status !== "complete" || !tab.active) return;
  enqueueEvent({
    event_type: EVENT_TYPE.NAVIGATION,
    tab_id: String(tabId),
    url: tab.url || "",
    title: tab.title || "",
  });
});

chrome.idle.setDetectionInterval(30);
chrome.idle.onStateChanged.addListener((state) => {
  if (state === "active") {
    enqueueEvent({ event_type: EVENT_TYPE.ACTIVE });
  } else if (state === "idle") {
    enqueueEvent({ event_type: EVENT_TYPE.IDLE });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "scroll_event") {
    enqueueEvent({
      event_type: EVENT_TYPE.SCROLL,
      tab_id: String(sender.tab?.id || ""),
      url: sender.tab?.url || "",
      scroll_delta_y: message.scroll_delta_y || 0,
      scroll_total_y: message.scroll_total_y || 0,
    });
    sendResponse({ ok: true });
    return true;
  }

  if (message.type === "tab_hidden") {
    enqueueEvent({
      event_type: EVENT_TYPE.TAB_HIDDEN,
      tab_id: String(sender.tab?.id || ""),
      url: sender.tab?.url || "",
    });
    sendResponse({ ok: true });
    return true;
  }

  if (message.type === "dual_task_result") {
    enqueueEvent(
      {
        event_type: EVENT_TYPE.DUAL_TASK,
        ...message.event,
      },
      { force: true }
    );
    flushEvents();
    sendResponse({ ok: true });
    return true;
  }

  if (message.type === "questionnaire_submit") {
    const payload = {
      type: MSG.QUESTIONNAIRE_RESULTS,
      results: {
        ...message.results,
        session_id: message.results?.session_id || sessionStatus.session_id,
        device_id: deviceId,
        timestamp: nowSec(),
      },
    };

    const sent = sendWS(payload);
    if (!sent) {
      fetch(`${systemAgentHttp}/questionnaire`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload.results),
      }).catch(() => {});
    }

    sendResponse({ ok: true });
    return true;
  }

  if (message.type === "get_state") {
    sendResponse({
      sessionStatus,
      recordingActive,
      connectionOnline,
      deviceId,
    });
    return true;
  }

  return false;
});

chrome.alarms.create(CFG.ALARM_HEARTBEAT, {
  periodInMinutes: CFG.HEARTBEAT_INTERVAL_MINUTES,
});
chrome.alarms.create(CFG.ALARM_FLUSH, {
  periodInMinutes: CFG.FLUSH_INTERVAL_MINUTES,
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === CFG.ALARM_HEARTBEAT) {
    sendHeartbeat();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectWS();
    }
  } else if (alarm.name === CFG.ALARM_FLUSH) {
    flushEvents();
  }
});

(async () => {
  const runtimeConfig = await loadRuntimeConfig();
  const endpoints = buildSystemAgentUrls(runtimeConfig);
  systemAgentHttp = endpoints.httpBaseUrl;
  systemAgentWs = endpoints.websocketUrl;
  await ensureDeviceId();
  await restoreState();
  notifyPopup();
  connectWS();
})();
