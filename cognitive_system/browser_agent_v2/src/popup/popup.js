import { STATES } from "../shared/constants.js";
import { formatTime } from "../shared/utils.js";

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
const $state      = document.getElementById("session-state");
const $elapsed    = document.getElementById("elapsed-time");
const $remaining  = document.getElementById("remaining-time");
const $progress   = document.getElementById("progress-bar");
const $timingBlk  = document.getElementById("timing-block");
const $idRow      = document.getElementById("session-id-row");
const $idVal      = document.getElementById("session-id-val");
const $connDot    = document.getElementById("connection-dot");
const $deviceId   = document.getElementById("device-id-val");

// ---------------------------------------------------------------------------
// Render helpers
// ---------------------------------------------------------------------------

function renderState(state, sessionInfo) {
  // Badge
  $state.className = "badge";
  if (state === STATES.RECORDING) {
    $state.textContent = "Recording";
    $state.classList.add("badge-recording");
  } else if (state === STATES.PAUSED) {
    $state.textContent = "Paused";
    $state.classList.add("badge-paused");
  } else {
    $state.textContent = "Inactive";
    $state.classList.add("badge-idle");
  }

  // Show/hide timing block
  if (state !== STATES.IDLE && sessionInfo?.session_id) {
    $timingBlk.classList.remove("hidden");
    $idRow.classList.remove("hidden");
    renderTiming(sessionInfo);
    $idVal.textContent = sessionInfo.session_id;
  } else {
    $timingBlk.classList.add("hidden");
    $idRow.classList.add("hidden");
  }
}

function renderTiming(si) {
  $elapsed.textContent   = formatTime(si.elapsed_time || 0);
  $remaining.textContent = si.remaining_time != null ? formatTime(si.remaining_time) : "--:--";

  const pct = si.duration > 0
    ? Math.min(100, ((si.elapsed_time || 0) / si.duration) * 100)
    : 0;
  $progress.style.width = pct.toFixed(1) + "%";
}

function setConnected(online) {
  $connDot.className = "conn-dot " + (online ? "conn-online" : "conn-offline");
}

// ---------------------------------------------------------------------------
// Bootstrap: ask background for current state
// ---------------------------------------------------------------------------

chrome.runtime.sendMessage({ type: "get_state" }, (resp) => {
  if (chrome.runtime.lastError || !resp) {
    setConnected(false);
    return;
  }
  setConnected(true);
  renderState(resp.state, resp.sessionInfo);
  if (resp.deviceId) $deviceId.textContent = resp.deviceId;
});

// ---------------------------------------------------------------------------
// Live updates from background
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "state_change") {
    setConnected(true);
    renderState(msg.state, msg.sessionInfo);
  } else if (msg.type === "session_update") {
    setConnected(true);
    renderTiming(msg.sessionInfo);
    // Also refresh state badge in case state changed
    if (msg.sessionInfo?.state) {
      renderState(msg.sessionInfo.state, msg.sessionInfo);
    }
  }
});
