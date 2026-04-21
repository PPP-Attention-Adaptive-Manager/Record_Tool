import { formatTime } from "../shared/utils.js";

const stateEl = document.getElementById("session-state");
const sessionIdEl = document.getElementById("session-id");
const modeEl = document.getElementById("mode");
const elapsedEl = document.getElementById("elapsed");
const remainingEl = document.getElementById("remaining");
const recordingEl = document.getElementById("recording-active");
const connectionEl = document.getElementById("connection-indicator");
const deviceEl = document.getElementById("device-id");

function setConnection(online) {
  connectionEl.textContent = online ? "online" : "offline";
  connectionEl.classList.toggle("online", Boolean(online));
  connectionEl.classList.toggle("offline", !online);
}

function render(snapshot) {
  const status = snapshot?.sessionStatus || {};
  stateEl.textContent = String(status.state || "inactive");
  sessionIdEl.textContent = status.session_id || "-";
  modeEl.textContent = status.mode || "-";
  elapsedEl.textContent = formatTime(status.elapsed_time || 0);
  remainingEl.textContent = formatTime(status.remaining_time || 0);
  recordingEl.textContent = snapshot?.recordingActive ? "active" : "inactive";
  deviceEl.textContent = snapshot?.deviceId || "-";
  setConnection(Boolean(snapshot?.connectionOnline));
}

chrome.runtime.sendMessage({ type: "get_state" }, (response) => {
  if (chrome.runtime.lastError || !response) {
    setConnection(false);
    return;
  }
  render(response);
});

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "session_snapshot") {
    render(message);
  }
});

