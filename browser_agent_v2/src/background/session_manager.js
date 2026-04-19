/**
 * SessionManager — the central orchestrator for a recording session.
 *
 * Responsibilities:
 *  1. Owns the state machine (inactive → running → … → finished)
 *  2. Listens to tab/window events forwarded from service_worker.js
 *  3. Decides WHEN to flush the EventBuffer → emit a structured event
 *  4. Drives the dual-task probe schedule
 *  5. Persists minimal session state in chrome.storage.local so the
 *     session survives an MV3 service worker restart
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
import { generateUUID, nowMs } from '../shared/utils.js';
import { createDualTaskEvent }  from '../shared/schemas.js';
import { StateMachine }         from './state_machine.js';
import { EventBuffer }          from './event_buffer.js';
import { InfluxWriter }         from './influx_writer.js';

export class SessionManager {
  constructor() {
    this.sm     = new StateMachine();
    this.buffer = new EventBuffer();
    this.writer = new InfluxWriter();

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

    // Dual-task probe state (at most one probe in-flight)
    this._pendingProbe = null; // { probeId, triggerMs, tabId }

    this.sm.onTransition((prev, next) => {
      console.log(`[SM] ${prev} → ${next}`);
      this._persistState();
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Session lifecycle
  // ─────────────────────────────────────────────────────────────────────────

  async startSession({ duration_minutes, user_id }) {
    if (!this.sm.isInactive()) {
      console.warn('[SessionManager] startSession called while already active');
      return false;
    }

    this.session_id          = generateUUID();
    this.user_id             = user_id || 'anonymous';
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

    // Emit start_session
    const ev = this.buffer.flush(
      this.activeTabId,
      EVENT_TYPES.START_SESSION,
      this._meta(),
      { tab_active: true, visibility_state: 'visible', chrome_in_foreground: true },
    );
    this.writer.enqueue(ev);

    // Schedule alarms
    chrome.alarms.create(ALARM_NAMES.SESSION_END,      { delayInMinutes: duration_minutes });
    chrome.alarms.create(ALARM_NAMES.IDLE_CHECK,       { periodInMinutes: IDLE_CHECK_INTERVAL });
    chrome.alarms.create(ALARM_NAMES.FOCUS_CHECKPOINT, { periodInMinutes: FOCUS_CHECKPOINT_INTERVAL });
    this._scheduleNextProbe();

    return true;
  }

  async endSession(openQuestionnaire = true) {
    if (!this.sm.isActive()) return;

    if (this.activeTabId != null) {
      const ev = this.buffer.flush(
        this.activeTabId,
        EVENT_TYPES.END_SESSION,
        this._meta(),
        { tab_active: false, chrome_in_foreground: this.chromeVisible },
      );
      this.writer.enqueue(ev);
    }

    this.sm.transition(SESSION_STATES.FINISHED);
    await this.writer.flushAll();

    chrome.alarms.clearAll();
    await chrome.storage.local.remove(STORAGE_KEYS.SESSION_STATE);

    if (openQuestionnaire) {
      const params = new URLSearchParams({
        session_id: this.session_id,
        user_id:    this.user_id,
      });
      chrome.tabs.create({
        url: chrome.runtime.getURL(`src/questionnaire/questionnaire.html?${params}`),
      });
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Tab event handlers (called from service_worker.js)
  // ─────────────────────────────────────────────────────────────────────────

  async onTabActivated(tabId, windowId, tabInfo) {
    if (!this.sm.isActive()) return;

    const prevTabId = this.activeTabId;

    // Flush the departing tab as a SWITCH event
    if (prevTabId != null && prevTabId !== tabId) {
      const ev = this.buffer.flush(prevTabId, EVENT_TYPES.SWITCH, this._meta(), {
        tab_active:           false,
        chrome_in_foreground: this.chromeVisible,
      });
      this.writer.enqueue(ev);
    }

    this.activeTabId    = tabId;
    this.activeWindowId = windowId;
    this.lastActivityMs = nowMs();

    if (tabInfo) {
      this.buffer.updateMeta(tabId, {
        url:      tabInfo.url   || '',
        title:    tabInfo.title || '',
        windowId: tabInfo.windowId ?? windowId,
      });
    }

    // Recover from idle/hidden back to running
    if (this.sm.is(SESSION_STATES.IDLE) || this.sm.is(SESSION_STATES.HIDDEN)) {
      this.sm.transition(SESSION_STATES.RUNNING);
    }
  }

  async onTabCreated(tab) {
    if (!this.sm.isActive()) return;

    this.buffer.updateMeta(tab.id, {
      url:      tab.url      || '',
      title:    tab.title    || '',
      windowId: tab.windowId,
    });

    const ev = this.buffer.flush(tab.id, EVENT_TYPES.NEW_TAB, this._meta(), {
      tab_active:           tab.active,
      chrome_in_foreground: this.chromeVisible,
    });
    this.writer.enqueue(ev);
  }

  async onTabUpdated(tabId, changeInfo, tab) {
    if (!this.sm.isActive()) return;

    const state   = this.buffer.getOrCreate(tabId);
    const prevUrl = state.url;

    // Update metadata first
    this.buffer.updateMeta(tabId, {
      url:   changeInfo.url   ?? tab.url   ?? prevUrl,
      title: changeInfo.title ?? tab.title ?? state.title,
    });

    // URL change on the active tab = context switch → flush as SWITCH
    if (
      changeInfo.url             &&
      tabId === this.activeTabId &&
      changeInfo.url !== prevUrl
    ) {
      const ev = this.buffer.flush(tabId, EVENT_TYPES.SWITCH, this._meta(), {
        tab_active:           true,
        chrome_in_foreground: this.chromeVisible,
      });
      this.writer.enqueue(ev);
    }
  }

  onTabRemoved(tabId) {
    if (tabId === this.activeTabId) this.activeTabId = null;
    this.buffer.remove(tabId);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Window focus handler
  // ─────────────────────────────────────────────────────────────────────────

  async onWindowFocusChanged(windowId) {
    if (!this.sm.isActive()) return;

    const noFocus = windowId === chrome.windows.WINDOW_ID_NONE;

    if (noFocus && this.chromeVisible) {
      this.chromeVisible = false;
      this.sm.transition(SESSION_STATES.BACKGROUND);

      if (this.activeTabId != null) {
        const ev = this.buffer.flush(this.activeTabId, EVENT_TYPES.BACKGROUND, this._meta(), {
          tab_active:           true,
          visibility_state:     'hidden',
          chrome_in_foreground: false,
        });
        this.writer.enqueue(ev);
      }
    } else if (!noFocus && !this.chromeVisible) {
      this.chromeVisible  = true;
      this.lastActivityMs = nowMs();

      if (this.sm.is(SESSION_STATES.BACKGROUND)) {
        this.sm.transition(SESSION_STATES.RUNNING);
      }
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Content script messages
  // ─────────────────────────────────────────────────────────────────────────

  onScrollData(tabId, data) {
    if (!this.sm.isActive()) return;
    this.buffer.accumulateScroll(tabId, data);
    this.lastActivityMs = nowMs();
  }

  onActivityPing(tabId) {
    if (!this.sm.isActive()) return;
    this.lastActivityMs = nowMs();

    // Break out of idle when user becomes active again
    if (this.sm.is(SESSION_STATES.IDLE)) {
      this.sm.transition(SESSION_STATES.RUNNING);
    }
  }

  onVisibilityChange(tabId, isVisible) {
    if (!this.sm.isActive()) return;
    if (tabId !== this.activeTabId) return;

    if (!isVisible) {
      // Only transition if we are currently running (not already in background/idle)
      if (this.sm.is(SESSION_STATES.RUNNING)) {
        this.sm.transition(SESSION_STATES.HIDDEN);
      }
      const ev = this.buffer.flush(tabId, EVENT_TYPES.TAB_HIDDEN, this._meta(), {
        tab_active:           false,
        visibility_state:     'hidden',
        chrome_in_foreground: this.chromeVisible,
      });
      this.writer.enqueue(ev);
    } else {
      if (this.sm.is(SESSION_STATES.HIDDEN)) {
        this.sm.transition(SESSION_STATES.RUNNING);
        this.lastActivityMs = nowMs();
      }
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Alarm handlers
  // ─────────────────────────────────────────────────────────────────────────

  async onAlarm(alarm) {
    switch (alarm.name) {
      case ALARM_NAMES.SESSION_END:
        await this.endSession(true);
        break;

      case ALARM_NAMES.IDLE_CHECK:
        await this._handleIdleCheck();
        break;

      case ALARM_NAMES.FOCUS_CHECKPOINT:
        await this._handleFocusCheckpoint();
        break;

      case ALARM_NAMES.DUAL_TASK:
        await this._triggerProbe();
        break;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Dual-task probe
  // ─────────────────────────────────────────────────────────────────────────

  onDualTaskResponse(payload) {
    if (!this._pendingProbe) return;
    if (this._pendingProbe.probeId !== payload.probeId) return;

    const ev = createDualTaskEvent({
      session_id:    this.session_id,
      user_id:       this.user_id,
      reaction_time: payload.reaction_time ?? -1,
      success:       payload.success ? 1 : 0,
      error:         payload.error   ? 1 : 0,
      missed_response: 0,
    });
    this.writer.enqueue(ev);
    this._pendingProbe = null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Questionnaire
  // ─────────────────────────────────────────────────────────────────────────

  async onSubmitQuestionnaire(payload) {
    this.writer.enqueue(payload);
    await this.writer.flushAll();
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Status (for popup)
  // ─────────────────────────────────────────────────────────────────────────

  getStatus() {
    return {
      state:               this.sm.state,
      session_id:          this.session_id,
      session_start_ms:    this.session_start_ms,
      session_duration_ms: this.session_duration_ms,
      active_tab_id:       this.activeTabId,
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // State restoration (service worker restart)
  // ─────────────────────────────────────────────────────────────────────────

  async restoreIfNeeded() {
    const { [STORAGE_KEYS.SESSION_STATE]: saved } =
      await chrome.storage.local.get(STORAGE_KEYS.SESSION_STATE);

    if (!saved) return;

    const { state, session_id, user_id, session_start_ms, session_duration_ms } = saved;

    // Only restore active (non-terminal) sessions
    const active = [
      SESSION_STATES.RUNNING,
      SESSION_STATES.HIDDEN,
      SESSION_STATES.BACKGROUND,
      SESSION_STATES.IDLE,
    ];
    if (!active.includes(state)) return;

    const elapsed = nowMs() - session_start_ms;
    if (elapsed >= session_duration_ms) {
      // Session already expired while SW was down
      await this.endSession(true);
      return;
    }

    this.session_id          = session_id;
    this.user_id             = user_id;
    this.session_start_ms    = session_start_ms;
    this.session_duration_ms = session_duration_ms;
    this.sm.state            = SESSION_STATES.RUNNING; // conservative restore

    // Re-arm alarms for the remaining duration
    const remaining = (session_duration_ms - elapsed) / 60_000;
    chrome.alarms.create(ALARM_NAMES.SESSION_END,      { delayInMinutes: remaining });
    chrome.alarms.create(ALARM_NAMES.IDLE_CHECK,       { periodInMinutes: IDLE_CHECK_INTERVAL });
    chrome.alarms.create(ALARM_NAMES.FOCUS_CHECKPOINT, { periodInMinutes: FOCUS_CHECKPOINT_INTERVAL });
    this._scheduleNextProbe();

    console.log('[SessionManager] Restored session', session_id);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Private helpers
  // ─────────────────────────────────────────────────────────────────────────

  _meta() {
    return { session_id: this.session_id, user_id: this.user_id };
  }

  async _handleIdleCheck() {
    if (!this.sm.is(SESSION_STATES.RUNNING)) return;

    const idleFor = nowMs() - this.lastActivityMs;
    if (idleFor < IDLE_THRESHOLD_MS) return;

    this.sm.transition(SESSION_STATES.IDLE);

    if (this.activeTabId != null) {
      const ev = this.buffer.flush(this.activeTabId, EVENT_TYPES.IDLE, this._meta(), {
        tab_active:           true,
        chrome_in_foreground: this.chromeVisible,
      });
      this.writer.enqueue(ev);
    }
  }

  async _handleFocusCheckpoint() {
    // Only emit a focus checkpoint when the user is actively using the session
    if (!this.sm.is(SESSION_STATES.RUNNING)) return;
    if (this.activeTabId == null) return;

    const ev = this.buffer.flush(this.activeTabId, EVENT_TYPES.FOCUS, this._meta(), {
      tab_active:           true,
      chrome_in_foreground: this.chromeVisible,
    });
    this.writer.enqueue(ev);
  }

  _scheduleNextProbe() {
    if (!this.sm.isActive()) return;
    const delayMs      = DUAL_TASK_MIN_MS + Math.random() * (DUAL_TASK_MAX_MS - DUAL_TASK_MIN_MS);
    const delayMinutes = delayMs / 60_000;
    chrome.alarms.create(ALARM_NAMES.DUAL_TASK, { delayInMinutes: delayMinutes });
  }

  async _triggerProbe() {
    if (!this.sm.is(SESSION_STATES.RUNNING) || this.activeTabId == null) {
      this._scheduleNextProbe();
      return;
    }

    const probeId    = generateUUID();
    const triggerMs  = nowMs();
    this._pendingProbe = { probeId, triggerMs, tabId: this.activeTabId };

    try {
      await chrome.tabs.sendMessage(this.activeTabId, {
        type: 'DUAL_TASK_SHOW',
        probeId,
      });
    } catch {
      // Tab has no content script (chrome://, PDF, etc.) — record as missed
      this._recordMissed(probeId);
    }

    // Auto-expire probe after response window
    setTimeout(() => this._recordMissed(probeId), 3_500);

    this._scheduleNextProbe();
  }

  _recordMissed(probeId) {
    if (!this._pendingProbe || this._pendingProbe.probeId !== probeId) return;

    const ev = createDualTaskEvent({
      session_id:      this.session_id,
      user_id:         this.user_id,
      reaction_time:   -1,
      success:         0,
      error:           0,
      missed_response: 1,
    });
    this.writer.enqueue(ev);
    this._pendingProbe = null;
  }

  async _persistState() {
    if (!this.sm.isActive()) return;
    await chrome.storage.local.set({
      [STORAGE_KEYS.SESSION_STATE]: {
        state:               this.sm.state,
        session_id:          this.session_id,
        user_id:             this.user_id,
        session_start_ms:    this.session_start_ms,
        session_duration_ms: this.session_duration_ms,
      },
    });
  }
}
