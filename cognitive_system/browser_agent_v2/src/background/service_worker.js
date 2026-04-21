import { handleCommand } from "./command_handler.js";
import { EventBuffer } from "./event_buffer.js";
import { Sender } from "./sender.js";
import { RecordingStateManager } from "./state_manager.js";
import { parseUrl } from "./url_parser.js";

const CONFIG = {
  baseUrl: "http://127.0.0.1:5000",
  heartbeatIntervalMs: 2000,
  sendIntervalMs: 1200,
  maxBatchSize: 200
};

const stateManager = new RecordingStateManager();
const eventBuffer = new EventBuffer();
const sender = new Sender(CONFIG.baseUrl);

let deviceId = null;
const browserId = detectBrowserId();
let activeTabId = null;
let heartbeatTimer = null;
let sendTimer = null;
let initialized = false;

function detectBrowserId() {
  const userAgent = navigator.userAgent || "";
  if (userAgent.includes("Edg/")) return "edge";
  if (userAgent.includes("Firefox/")) return "firefox";
  if (userAgent.includes("OPR/")) return "opera";
  return "chrome";
}

function generateDeviceId() {
  if (crypto && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `device_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`;
}

async function getOrCreateDeviceId() {
  const stored = await chrome.storage.local.get(["device_id"]);
  if (stored.device_id) {
    return stored.device_id;
  }
  const created = generateDeviceId();
  await chrome.storage.local.set({ device_id: created });
  return created;
}

function buildEvent(eventType, extra = {}, forcedSessionId = null) {
  const sessionId = forcedSessionId || stateManager.getSessionId();
  return {
    event_type: eventType,
    timestamp_ms: Date.now(),
    session_id: sessionId,
    ...extra
  };
}

async function getTabSafe(tabId) {
  if (tabId === null || tabId === undefined) {
    return null;
  }
  try {
    return await chrome.tabs.get(tabId);
  } catch (_error) {
    return null;
  }
}

function tabFields(tab, fallbackTabId = null) {
  const parsed = parseUrl(tab?.url || "");
  return {
    tab_id: tab?.id ?? fallbackTabId ?? "unknown",
    tab_title: tab?.title || "",
    ...parsed
  };
}

function appendTabEvent(eventType, tab, extra = {}) {
  if (!stateManager.isRecording()) {
    return;
  }
  eventBuffer.appendEvent(buildEvent(eventType, { ...tabFields(tab, activeTabId), ...extra }));
}

async function processHeartbeat() {
  if (!deviceId) {
    return;
  }
  const response = await sender.heartbeat({
    device_id: deviceId,
    browser_id: browserId,
    session_id: stateManager.getSessionId(),
    extension_timestamp_ms: Date.now(),
    state: stateManager.getState()
  });

  if (!response || !Array.isArray(response.commands)) {
    return;
  }

  for (const commandEnvelope of response.commands) {
    handleCommand(commandEnvelope, stateManager, eventBuffer);
  }
}

async function flushBatches() {
  if (!deviceId) {
    return;
  }

  for (let i = 0; i < 5; i += 1) {
    const batch = eventBuffer.peekBatch(CONFIG.maxBatchSize);
    if (batch.length === 0) {
      return;
    }

    const batchSessionId = batch[0]?.session_id || stateManager.getSessionId() || null;
    const response = await sender.sendEvents({
      device_id: deviceId,
      browser_id: browserId,
      session_id: batchSessionId,
      events: batch
    });
    if (!response || response.status !== "ok") {
      return;
    }
    eventBuffer.dropBatch(batch.length);
  }
}

function registerRuntimeMessageListener() {
  chrome.runtime.onMessage.addListener((message, senderContext) => {
    if (!message || !message.kind || !stateManager.isRecording()) {
      return;
    }

    const sessionId = stateManager.getSessionId();
    const tabId = senderContext?.tab?.id ?? message?.payload?.tab_id ?? activeTabId ?? "unknown";
    const rawUrl = message?.payload?.url || senderContext?.tab?.url || "";
    const parsed = parseUrl(rawUrl);

    if (message.kind === "content_scroll_update") {
      eventBuffer.updateScroll({
        session_id: sessionId,
        tab_id: tabId,
        timestamp_ms: message?.payload?.timestamp_ms || Date.now(),
        scroll_delta: message?.payload?.scroll_delta || 0,
        scroll_depth: message?.payload?.scroll_depth || 0,
        scroll_event_count: message?.payload?.scroll_event_count || 1,
        ...parsed
      });
      return;
    }

    if (message.kind === "content_activity") {
      const activityType = message?.payload?.type || "focus";
      if (activityType === "tab_hidden") {
        eventBuffer.flushScroll(tabId, "tab_hidden", { session_id: sessionId });
      } else if (activityType === "idle") {
        eventBuffer.flushAllScroll("idle", { session_id: sessionId });
      } else if (activityType === "background") {
        eventBuffer.flushAllScroll("background", { session_id: sessionId });
      } else if (activityType === "focus") {
        eventBuffer.flushAllScroll("focus_checkpoint", { session_id: sessionId });
      }

      eventBuffer.appendEvent(
        buildEvent(activityType, {
          tab_id: tabId,
          ...parsed
        })
      );
    }
  });
}

function registerTabListeners() {
  chrome.tabs.onCreated.addListener((tab) => {
    appendTabEvent("new_tab", tab, {});
  });

  chrome.tabs.onActivated.addListener(async (activeInfo) => {
    const previousTabId = activeTabId;
    activeTabId = activeInfo.tabId;

    if (!stateManager.isRecording()) {
      return;
    }

    if (previousTabId !== null && previousTabId !== activeTabId) {
      eventBuffer.flushScroll(previousTabId, "switch", {
        session_id: stateManager.getSessionId()
      });
    }

    const tab = await getTabSafe(activeInfo.tabId);
    appendTabEvent("switch", tab, {
      previous_tab_id: previousTabId === null ? "" : previousTabId
    });
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (!stateManager.isRecording()) {
      return;
    }
    if (changeInfo.url) {
      appendTabEvent("navigation", tab, {});
    }
  });

  chrome.tabs.onRemoved.addListener((tabId) => {
    if (!stateManager.isRecording()) {
      return;
    }
    eventBuffer.flushScroll(tabId, "tab_removed", { session_id: stateManager.getSessionId() });
    if (activeTabId === tabId) {
      activeTabId = null;
    }
  });
}

function registerWindowListeners() {
  chrome.windows.onFocusChanged.addListener((windowId) => {
    if (!stateManager.isRecording()) {
      return;
    }
    const sessionId = stateManager.getSessionId();
    if (windowId === chrome.windows.WINDOW_ID_NONE) {
      eventBuffer.flushAllScroll("background", { session_id: sessionId });
      eventBuffer.appendEvent(
        buildEvent("background", {
          window_id: "none"
        })
      );
      return;
    }
    eventBuffer.appendEvent(
      buildEvent("focus", {
        window_id: windowId
      })
    );
  });
}

function registerIdleListeners() {
  chrome.idle.onStateChanged.addListener((newState) => {
    if (!stateManager.isRecording()) {
      return;
    }
    const sessionId = stateManager.getSessionId();
    if (newState === "active") {
      eventBuffer.appendEvent(
        buildEvent("focus", {
          focus_reason: "idle_active"
        })
      );
      return;
    }

    eventBuffer.flushAllScroll("idle", { session_id: sessionId });
    eventBuffer.appendEvent(
      buildEvent("idle", {
        idle_state: newState
      })
    );
  });
}

function startLoops() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
  }
  if (sendTimer) {
    clearInterval(sendTimer);
  }

  heartbeatTimer = setInterval(() => {
    void processHeartbeat();
  }, CONFIG.heartbeatIntervalMs);
  sendTimer = setInterval(() => {
    void flushBatches();
  }, CONFIG.sendIntervalMs);

  void processHeartbeat();
  void flushBatches();
}

async function initialize() {
  if (initialized) {
    return;
  }
  initialized = true;

  deviceId = await getOrCreateDeviceId();
  registerRuntimeMessageListener();
  registerTabListeners();
  registerWindowListeners();
  registerIdleListeners();
  startLoops();
}

chrome.runtime.onInstalled.addListener(() => {
  void initialize();
});

chrome.runtime.onStartup.addListener(() => {
  void initialize();
});

void initialize();

