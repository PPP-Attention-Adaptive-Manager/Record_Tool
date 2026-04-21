import {
  SESSION_STATES,
  EVENT_TYPES,
  ALARM_NAMES,
  IDLE_THRESHOLD_MS,
  FOCUS_CHECKPOINT_INTERVAL,
  IDLE_CHECK_INTERVAL,
  DUAL_TASK_MIN_MS,
  DUAL_TASK_MAX_MS,
  DUAL_TASK_RESPONSE_WINDOW,
  STORAGE_KEYS,
} from '../shared/constants.js';
import { generateUUID, nowMs } from '../shared/utils.js';
import {
  createBehaviorEvent,
  createDualTaskEvent,
} from '../shared/schemas.js';
import { StateMachine } from './state_machine.js';
import { EventBuffer } from './event_buffer.js';
import { CoreIpcClient } from './ipc_client.js';

export class SessionManager {
  constructor() {
    this.sm = new StateMachine();
    this.buffer = new EventBuffer();
    this.transport = new CoreIpcClient();

    this.session_id = null;
    this.user_id = null;
    this.session_start_ms = null;
    this.session_duration_ms = null;

    this.activeTabId = null;
    this.activeWindowId = null;
    this.chromeVisible = true;
    this.lastActivityMs = nowMs();
    this.lastFlushMs = nowMs();
    this.enableInflux = false;

    this._pendingProbe = null;

    this.sm.onTransition(() => {
      this._persistState().catch((error) => {
        console.warn('[SessionManager] persist failed:', error.message);
      });
    });
  }

  async startSession({ duration_minutes, user_id, enable_influx = false }) {
    if (!this.sm.isInactive()) {
      return false;
    }

    const startResponse = await this.transport.startSession({
      user_id,
      duration_minutes,
      enable_influx,
    });

    this.session_id = startResponse.session_id;
    this.user_id = user_id;
    this.enableInflux = !!enable_influx;
    this.session_start_ms = Date.parse(startResponse.started_at);
    this.session_duration_ms = duration_minutes * 60_000;
    this.lastActivityMs = nowMs();
    this.lastFlushMs = nowMs();

    this.sm.transition(SESSION_STATES.RUNNING);

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      this.activeTabId = tab.id;
      this.activeWindowId = tab.windowId;
      this.buffer.updateMeta(tab.id, {
        url: tab.url || '',
        title: tab.title || '',
        windowId: tab.windowId,
      });
    }

    chrome.alarms.create(ALARM_NAMES.SESSION_END, { delayInMinutes: duration_minutes });
    chrome.alarms.create(ALARM_NAMES.IDLE_CHECK, { periodInMinutes: IDLE_CHECK_INTERVAL });
    chrome.alarms.create(ALARM_NAMES.FOCUS_CHECKPOINT, { periodInMinutes: FOCUS_CHECKPOINT_INTERVAL });
    this._scheduleNextProbe();

    return true;
  }

  async endSession(openQuestionnaire = true) {
    if (!this.sm.isActive()) {
      return;
    }

    if (this.activeTabId != null) {
      const finalEvent = this.buffer.flush(
        this.activeTabId,
        EVENT_TYPES.FOCUS,
        this._meta(),
        {
          tab_active: false,
          chrome_in_foreground: this.chromeVisible,
        },
      );
      await this.transport.enqueue(finalEvent);
    }

    await this.transport.flushAll();
    await this.transport.stopSession(this.session_id);

    this.sm.transition(SESSION_STATES.FINISHED);
    chrome.alarms.clearAll();
    await chrome.storage.local.remove(STORAGE_KEYS.SESSION_STATE);

    const finishedSessionId = this.session_id;
    const finishedUserId = this.user_id;
    this._clearVolatileState();

    if (openQuestionnaire) {
      const params = new URLSearchParams({
        session_id: finishedSessionId,
        user_id: finishedUserId,
      });
      chrome.tabs.create({
        url: chrome.runtime.getURL(`src/questionnaire/questionnaire.html?${params}`),
      });
    }
  }

  async onTabActivated(tabId, windowId, tabInfo) {
    if (!this.sm.isActive()) {
      return;
    }

    const prevTabId = this.activeTabId;
    if (prevTabId != null && prevTabId !== tabId) {
      const event = this.buffer.flush(prevTabId, EVENT_TYPES.SWITCH, this._meta(), {
        tab_active: false,
        chrome_in_foreground: this.chromeVisible,
      });
      await this.transport.enqueue(event);
    }

    this.activeTabId = tabId;
    this.activeWindowId = windowId;
    this.lastActivityMs = nowMs();

    if (tabInfo) {
      this.buffer.updateMeta(tabId, {
        url: tabInfo.url || '',
        title: tabInfo.title || '',
        windowId: tabInfo.windowId ?? windowId,
      });
    }

    if (this.sm.is(SESSION_STATES.IDLE) || this.sm.is(SESSION_STATES.HIDDEN)) {
      this.sm.transition(SESSION_STATES.RUNNING);
    }
  }

  async onTabCreated(tab) {
    if (!this.sm.isActive()) {
      return;
    }
    this.buffer.updateMeta(tab.id, {
      url: tab.url || '',
      title: tab.title || '',
      windowId: tab.windowId,
    });
  }

  async onTabUpdated(tabId, changeInfo, tab) {
    if (!this.sm.isActive()) {
      return;
    }

    const state = this.buffer.getOrCreate(tabId);
    const prevUrl = state.url;

    this.buffer.updateMeta(tabId, {
      url: changeInfo.url ?? tab.url ?? prevUrl,
      title: changeInfo.title ?? tab.title ?? state.title,
    });

    if (changeInfo.url && tabId === this.activeTabId && changeInfo.url !== prevUrl) {
      const event = this.buffer.flush(tabId, EVENT_TYPES.SWITCH, this._meta(), {
        tab_active: true,
        chrome_in_foreground: this.chromeVisible,
      });
      await this.transport.enqueue(event);
    }
  }

  onTabRemoved(tabId) {
    if (tabId === this.activeTabId) {
      this.activeTabId = null;
    }
    this.buffer.remove(tabId);
  }

  async onWindowFocusChanged(windowId) {
    if (!this.sm.isActive()) {
      return;
    }

    const noFocus = windowId === chrome.windows.WINDOW_ID_NONE;

    if (noFocus && this.chromeVisible) {
      this.chromeVisible = false;
      this.sm.transition(SESSION_STATES.BACKGROUND);

      if (this.activeTabId != null) {
        const event = this.buffer.flush(this.activeTabId, EVENT_TYPES.BACKGROUND, this._meta(), {
          tab_active: true,
          visibility_state: 'hidden',
          chrome_in_foreground: false,
        });
        await this.transport.enqueue(event);
      }
    } else if (!noFocus && !this.chromeVisible) {
      this.chromeVisible = true;
      this.lastActivityMs = nowMs();
      if (this.sm.is(SESSION_STATES.BACKGROUND)) {
        this.sm.transition(SESSION_STATES.RUNNING);
      }
    }
  }

  onScrollData(tabId, data) {
    if (!this.sm.isActive()) {
      return;
    }
    this.buffer.accumulateScroll(tabId, data);
    this.lastActivityMs = nowMs();
  }

  onActivityPing() {
    if (!this.sm.isActive()) {
      return;
    }
    this.lastActivityMs = nowMs();
    if (this.sm.is(SESSION_STATES.IDLE)) {
      this.sm.transition(SESSION_STATES.RUNNING);
    }
  }

  async onVisibilityChange(tabId, isVisible) {
    if (!this.sm.isActive() || tabId !== this.activeTabId) {
      return;
    }

    if (!isVisible) {
      if (this.sm.is(SESSION_STATES.RUNNING)) {
        this.sm.transition(SESSION_STATES.HIDDEN);
      }
      const event = this.buffer.flush(tabId, EVENT_TYPES.TAB_HIDDEN, this._meta(), {
        tab_active: false,
        visibility_state: 'hidden',
        chrome_in_foreground: this.chromeVisible,
      });
      await this.transport.enqueue(event);
    } else if (this.sm.is(SESSION_STATES.HIDDEN)) {
      this.sm.transition(SESSION_STATES.RUNNING);
      this.lastActivityMs = nowMs();
    }
  }

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
      default:
        break;
    }
  }

  async onDualTaskResponse(payload) {
    if (!this._pendingProbe || this._pendingProbe.probeId !== payload.probeId) {
      return;
    }

    const event = createDualTaskEvent({
      session_id: this.session_id,
      user_id: this.user_id,
      reaction_time: payload.reaction_time ?? -1,
      dual_task_success: payload.success ? 1 : 0,
      dual_task_error: payload.error ? 1 : 0,
      missed_response: 0,
    });
    await this.transport.enqueue(event);
    this._pendingProbe = null;
  }

  async onSubmitQuestionnaire(payload) {
    await this.transport.enqueue(payload);
    await this.transport.flushAll();
  }

  async shutdownCore(force = false) {
    if (this.sm.isActive() && !force) {
      throw new Error('Stop the active session before quitting the core.');
    }

    if (this.sm.isActive() && force) {
      await this.endSession(false);
    }

    return this.transport.shutdownCore(force);
  }

  async getStatus() {
    const transport = await this.transport.getStatus();
    return {
      state: this.sm.state,
      session_id: this.session_id,
      session_start_ms: this.session_start_ms,
      session_duration_ms: this.session_duration_ms,
      active_tab_id: this.activeTabId,
      enable_influx: this.enableInflux,
      transport,
    };
  }

  async restoreIfNeeded() {
    const saved = await chrome.storage.local.get(STORAGE_KEYS.SESSION_STATE);
    const state = saved[STORAGE_KEYS.SESSION_STATE];
    if (!state) {
      return;
    }

    const activeStates = [
      SESSION_STATES.RUNNING,
      SESSION_STATES.HIDDEN,
      SESSION_STATES.BACKGROUND,
      SESSION_STATES.IDLE,
    ];
    if (!activeStates.includes(state.state)) {
      return;
    }

    this.session_id = state.session_id;
    this.user_id = state.user_id;
    this.session_start_ms = state.session_start_ms;
    this.session_duration_ms = state.session_duration_ms;
    this.enableInflux = !!state.enable_influx;
    this.sm.state = SESSION_STATES.RUNNING;

    const elapsed = nowMs() - this.session_start_ms;
    if (elapsed >= this.session_duration_ms) {
      await this.endSession(true);
      return;
    }

    const remaining = (this.session_duration_ms - elapsed) / 60_000;
    chrome.alarms.create(ALARM_NAMES.SESSION_END, { delayInMinutes: remaining });
    chrome.alarms.create(ALARM_NAMES.IDLE_CHECK, { periodInMinutes: IDLE_CHECK_INTERVAL });
    chrome.alarms.create(ALARM_NAMES.FOCUS_CHECKPOINT, { periodInMinutes: FOCUS_CHECKPOINT_INTERVAL });
    this._scheduleNextProbe();
    this.transport.flushAll().catch((error) => {
      console.warn('[SessionManager] restore flush failed:', error.message);
    });
  }

  _meta() {
    return { session_id: this.session_id, user_id: this.user_id };
  }

  async _handleIdleCheck() {
    if (!this.sm.is(SESSION_STATES.RUNNING)) {
      return;
    }

    const idleFor = nowMs() - this.lastActivityMs;
    if (idleFor < IDLE_THRESHOLD_MS || this.activeTabId == null) {
      return;
    }

    this.sm.transition(SESSION_STATES.IDLE);
    const event = this.buffer.flush(this.activeTabId, EVENT_TYPES.IDLE, this._meta(), {
      tab_active: true,
      chrome_in_foreground: this.chromeVisible,
    });
    await this.transport.enqueue(event);
  }

  async _handleFocusCheckpoint() {
    if (!this.sm.is(SESSION_STATES.RUNNING) || this.activeTabId == null) {
      return;
    }
    const event = this.buffer.flush(this.activeTabId, EVENT_TYPES.FOCUS, this._meta(), {
      tab_active: true,
      chrome_in_foreground: this.chromeVisible,
    });
    await this.transport.enqueue(event);
  }

  _scheduleNextProbe() {
    if (!this.sm.isActive()) {
      return;
    }
    const delayMs = DUAL_TASK_MIN_MS + Math.random() * (DUAL_TASK_MAX_MS - DUAL_TASK_MIN_MS);
    chrome.alarms.create(ALARM_NAMES.DUAL_TASK, { delayInMinutes: delayMs / 60_000 });
  }

  async _triggerProbe() {
    if (!this.sm.is(SESSION_STATES.RUNNING) || this.activeTabId == null) {
      this._scheduleNextProbe();
      return;
    }

    const probeId = generateUUID();
    this._pendingProbe = {
      probeId,
      triggerMs: nowMs(),
      tabId: this.activeTabId,
    };

    try {
      await chrome.tabs.sendMessage(this.activeTabId, {
        type: 'DUAL_TASK_SHOW',
        probeId,
      });
    } catch {
      await this._recordMissed(probeId);
    }

    setTimeout(() => {
      this._recordMissed(probeId).catch((error) => {
        console.warn('[SessionManager] missed probe write failed:', error.message);
      });
    }, DUAL_TASK_RESPONSE_WINDOW + 500);

    this._scheduleNextProbe();
  }

  async _recordMissed(probeId) {
    if (!this._pendingProbe || this._pendingProbe.probeId !== probeId) {
      return;
    }

    const event = createDualTaskEvent({
      session_id: this.session_id,
      user_id: this.user_id,
      reaction_time: -1,
      dual_task_success: 0,
      dual_task_error: 0,
      missed_response: 1,
    });
    await this.transport.enqueue(event);
    this._pendingProbe = null;
  }

  async _persistState() {
    if (!this.sm.isActive()) {
      return;
    }
    await chrome.storage.local.set({
      [STORAGE_KEYS.SESSION_STATE]: {
        state: this.sm.state,
        session_id: this.session_id,
        user_id: this.user_id,
        session_start_ms: this.session_start_ms,
        session_duration_ms: this.session_duration_ms,
        enable_influx: this.enableInflux,
      },
    });
  }

  _clearVolatileState() {
    this.session_id = null;
    this.user_id = null;
    this.session_start_ms = null;
    this.session_duration_ms = null;
    this.activeTabId = null;
    this.activeWindowId = null;
    this.chromeVisible = true;
    this.lastActivityMs = nowMs();
    this.lastFlushMs = nowMs();
    this.enableInflux = false;
    this._pendingProbe = null;
  }
}
