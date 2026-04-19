/**
 * Popup controller.
 *
 * Extension pages support ES modules natively — no bundler needed here.
 * Communicates with the service worker via chrome.runtime.sendMessage.
 */

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dot        = document.getElementById('state-dot');
const statusPanel = document.getElementById('status-panel');
const startForm  = document.getElementById('start-form');
const btnStart   = document.getElementById('btn-start');
const btnStop    = document.getElementById('btn-stop');
const inpDur     = document.getElementById('inp-duration');
const inpUid     = document.getElementById('inp-uid');
const statusState = document.getElementById('status-state');
const statusSid  = document.getElementById('status-sid');
const statusTime = document.getElementById('status-time');
const errorMsg   = document.getElementById('error-msg');

// ── State ─────────────────────────────────────────────────────────────────────
let statusInterval = null;

// ── Init ──────────────────────────────────────────────────────────────────────
refreshStatus();

// ── Event listeners ────────────────────────────────────────────────────────────
btnStart.addEventListener('click', onStartClick);
btnStop.addEventListener('click',  onStopClick);

// ── Functions ─────────────────────────────────────────────────────────────────

async function onStartClick() {
  clearError();
  const duration = parseInt(inpDur.value, 10);
  const userId   = inpUid.value.trim() || 'anonymous';

  if (!duration || duration < 1 || duration > 180) {
    showError('Duration must be 1–180 minutes.');
    return;
  }

  btnStart.disabled = true;

  const res = await send({ type: 'START_SESSION', payload: { duration_minutes: duration, user_id: userId } });

  if (res?.ok) {
    refreshStatus();
    startPolling();
  } else {
    showError('Could not start session. Is the extension loaded correctly?');
    btnStart.disabled = false;
  }
}

async function onStopClick() {
  btnStop.disabled = true;
  await send({ type: 'STOP_SESSION' });
  stopPolling();
  refreshStatus();
  btnStop.disabled = false;
}

async function refreshStatus() {
  const status = await send({ type: 'GET_STATUS' });
  if (!status) return;

  const active = ['running', 'hidden', 'background', 'idle'].includes(status.state);

  // Dot color
  dot.className = `dot ${status.state}`;

  if (active) {
    startForm.style.display  = 'none';
    btnStop.style.display    = 'block';
    statusPanel.classList.add('visible');

    statusState.textContent = status.state;
    statusSid.textContent   = status.session_id
      ? status.session_id.slice(0, 8) + '…'
      : '—';

    if (status.session_start_ms && status.session_duration_ms) {
      const elapsed   = Date.now() - status.session_start_ms;
      const remaining = Math.max(0, status.session_duration_ms - elapsed);
      statusTime.textContent = formatDuration(remaining);
    }

    startPolling();
  } else {
    startForm.style.display  = '';
    btnStop.style.display    = 'none';
    btnStart.disabled        = false;
    statusPanel.classList.remove('visible');
    stopPolling();
  }
}

function startPolling() {
  if (statusInterval) return;
  statusInterval = setInterval(refreshStatus, 3_000);
}

function stopPolling() {
  if (!statusInterval) return;
  clearInterval(statusInterval);
  statusInterval = null;
}

function showError(msg) {
  errorMsg.textContent = msg;
  errorMsg.classList.add('visible');
}

function clearError() {
  errorMsg.textContent = '';
  errorMsg.classList.remove('visible');
}

function formatDuration(ms) {
  const totalSec = Math.floor(ms / 1_000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

/**
 * Wrapper around chrome.runtime.sendMessage that returns null on error
 * instead of throwing (handles the SW-killed case gracefully).
 */
function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        console.warn('[popup] SW message error:', chrome.runtime.lastError.message);
        resolve(null);
      } else {
        resolve(response);
      }
    });
  });
}
