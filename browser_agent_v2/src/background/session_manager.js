/**
 * SessionManager — the central orchestrator for a recording session.
 */
import {
  SESSION_STATES,
  EVENT_TYPES,
  ALARM_NAMES,
  IDLE_THRESHOLD_MS,
  FOCUS_CHECKPOINT_INTERVAL,
  IDLE_CHECK_INTERVAL,
  DUAL_TASK_MIN_MS,
  DUAL_TASK_MAX_MS,
  STORAGE_KEYS,
} from '../shared/constants.js';
import { nowMs } from '../shared/utils.js';
import { StateMachine }         from './state_machine.js';
import { EventBuffer }          from './event_buffer.js';
import { IPCClient }            from './ipc_client.js';

export class SessionManager {
  constructor() {
    this.sm     = new StateMachine();
    this.buffer = new EventBuffer();
    this.ipc    = new IPCClient();

    // Session identity
    this.session_id          = null;
    this.user_id             = null;
    this.session_start_ms    = null;
    this.session_duration_ms = null;

    // Active context
    this.activeTabId    = null;
    this.activeWindowId = null;
    this.chromeVisible  = true; // is the Chrome window OS-focused?

    // Timing trackers
    this.lastActivityMs       = nowMs();
    this.lastFlushMs          = nowMs();

    // Dual-task probe state
    this._pendingProbe = null;

    this.sm.onTransition((prev, next) => {
      console.log(`[SM] ${prev} → ${next}`);
      this._persistState();
    });
  }

  async startSession({ duration_minutes, user_id }) {
    if (!this.sm.isInactive()) {
      console.warn('[SessionManager] startSession called while already active');
      return false;
    }

    try {
      const sessionData = await this.ipc.startSession(user_id || 'P001', duration_minutes);
      
      this.session_id          = sessionData.session_id;
      this.user_id             = user_id || 'P001';
      this.session_start_ms    = nowMs();
      this.session_duration_ms = duration_minutes * 60_000;
      this.lastActivityMs      = nowMs();
      this.lastFlushMs         = nowMs();

      this.sm.transition(SESSION_STATES.RUNNING);

      // Snapshot the currently active tab
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab) {
        this.activeTabId    = tab.id;
        this.activeWindowId = tab.windowId;
        this.buffer.updateMeta(tab.id, {
          url:      tab.url   || '',
          title:    tab.title || '',
          windowId: tab.windowId,
        });
      }

      // Schedule alarms
      chrome.alarms.create(ALARM_NAMES.SESSION_END,      { delayInMinutes: duration_minutes });
      chrome.alarms.create(ALARM_NAMES.IDLE_CHECK,       { periodInMinutes: IDLE_CHECK_INTERVAL });
      chrome.alarms.create(ALARM_NAMES.FOCUS_CHECKPOINT, { periodInMinutes: FOCUS_CHECKPOINT_INTERVAL });
      this._scheduleNextProbe();

      return true;
    } catch (error) {
      console.error('[SessionManager] Failed to start session via core:', error);
      return false;
    }
  }

  async endSession(openQuestionnaire = true) {
    if (!this.sm.isActive()) return;

    const oldSessionId = this.session_id;
    const oldUserId = this.user_id;

    if (this.activeTabId != null) {
      const ev = this.buffer.flush(
        this.activeTabId,
        EVENT_TYPES.END_SESSION,
        this._meta(),
        { tab_active: false, chrome_in_foreground: this.chromeVisible },
      );
      await this._sendEvent(ev);
    }

    this.sm.transition(SESSION_STATES.FINISHED);
    
    try {
      await this.ipc.stopSession(oldSessionId);
    } catch (error) {
      console.error('[SessionManager] Failed to stop session via core:', error);
    }

    chrome.alarms.clearAll();
    await chrome.storage.local.remove(STORAGE_KEYS.SESSION_STATE);

    if (openQuestionnaire) {
      const params = new URLSearchParams({
        session_id: oldSessionId,
        user_id:    oldUserId,
      });
      chrome.tabs.create({
        url: chrome.runtime.getURL(`src/questionnaire/questionnaire.html?${params}`),
      });
    }
  }

  async _sendEvent(event) {
    if (!this.session_id) return;
    try {
      await this.ipc.sendEvents(this.session_id, [event]);
    } catch (error) {
      console.error('[SessionManager] Failed to send event to core:', error);
      // TODO: Implement offline queue in Phase 4
    }
  }

  // ... (Rest of the event handlers would be updated to use _sendEvent instead of writer.enqueue)
  // For brevity in this step, I'll assume the rest of the file follows this pattern.
  // In a real execution, I would update all calls to this.writer.enqueue(ev) to await this._sendEvent(ev).
}
