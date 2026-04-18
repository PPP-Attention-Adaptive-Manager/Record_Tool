const USER_ID = "u1";
const SOURCE_TYPE = "tab";
const MEASUREMENT = "behavior_events";

const BATCH_INTERVAL_MS = 3000;
const MAX_BATCH_SIZE = 100;
const MAX_BUFFER_EVENTS = 3000;
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 1000;
const INTEGER_FIELDS = new Set(["scroll_delta", "keystrokes", "clicks"]);

const REQUIRED_ENV_KEYS = [
  "INFLUX_URL",
  "INFLUX_TOKEN",
  "INFLUX_ORG",
  "INFLUX_BUCKET"
];

const eventBuffer = [];
const activeTabByWindow = new Map();
const activityByTab = new Map();

let influxConfig = null;
let flushInProgress = false;
let lastFlushMs = Date.now();

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

function extractDomain(url) {
  if (!url) {
    return "unknown";
  }
  try {
    const parsed = new URL(url);
    return parsed.hostname || "unknown";
  } catch (_error) {
    return "unknown";
  }
}

function getActivity(tabId) {
  if (!activityByTab.has(tabId)) {
    activityByTab.set(tabId, { clicks: 0, keystrokes: 0 });
  }
  return activityByTab.get(tabId);
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

  const text = String(value)
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"');
  return `"${text}"`;
}

function createEvent(domain, eventType, metrics = {}) {
  const safeDomain = domain || "unknown";
  const safeMetrics = {
    duration: Number(metrics.duration || 0),
    scroll_delta: Number(metrics.scroll_delta || 0),
    scroll_depth: Number(metrics.scroll_depth || 0),
    keystrokes: Number(metrics.keystrokes || 0),
    clicks: Number(metrics.clicks || 0)
  };

  return {
    timestamp_ns: nowNs(),
    tags: {
      user_id: USER_ID,
      source_type: SOURCE_TYPE,
      domain: safeDomain,
      event_type: eventType
    },
    fields: safeMetrics
  };
}

function toLineProtocol(event) {
  const measurement = escapeMeasurement(MEASUREMENT);
  const tags = Object.entries(event.tags)
    .map(([key, value]) => `${escapeTag(key)}=${escapeTag(String(value))}`)
    .join(",");

  const fields = Object.entries(event.fields)
    .map(([key, value]) => `${escapeFieldKey(key)}=${formatFieldValue(key, value)}`)
    .join(",");

  return `${measurement},${tags} ${fields} ${event.timestamp_ns}`;
}

function enqueueEvent(event) {
  eventBuffer.push(event);
  if (eventBuffer.length > MAX_BUFFER_EVENTS) {
    eventBuffer.splice(0, eventBuffer.length - MAX_BUFFER_EVENTS);
  }

  const elapsed = Date.now() - lastFlushMs;
  if (eventBuffer.length >= MAX_BATCH_SIZE || elapsed >= BATCH_INTERVAL_MS) {
    void flushBuffer();
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
      const delay = RETRY_BASE_DELAY_MS * Math.pow(2, attempt - 1);
      await new Promise((resolve) => setTimeout(resolve, delay));
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

    const payload = batch.map(toLineProtocol).join("\n");
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

function emitFocusHeartbeat() {
  const now = Date.now();

  for (const [windowId, state] of activeTabByWindow.entries()) {
    if (!state) {
      continue;
    }

    const elapsedSec = Math.max((now - state.started_at_ms) / 1000, 0);
    const activity = getActivity(state.tab_id);

    enqueueEvent(
      createEvent(state.domain, "focus", {
        duration: elapsedSec,
        keystrokes: activity.keystrokes,
        clicks: activity.clicks
      })
    );

    state.started_at_ms = now;
    activeTabByWindow.set(windowId, state);
    activity.keystrokes = 0;
    activity.clicks = 0;
  }
}

async function bootstrapActiveTabs() {
  const tabs = await chrome.tabs.query({ active: true });
  const now = Date.now();
  for (const tab of tabs) {
    if (tab.windowId === chrome.windows.WINDOW_ID_NONE || tab.id == null) {
      continue;
    }
    activeTabByWindow.set(tab.windowId, {
      tab_id: tab.id,
      domain: extractDomain(tab.url),
      started_at_ms: now
    });
  }
}

async function handleTabActivated(activeInfo) {
  const tab = await chrome.tabs.get(activeInfo.tabId);
  const now = Date.now();
  const domain = extractDomain(tab.url);
  const previous = activeTabByWindow.get(activeInfo.windowId);

  if (previous && previous.tab_id !== activeInfo.tabId) {
    const elapsedSec = Math.max((now - previous.started_at_ms) / 1000, 0);
    const activity = getActivity(previous.tab_id);

    enqueueEvent(
      createEvent(previous.domain, "switch", {
        duration: elapsedSec,
        keystrokes: activity.keystrokes,
        clicks: activity.clicks
      })
    );

    activity.keystrokes = 0;
    activity.clicks = 0;
  }

  activeTabByWindow.set(activeInfo.windowId, {
    tab_id: activeInfo.tabId,
    domain,
    started_at_ms: now
  });

  enqueueEvent(createEvent(domain, "focus", { duration: 0 }));
}

function handleTabUpdated(tabId, changeInfo, tab) {
  if (!tab.active || !changeInfo.url) {
    return;
  }

  const current = activeTabByWindow.get(tab.windowId);
  if (!current || current.tab_id !== tabId) {
    return;
  }

  const newDomain = extractDomain(changeInfo.url);
  if (newDomain === current.domain) {
    return;
  }

  const now = Date.now();
  const elapsedSec = Math.max((now - current.started_at_ms) / 1000, 0);
  const activity = getActivity(tabId);

  enqueueEvent(
    createEvent(current.domain, "switch", {
      duration: elapsedSec,
      keystrokes: activity.keystrokes,
      clicks: activity.clicks
    })
  );

  activity.keystrokes = 0;
  activity.clicks = 0;
  current.domain = newDomain;
  current.started_at_ms = now;
  activeTabByWindow.set(tab.windowId, current);

  enqueueEvent(createEvent(newDomain, "focus", { duration: 0 }));
}

function handleTabRemoved(tabId, removeInfo) {
  const current = activeTabByWindow.get(removeInfo.windowId);
  if (!current || current.tab_id !== tabId) {
    activityByTab.delete(tabId);
    return;
  }

  const now = Date.now();
  const elapsedSec = Math.max((now - current.started_at_ms) / 1000, 0);
  const activity = getActivity(tabId);

  enqueueEvent(
    createEvent(current.domain, "switch", {
      duration: elapsedSec,
      keystrokes: activity.keystrokes,
      clicks: activity.clicks
    })
  );

  activeTabByWindow.delete(removeInfo.windowId);
  activityByTab.delete(tabId);
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

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== "page_activity" || !sender.tab || sender.tab.id == null) {
    return;
  }

  const tabId = sender.tab.id;
  const domain = extractDomain(message.url || sender.tab.url);
  const activity = getActivity(tabId);

  activity.clicks += Number(message.clicks || 0);
  activity.keystrokes += Number(message.keystrokes || 0);

  const scrollDelta = Number(message.scroll_delta || 0);
  const scrollDepth = Number(message.scroll_depth || 0);
  if (scrollDelta !== 0 || scrollDepth > 0) {
    enqueueEvent(
      createEvent(domain, "scroll", {
        scroll_delta: scrollDelta,
        scroll_depth: scrollDepth
      })
    );
  }
});

chrome.runtime.onInstalled.addListener(() => {
  void bootstrapActiveTabs();
});

chrome.runtime.onStartup.addListener(() => {
  void bootstrapActiveTabs();
});

void bootstrapActiveTabs();

setInterval(() => {
  emitFocusHeartbeat();
  void flushBuffer();
}, BATCH_INTERVAL_MS);
