/**
 * Service Worker entry point (Manifest V3).
 *
 * This file is intentionally thin: it only wires Chrome API events to the
 * SessionManager.  All session logic lives in session_manager.js.
 *
 * Lifecycle note:
 *   MV3 service workers are killed after ~30 s of inactivity and restarted on
 *   the next event.  SessionManager.restoreIfNeeded() re-hydrates state from
 *   chrome.storage.local on every start-up so no data is lost.
 */
import { SessionManager } from './session_manager.js';

// Singleton — survives for the lifetime of this SW instance
const manager = new SessionManager();

// ─────────────────────────────────────────────────────────────────────────────
// Service worker start-up: restore any active session
// ─────────────────────────────────────────────────────────────────────────────
manager.restoreIfNeeded().catch((error) => {
  console.warn('[SW] restore failed:', error.message);
});

// ─────────────────────────────────────────────────────────────────────────────
// Tab events
// ─────────────────────────────────────────────────────────────────────────────

chrome.tabs.onActivated.addListener(({ tabId, windowId }) => {
  chrome.tabs.get(tabId, (tab) => {
    if (chrome.runtime.lastError) return; // tab may already be gone
    manager.onTabActivated(tabId, windowId, tab);
  });
});

chrome.tabs.onCreated.addListener((tab) => {
  manager.onTabCreated(tab);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  manager.onTabUpdated(tabId, changeInfo, tab);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  manager.onTabRemoved(tabId);
});

// ─────────────────────────────────────────────────────────────────────────────
// Window focus
// ─────────────────────────────────────────────────────────────────────────────

chrome.windows.onFocusChanged.addListener((windowId) => {
  manager.onWindowFocusChanged(windowId);
});

// ─────────────────────────────────────────────────────────────────────────────
// Alarms (session end, idle detection, focus checkpoints, dual-task)
// ─────────────────────────────────────────────────────────────────────────────

chrome.alarms.onAlarm.addListener((alarm) => {
  manager.onAlarm(alarm);
});

// ─────────────────────────────────────────────────────────────────────────────
// Messages from content scripts, popup, and questionnaire page
// ─────────────────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id ?? null;

  switch (message.type) {

    case 'START_SESSION':
      manager.startSession(message.payload)
        .then((ok) => sendResponse({ ok }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true; // keep channel open for async response

    case 'STOP_SESSION':
      manager.endSession(true)
        .then(() => sendResponse({ ok: true }))
        .catch(() => sendResponse({ ok: false }));
      return true;

    case 'QUIT_CORE':
      manager.shutdownCore(Boolean(message.payload?.force))
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'GET_STATUS':
      manager.getStatus()
        .then((status) => sendResponse(status))
        .catch((err) => sendResponse({ state: 'inactive', error: err.message }));
      return true;

    case 'SCROLL_DATA':
      if (tabId != null) manager.onScrollData(tabId, message.payload);
      break;

    case 'ACTIVITY_PING':
      if (tabId != null) manager.onActivityPing(tabId);
      break;

    case 'VISIBILITY_CHANGE':
      if (tabId != null) manager.onVisibilityChange(tabId, message.payload.visible);
      break;

    case 'DUAL_TASK_RESPONSE':
      manager.onDualTaskResponse(message.payload)
        .then(() => sendResponse({ ok: true }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'SUBMIT_QUESTIONNAIRE':
      manager.onSubmitQuestionnaire(message.payload)
        .then(() => sendResponse({ ok: true }))
        .catch(() => sendResponse({ ok: false }));
      return true;

    default:
      console.warn('[SW] Unknown message type:', message.type);
  }
});
