const USER_ID = "u1";
const SOURCE_TYPE = "tab";
const MEASUREMENT = "behavior_events";

const FLUSH_INTERVAL_MS = 3000;
const IDLE_TIMEOUT_MS = 5000;
const IDLE_CHECK_INTERVAL_MS = 1000;
const MAX_BATCH_SIZE = 100;
const MAX_BUFFER_EVENTS = 3000;
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 1000;
const NOISE_MIN_DURATION_SECONDS = 0.5;

const REQUIRED_ENV_KEYS = [
  "INFLUX_URL",
  "INFLUX_TOKEN",
  "INFLUX_ORG",
  "INFLUX_BUCKET"
];

const INTEGER_FIELDS = new Set(["scroll_delta"]);

const eventBuffer = [];
const activeTabByWindow = new Map();

let influxConfig = null;
let flushInProgress = false;
let lastFlushMs = Date.now();
let focusedWindowId = chrome.windows.WINDOW_ID_NONE;

const configLoadPromise = loadInfluxConfig();

function nowNs() {
  return `${BigInt(Date.now()) * 1000000n}`;
}

function parseEnvText(text) {
  const env = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex <= 0) {
      continue;
    }

    const key = trimmed.slice(0, separatorIndex).trim();
    let value = trimmed.slice(separatorIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    env[key] = value;
  }
  return env;
}

function hasRequiredConfig(config) {
  return REQUIRED_ENV_KEYS.every((key) => Boolean(config[key]));
}

async function loadInfluxConfig() {
  try {
    const envResponse = await fetch(chrome.runtime.getURL(".env"), {
      cache: "no-store"
    });
    if (envResponse.ok) {
      const envText = await envResponse.text();
      const parsed = parseEnvText(envText);
      if (hasRequiredConfig(parsed)) {
        influxConfig = parsed;
        console.log("Influx config loaded from extension .env");
        return;
      }
    }
  } catch (error) {
    console.warn("Unable to read extension .env:", error);
  }

  const stored = await chrome.storage.local.get(REQUIRED_ENV_KEYS);
  if (hasRequiredConfig(stored)) {
    influxConfig = stored;
    console.log("Influx config loaded from chrome.storage.local");
    return;
  }

  console.error(
    "Influx config missing. Provide extension/.env or save required keys in chrome.storage.local."
  );
}

// Domain-only tracking is insufficient because the URL path/query carries
// semantic task context (issues page vs search page vs repo page).
function parseUrlParts(rawUrl) {
  if (!rawUrl) {
    return { domain: "unknown", path: "/", full_url: "" };
  }

  try {
    const parsed = new URL(rawUrl);
    return {
      domain: parsed.hostname || "unknown",
      path: `${parsed.pathname || "/"}${parsed.search || ""}`,
      full_url: parsed.href
    };
  } catch (_error) {
    return {
      domain: "unknown",
      path: "/",
      full_url: String(rawUrl)
    };
  }
}

function escapeMeasurement(value) {
  return value.replace(/\\/g, "\\\\").replace(/,/g, "\\,").replace(/ /g, "\\ ");
}

function escapeTag(value) {
  return value
    .replace(/\\/g, "\\\\")
    .replace(/,/g, "\\,")
    .replace(/ /g, "\\ ")
    .replace(/=/g, "\\=");
}

function escapeFieldKey(value) {
  return value.replace(/\\/g, "\\\\").replace(/,/g, "\\,").replace(/ /g, "\\ ");
}

function escapeFieldString(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function formatFieldValue(key, value) {
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }

  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      return "0";
    }
    if (INTEGER_FIELDS.has(key)) {
      return `${Math.trunc(value)}i`;
    }
    return `${Number(value)}`;
  }

  return `"${escapeFieldString(value)}"`;
}

function createCurrentEvent(url, nowMs) {
  const parts = parseUrlParts(url);
  return {
    start_time: nowMs,
    domain: parts.domain,
    path: parts.path,
    full_url: parts.full_url,
    scroll_total: 0,
    last_scroll_depth: 0,
    last_activity_ms: nowMs,
    active_accumulated_ms: 0,
    active_started_ms: null
  };
}

function isStateActive(state) {
  return Boolean(state?.window_focused && state?.tab_visible);
}

function syncActiveDurationClock(state, nowMs) {
  if (!state?.currentEvent) {
    return;
  }

  const isActive = isStateActive(state);
  const activeStarted = state.currentEvent.active_started_ms;
  if (isActive && activeStarted == null) {
    state.currentEvent.active_started_ms = nowMs;
    return;
  }

  if (!isActive && activeStarted != null) {
    state.currentEvent.active_accumulated_ms += nowMs - activeStarted;
    state.currentEvent.active_started_ms = null;
  }
}

function createStateFromTab(
  tab,
  nowMs,
  fallbackUrl = "",
  windowFocused = false,
  tabVisible = true
) {
  const resolvedUrl = tab?.url || fallbackUrl || "";
  const currentEvent = createCurrentEvent(resolvedUrl, nowMs);
  if (windowFocused && tabVisible) {
    currentEvent.active_started_ms = nowMs;
  }
  return {
    tab_id: tab?.id ?? -1,
    window_id: tab?.windowId ?? chrome.windows.WINDOW_ID_NONE,
    title: tab?.title || "",
    window_focused: windowFocused,
    tab_visible: tabVisible,
    currentEvent
  };
}

function createEventLinePayload(segment, eventType, durationSeconds) {
  const hasScroll = Math.trunc(Number(segment.scroll_total || 0)) !== 0;
  const normalizedEventType = hasScroll ? "scroll" : eventType;

  const fields = {
    duration: Number(durationSeconds || 0),
    path: segment.path || "/",
    full_url: segment.full_url || ""
  };

  // Scroll fields are included only when real scroll happened.
  // This avoids polluting Influx with zero-value scroll metrics.
  if (hasScroll) {
    fields.scroll_delta = Math.trunc(Number(segment.scroll_total || 0));
    const depth = Number(segment.last_scroll_depth || 0);
    if (depth > 0) {
      fields.scroll_depth = depth;
    }
  }

  return {
    timestamp_ns: nowNs(),
    tags: {
      user_id: USER_ID,
      source_type: SOURCE_TYPE,
      domain: segment.domain || "unknown",
      event_type: normalizedEventType
    }
    ,
    fields
  };
}

function shouldDropEvent(event) {
  const duration = Number(event?.fields?.duration || 0);
  const scrollDelta = Number(event?.fields?.scroll_delta || 0);
  if (event?.tags?.event_type === "switch") {
    return false;
  }
  return duration < NOISE_MIN_DURATION_SECONDS && scrollDelta === 0;
}

// One logical event must produce one line protocol row. Splitting duration,
// scroll and URL fields into separate writes fragments the event graph.
function buildLineProtocol(event) {
  const measurement = escapeMeasurement(MEASUREMENT);
  const tags = Object.entries(event.tags)
    .map(([key, value]) => `${escapeTag(key)}=${escapeTag(String(value))}`)
    .join(",");
  const fields = Object.entries(event.fields)
    .map(([key, value]) => `${escapeFieldKey(key)}=${formatFieldValue(key, value)}`)
    .join(",");

  return `${measurement},${tags} ${fields} ${event.timestamp_ns}`;
}

function logEventToConsole(event, state) {
  console.info("[Behavior Browser Agent]", {
    event_type: event.tags.event_type,
    domain: event.tags.domain,
    path: event.fields.path,
    full_url: event.fields.full_url,
    tab_id: state?.tab_id ?? null,
    window_id: state?.window_id ?? null,
    tab_title: state?.title || "",
    duration: Number(event.fields.duration || 0),
    scroll_delta: Number(event.fields.scroll_delta || 0),
    scroll_depth: Number(event.fields.scroll_depth || 0)
  });
}

function enqueueEvent(event, state) {
  if (shouldDropEvent(event)) {
    return false;
  }

  eventBuffer.push(event);
  logEventToConsole(event, state);

  if (eventBuffer.length > MAX_BUFFER_EVENTS) {
    eventBuffer.splice(0, eventBuffer.length - MAX_BUFFER_EVENTS);
  }

  const elapsedSinceFlush = Date.now() - lastFlushMs;
  if (eventBuffer.length >= MAX_BATCH_SIZE || elapsedSinceFlush >= FLUSH_INTERVAL_MS) {
    void flushBuffer();
  }
  return true;
}

function flushCurrentEvent(state, eventType, nowMs) {
  if (!state?.currentEvent) {
    return;
  }

  syncActiveDurationClock(state, nowMs);
  let activeMs = state.currentEvent.active_accumulated_ms;
  if (isStateActive(state) && state.currentEvent.active_started_ms != null) {
    activeMs += nowMs - state.currentEvent.active_started_ms;
  }
  const durationSeconds = Math.max(activeMs / 1000, 0);
  const event = createEventLinePayload(state.currentEvent, eventType, durationSeconds);
  enqueueEvent(event, state);
}

function rotateSegmentToUrl(state, url, nowMs) {
  if (!state) {
    return;
  }

  if (!state.currentEvent) {
    state.currentEvent = createCurrentEvent(url, nowMs);
    return;
  }

  const nextParts = parseUrlParts(url);
  if (nextParts.full_url === state.currentEvent.full_url) {
    return;
  }

  flushCurrentEvent(state, "switch", nowMs);
  state.currentEvent = {
    ...createCurrentEvent(url, nowMs),
    last_scroll_depth: 0,
    active_started_ms: isStateActive(state) ? nowMs : null
  };
}

function ensureStateForSenderTab(tab, nowMs, fallbackUrl = "") {
  if (!tab || tab.id == null || tab.windowId === chrome.windows.WINDOW_ID_NONE) {
    return null;
  }

  const existing = activeTabByWindow.get(tab.windowId);
  if (!existing) {
    const created = createStateFromTab(
      tab,
      nowMs,
      fallbackUrl,
      tab.windowId === focusedWindowId,
      true
    );
    activeTabByWindow.set(tab.windowId, created);
    return created;
  }

  if (existing.tab_id !== tab.id) {
    return null;
  }

  existing.title = tab.title || existing.title;
  const resolvedUrl = tab.url || fallbackUrl || "";
  if (resolvedUrl) {
    rotateSegmentToUrl(existing, resolvedUrl, nowMs);
  }
  syncActiveDurationClock(existing, nowMs);
  activeTabByWindow.set(tab.windowId, existing);
  return existing;
}

function applyFocusedWindow(windowId) {
  focusedWindowId = windowId;
  const nowMs = Date.now();
  for (const [key, state] of activeTabByWindow.entries()) {
    if (!state) {
      continue;
    }
    state.window_focused = state.window_id === windowId;
    syncActiveDurationClock(state, nowMs);
    activeTabByWindow.set(key, state);
  }
}

async function initializeFocusedWindow() {
  try {
    const windows = await chrome.windows.getAll();
    const focused = windows.find((item) => item.focused);
    applyFocusedWindow(focused ? focused.id : chrome.windows.WINDOW_ID_NONE);
  } catch (_error) {
    applyFocusedWindow(chrome.windows.WINDOW_ID_NONE);
  }
}

async function writePayload(payload) {
  if (!influxConfig) {
    return false;
  }

  const writeUrl =
    `${influxConfig.INFLUX_URL.replace(/\/$/, "")}/api/v2/write` +
    `?org=${encodeURIComponent(influxConfig.INFLUX_ORG)}` +
    `&bucket=${encodeURIComponent(influxConfig.INFLUX_BUCKET)}` +
    "&precision=ns";

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt += 1) {
    try {
      const response = await fetch(writeUrl, {
        method: "POST",
        headers: {
          Authorization: `Token ${influxConfig.INFLUX_TOKEN}`,
          "Content-Type": "text/plain; charset=utf-8"
        },
        body: payload
      });

      if (response.ok) {
        return true;
      }

      const message = await response.text();
      console.warn(
        `Influx write failed (${attempt}/${MAX_RETRIES})`,
        response.status,
        message
      );
    } catch (error) {
      console.warn(`Influx connection error (${attempt}/${MAX_RETRIES})`, error);
    }

    if (attempt < MAX_RETRIES) {
      const delayMs = RETRY_BASE_DELAY_MS * Math.pow(2, attempt - 1);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  return false;
}

async function flushBuffer() {
  if (flushInProgress || eventBuffer.length === 0) {
    return;
  }

  flushInProgress = true;
  try {
    await configLoadPromise;
    if (!influxConfig) {
      return;
    }

    const batch = eventBuffer.splice(0, eventBuffer.length);
    if (batch.length === 0) {
      return;
    }

    const payload = batch.map(buildLineProtocol).join("\n");
    const ok = await writePayload(payload);
    if (!ok) {
      eventBuffer.unshift(...batch);
      if (eventBuffer.length > MAX_BUFFER_EVENTS) {
        eventBuffer.splice(0, eventBuffer.length - MAX_BUFFER_EVENTS);
      }
    } else {
      lastFlushMs = Date.now();
    }
  } finally {
    flushInProgress = false;
  }
}

function checkIdleSegments() {
  const nowMs = Date.now();
  for (const [windowId, state] of activeTabByWindow.entries()) {
    if (!state?.currentEvent) {
      continue;
    }

    const inactiveMs = nowMs - state.currentEvent.last_activity_ms;
    if (inactiveMs >= IDLE_TIMEOUT_MS) {
      flushCurrentEvent(state, "idle", nowMs);
      state.currentEvent = null;
      activeTabByWindow.set(windowId, state);
    }
  }
}

async function bootstrapActiveTabs() {
  const tabs = await chrome.tabs.query({ active: true });
  const nowMs = Date.now();
  for (const tab of tabs) {
    if (tab.id == null || tab.windowId === chrome.windows.WINDOW_ID_NONE) {
      continue;
    }
    activeTabByWindow.set(
      tab.windowId,
      createStateFromTab(tab, nowMs, "", tab.windowId === focusedWindowId, true)
    );
  }
}

async function handleTabActivated(activeInfo) {
  const tab = await chrome.tabs.get(activeInfo.tabId);
  const nowMs = Date.now();

  const previous = activeTabByWindow.get(activeInfo.windowId);
  if (previous && previous.tab_id !== activeInfo.tabId) {
    flushCurrentEvent(previous, "switch", nowMs);
  }

  activeTabByWindow.set(activeInfo.windowId, createStateFromTab(tab, nowMs));
  const created = activeTabByWindow.get(activeInfo.windowId);
  if (created) {
    created.window_focused = activeInfo.windowId === focusedWindowId;
    created.tab_visible = true;
    syncActiveDurationClock(created, nowMs);
    activeTabByWindow.set(activeInfo.windowId, created);
  }
}

function handleTabUpdated(tabId, changeInfo, tab) {
  if (!tab?.active) {
    return;
  }

  const state = activeTabByWindow.get(tab.windowId);
  if (!state || state.tab_id !== tabId) {
    return;
  }

  if (changeInfo.title) {
    state.title = changeInfo.title;
  }
  if (changeInfo.url) {
    rotateSegmentToUrl(state, changeInfo.url, Date.now());
  }
  activeTabByWindow.set(tab.windowId, state);
}

function handleTabRemoved(tabId, removeInfo) {
  const state = activeTabByWindow.get(removeInfo.windowId);
  if (!state || state.tab_id !== tabId) {
    return;
  }

  flushCurrentEvent(state, "switch", Date.now());
  activeTabByWindow.delete(removeInfo.windowId);
}

chrome.tabs.onActivated.addListener((activeInfo) => {
  void handleTabActivated(activeInfo);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  handleTabUpdated(tabId, changeInfo, tab);
});

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  handleTabRemoved(tabId, removeInfo);
});

chrome.windows.onFocusChanged.addListener((windowId) => {
  applyFocusedWindow(windowId);
});

chrome.runtime.onMessage.addListener((message, sender) => {
  if (!sender.tab || sender.tab.id == null) {
    return;
  }

  const nowMs = Date.now();
  const state = ensureStateForSenderTab(sender.tab, nowMs, message?.url || "");
  if (!state) {
    return;
  }

  if (!state.currentEvent) {
    const resolvedUrl = sender.tab.url || message?.url || "";
    state.currentEvent = createCurrentEvent(resolvedUrl, nowMs);
    if (isStateActive(state)) {
      state.currentEvent.active_started_ms = nowMs;
    }
  }

  if (message?.type === "page_visibility") {
    state.tab_visible = Boolean(message.visible);
    syncActiveDurationClock(state, nowMs);
    activeTabByWindow.set(sender.tab.windowId, state);
    return;
  }

  if (message?.type === "page_interrupt") {
    state.currentEvent.last_activity_ms = nowMs;
    syncActiveDurationClock(state, nowMs);
    activeTabByWindow.set(sender.tab.windowId, state);
    return;
  }

  if (message?.type !== "page_activity") {
    return;
  }

  // No write in scroll path: we aggregate into currentEvent and flush only on
  // switch/url-change/idle timeout.
  const scrollDelta = Math.trunc(Number(message.scroll_delta || 0));
  if (scrollDelta !== 0) {
    const scrollDepth = Number(message.scroll_depth || 0);
    state.currentEvent.scroll_total += scrollDelta;
    state.currentEvent.last_scroll_depth = Math.max(
      state.currentEvent.last_scroll_depth,
      scrollDepth
    );
    state.currentEvent.last_activity_ms = nowMs;
    syncActiveDurationClock(state, nowMs);
    activeTabByWindow.set(sender.tab.windowId, state);
  }
});

chrome.runtime.onInstalled.addListener(() => {
  void bootstrapActiveTabs();
});

chrome.runtime.onStartup.addListener(() => {
  void bootstrapActiveTabs();
});

async function initializeAgentState() {
  await initializeFocusedWindow();
  await bootstrapActiveTabs();
}

void initializeAgentState();

setInterval(() => {
  void flushBuffer();
}, FLUSH_INTERVAL_MS);

setInterval(() => {
  checkIdleSegments();
}, IDLE_CHECK_INTERVAL_MS);
