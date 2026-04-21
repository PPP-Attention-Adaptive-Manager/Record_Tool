/**
 * Per-tab behavioral state accumulator.
 *
 * Design rationale
 * ────────────────
 * Raw scroll events fire at 60 fps — writing one InfluxDB point per event
 * would fragment the signal and overwhelm storage.  Instead, this buffer
 * accumulates scroll + activity data for each tab.  A single structured event
 * is emitted only when a flush condition is met (tab switch, URL change,
 * idle, focus checkpoint, end_session, etc.).
 *
 * Scroll accumulators are RESET after each flush so the next event captures
 * only the delta since the previous write.  depth_max tracks the waterline
 * across the entire tab lifetime (reset on flush too — per-interval max).
 */
import { nowMs } from '../shared/utils.js';
import { createBehaviorEvent } from '../shared/schemas.js';
import { parseURL } from './url_parser.js';
import { classifySiteType, classifyTaskHint } from './semantic_rules.js';

export class EventBuffer {
  constructor() {
    /** @type {Map<number, TabState>} */
    this._tabs = new Map();
  }

  // ─── Public API ────────────────────────────────────────────────────────────

  /**
   * Return the state object for tabId, creating a fresh one if absent.
   * @param {number} tabId
   * @returns {TabState}
   */
  getOrCreate(tabId) {
    if (!this._tabs.has(tabId)) {
      this._tabs.set(tabId, this._freshState(tabId));
    }
    return this._tabs.get(tabId);
  }

  /**
   * Merge incoming scroll batch (sent by content script every 2 s).
   * Accumulates; does NOT flush.
   *
   * @param {number} tabId
   * @param {{ delta: number, depth_last: number, depth_max: number, event_count: number }} data
   */
  accumulateScroll(tabId, data) {
    const s = this.getOrCreate(tabId);
    s.scroll_delta_cumulative += data.delta         ?? 0;
    s.scroll_depth_last        = data.depth_last    ?? s.scroll_depth_last;
    s.scroll_depth_max         = Math.max(s.scroll_depth_max, data.depth_max ?? 0);
    s.scroll_event_count      += data.event_count   ?? 0;
  }

  /**
   * Update tab metadata without flushing.
   * Called when a tab is navigated or activated.
   *
   * @param {number} tabId
   * @param {{ url?: string, title?: string, windowId?: number }} meta
   */
  updateMeta(tabId, meta) {
    const s = this.getOrCreate(tabId);
    if (meta.url      !== undefined) s.url      = meta.url;
    if (meta.title    !== undefined) s.title    = meta.title;
    if (meta.windowId !== undefined) s.windowId = meta.windowId;
  }

  /**
   * Build a complete behavioral event for tabId, then RESET scroll accumulators.
   *
   * This is the ONLY place a behavior event object is created for a tab.
   * Call this exactly once per flush condition.
   *
   * @param {number}  tabId
   * @param {string}  eventType   one of EVENT_TYPES
   * @param {{ session_id: string, user_id: string }} sessionMeta
   * @param {object}  extraFields  overrides (e.g. tab_active, visibility_state)
   * @returns {object}  complete event conforming to createBehaviorEvent schema
   */
  flush(tabId, eventType, sessionMeta, extraFields = {}) {
    const s   = this.getOrCreate(tabId);
    const now = nowMs();
    const url = parseURL(s.url);

    const event = createBehaviorEvent({
      // Metadata
      session_id:                sessionMeta.session_id,
      user_id:                   sessionMeta.user_id,
      event_type:                eventType,
      timestamp:                 now,
      duration_since_last_event: (now - s.last_event_time) / 1000,
      source:                    'browser',

      // Tab
      tab_id:       tabId,
      window_id:    s.windowId,
      full_url:     url.full_url,
      domain:       url.domain,
      path:         url.path,
      query_string: url.query_string,
      title:        s.title,

      // Behavioral aggregates
      scroll_delta_cumulative: s.scroll_delta_cumulative,
      scroll_depth_last:       s.scroll_depth_last,
      scroll_depth_max:        s.scroll_depth_max,
      scroll_event_count:      s.scroll_event_count,

      // Semantic helpers
      site_type: classifySiteType(url.domain),
      task_hint: classifyTaskHint(url.domain, url.path),

      ...extraFields,
    });

    // Reset accumulators — next flush captures only the new interval
    s.scroll_delta_cumulative = 0;
    s.scroll_depth_last       = 0;
    s.scroll_depth_max        = 0;
    s.scroll_event_count      = 0;
    s.last_event_time         = now;

    return event;
  }

  /** Remove a tab from the buffer (e.g. when it is closed). */
  remove(tabId) {
    this._tabs.delete(tabId);
  }

  has(tabId) {
    return this._tabs.has(tabId);
  }

  // ─── Private ───────────────────────────────────────────────────────────────

  _freshState(tabId) {
    const now = nowMs();
    return {
      tabId,
      windowId:    null,
      url:         '',
      title:       '',

      // Scroll accumulators (reset on every flush)
      scroll_delta_cumulative: 0,
      scroll_depth_last:       0,
      scroll_depth_max:        0,
      scroll_event_count:      0,

      last_event_time: now,
      active_since:    now,
    };
  }
}
