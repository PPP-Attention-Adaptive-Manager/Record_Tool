import { CORE_CONFIG, STORAGE_KEYS } from '../shared/constants.js';

const dot = document.getElementById('state-dot');
const statusPanel = document.getElementById('status-panel');
const startForm = document.getElementById('start-form');
const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const inpDur = document.getElementById('inp-duration');
const inpUid = document.getElementById('inp-uid');
const inpEnableInflux = document.getElementById('inp-enable-influx');
const btnQuitCore = document.getElementById('btn-quit-core');
const coreNotice = document.getElementById('core-notice');
const quitHint = document.getElementById('quit-hint');
const statusState = document.getElementById('status-state');
const statusSid = document.getElementById('status-sid');
const statusTime = document.getElementById('status-time');
const statusConn = document.getElementById('status-conn');
const statusQueue = document.getElementById('status-queue');
const errorMsg = document.getElementById('error-msg');

const btnToggle = document.getElementById('btn-toggle-settings');
const settingsPane = document.getElementById('settings-panel');
const cfgUrl = document.getElementById('cfg-url');
const btnSaveCfg = document.getElementById('btn-save-cfg');
const cfgSavedMsg = document.getElementById('cfg-saved-msg');

let statusInterval = null;

loadConfig().then(renderConfig);
refreshStatus();

btnStart.addEventListener('click', onStartClick);
btnStop.addEventListener('click', onStopClick);
btnQuitCore.addEventListener('click', onQuitCoreClick);
btnToggle.addEventListener('click', toggleSettings);
btnSaveCfg.addEventListener('click', onSaveConfig);

async function onStartClick() {
  clearError();

  const duration = parseInt(inpDur.value, 10);
  const userId = inpUid.value.trim();
  if (!/^P\d{3}$/.test(userId)) {
    showError('Participant ID must look like P001.');
    return;
  }
  if (!duration || duration < 1 || duration > 180) {
    showError('Duration must be 1-180 minutes.');
    return;
  }

  btnStart.disabled = true;
  const response = await send({
    type: 'START_SESSION',
    payload: {
      duration_minutes: duration,
      user_id: userId,
      enable_influx: inpEnableInflux.checked,
    },
  });

  if (response?.ok) {
    await refreshStatus();
    startPolling();
  } else {
    showError(response?.error || 'Could not start session.');
    btnStart.disabled = false;
  }
}

async function onStopClick() {
  btnStop.disabled = true;
  const response = await send({ type: 'STOP_SESSION' });
  if (!response?.ok) {
    showError(response?.error || 'Could not stop session.');
  }
  stopPolling();
  await refreshStatus();
  btnStop.disabled = false;
}

async function onQuitCoreClick() {
  clearError();
  if (btnQuitCore.classList.contains('hidden') || btnQuitCore.disabled) {
    return;
  }
  btnQuitCore.disabled = true;

  let response = await send({ type: 'QUIT_CORE', payload: { force: false } });
  if (!response?.ok && /Stop the active session|active session/i.test(response?.error || '')) {
    const force = window.confirm('A session is still active. Force-stop the session and quit the core?');
    if (force) {
      response = await send({ type: 'QUIT_CORE', payload: { force: true } });
    }
  }

  if (!response?.ok) {
    showError(response?.error || 'Could not quit the core.');
  } else {
    stopPolling();
    await refreshStatus();
  }

  btnQuitCore.disabled = false;
}

async function refreshStatus() {
  const status = await send({ type: 'GET_STATUS' });
  if (!status) {
    dot.className = 'dot finished';
    statusPanel.classList.add('visible');
    statusState.textContent = 'offline';
    statusSid.textContent = '-';
    statusTime.textContent = '-';
    statusConn.textContent = 'service worker unavailable';
    statusQueue.textContent = '-';
    renderCoreNotice('offline', 'The extension cannot reach its background worker right now.');
    btnStop.style.display = 'none';
    startForm.style.display = '';
    btnStart.disabled = false;
    btnQuitCore.classList.remove('hidden');
    btnQuitCore.disabled = true;
    quitHint.textContent = 'Reload the extension if this state persists.';
    return;
  }

  const active = ['running', 'hidden', 'background', 'idle'].includes(status.state);
  const connectionState = status.transport?.connection_state || 'idle';
  dot.className = `dot ${status.state}`;

  statusConn.textContent = humanizeConnectionState(connectionState);
  statusQueue.textContent = String(status.transport?.queued_events ?? 0);
  renderTransportNotice(connectionState, status.transport?.queued_events ?? 0);

  if (active) {
    startForm.style.display = 'none';
    btnStop.style.display = 'block';
    btnQuitCore.classList.add('hidden');
    btnQuitCore.disabled = true;
    quitHint.textContent = 'Stop the session before quitting the core.';
    statusPanel.classList.add('visible');

    statusState.textContent = status.state;
    statusSid.textContent = status.session_id
      ? `${status.session_id.slice(0, 18)}...`
      : '-';

    if (status.session_start_ms && status.session_duration_ms) {
      const remaining = Math.max(
        0,
        status.session_duration_ms - (Date.now() - status.session_start_ms),
      );
      statusTime.textContent = formatDuration(remaining);
    } else {
      statusTime.textContent = '-';
    }
    startPolling();
  } else {
    startForm.style.display = '';
    btnStop.style.display = 'none';
    btnStart.disabled = false;
    btnQuitCore.classList.remove('hidden');
    btnQuitCore.disabled = connectionState === 'offline';
    quitHint.textContent = connectionState === 'offline'
      ? 'Core already looks offline.'
      : 'This stops the local Python core, not just the current session.';
    statusPanel.classList.add('visible');
    statusState.textContent = connectionState === 'offline' ? 'offline' : 'ready';
    statusSid.textContent = '-';
    statusTime.textContent = '-';
    stopPolling();
  }
}

function startPolling() {
  if (statusInterval) {
    return;
  }
  statusInterval = setInterval(refreshStatus, 3_000);
}

function stopPolling() {
  if (!statusInterval) {
    return;
  }
  clearInterval(statusInterval);
  statusInterval = null;
}

function toggleSettings() {
  const open = settingsPane.style.display !== 'none';
  settingsPane.style.display = open ? 'none' : 'block';
  btnToggle.textContent = open ? 'Core settings' : 'Hide settings';
}

async function loadConfig() {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.CORE_CONFIG);
  return { ...CORE_CONFIG, ...(stored[STORAGE_KEYS.CORE_CONFIG] ?? {}) };
}

function renderConfig(cfg) {
  cfgUrl.value = cfg.URL ?? CORE_CONFIG.URL;
}

async function onSaveConfig() {
  cfgSavedMsg.style.display = 'none';
  const newCfg = {
    URL: cfgUrl.value.trim() || CORE_CONFIG.URL,
  };
  await chrome.storage.local.set({ [STORAGE_KEYS.CORE_CONFIG]: newCfg });
  cfgSavedMsg.style.display = 'block';
  setTimeout(() => {
    cfgSavedMsg.style.display = 'none';
  }, 2_000);
}

function showError(message) {
  errorMsg.textContent = message;
  errorMsg.classList.add('visible');
}

function clearError() {
  errorMsg.textContent = '';
  errorMsg.classList.remove('visible');
}

function renderTransportNotice(connectionState, queuedEvents) {
  if (connectionState === 'offline') {
    renderCoreNotice(
      'offline',
      queuedEvents > 0
        ? `Core is offline. ${queuedEvents} event(s) are queued and will retry automatically.`
        : 'Core is offline. Start the Python core to begin or resume syncing.',
    );
    return;
  }

  if (connectionState === 'buffering') {
    renderCoreNotice(
      'buffering',
      queuedEvents > 0
        ? `Sync is in progress. ${queuedEvents} queued event(s) are waiting to flush.`
        : 'Preparing to sync events to the local core.',
    );
    return;
  }

  renderCoreNotice('online', 'Core connection looks healthy.');
}

function renderCoreNotice(kind, message) {
  coreNotice.textContent = message;
  coreNotice.className = `notice visible ${kind}`;
}

function humanizeConnectionState(connectionState) {
  switch (connectionState) {
    case 'online':
      return 'online';
    case 'buffering':
      return 'buffering';
    case 'offline':
      return 'offline';
    default:
      return 'idle';
  }
}

function formatDuration(ms) {
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}m ${(seconds % 60).toString().padStart(2, '0')}s`;
}

function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        console.warn('[popup]', chrome.runtime.lastError.message);
        resolve(null);
      } else {
        resolve(response);
      }
    });
  });
}
