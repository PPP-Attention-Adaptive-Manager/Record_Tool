export class EventBuffer {
  constructor() {
    this._events = [];
    this._scrollByTab = new Map();
  }

  appendEvent(event) {
    if (!event || !event.event_type) {
      return;
    }
    this._events.push({ ...event });
  }

  updateScroll(payload) {
    const tabId = payload.tab_id ?? "unknown";
    const key = String(tabId);
    const current = this._scrollByTab.get(key) || {
      tab_id: tabId,
      session_id: payload.session_id || null,
      full_url: payload.full_url || "",
      domain: payload.domain || "",
      path: payload.path || "",
      scroll_delta_cumulative: 0,
      scroll_depth_max: 0,
      scroll_event_count: 0,
      last_timestamp_ms: Date.now()
    };

    const delta = Number(payload.scroll_delta || 0);
    const count = Number(payload.scroll_event_count || 1);
    const depthPoint = Number(payload.scroll_depth || payload.scroll_position || 0);

    current.scroll_delta_cumulative += Number.isFinite(delta) ? delta : 0;
    current.scroll_event_count += Number.isFinite(count) ? count : 1;
    current.scroll_depth_max = Math.max(current.scroll_depth_max, Number.isFinite(depthPoint) ? depthPoint : 0);
    current.last_timestamp_ms = Number(payload.timestamp_ms || Date.now());

    if (payload.session_id) {
      current.session_id = payload.session_id;
    }
    if (payload.full_url) {
      current.full_url = payload.full_url;
    }
    if (payload.domain) {
      current.domain = payload.domain;
    }
    if (payload.path) {
      current.path = payload.path;
    }

    this._scrollByTab.set(key, current);
  }

  flushScroll(tabId, reason, extra = {}) {
    const key = String(tabId ?? "unknown");
    const current = this._scrollByTab.get(key);
    if (!current || current.scroll_event_count <= 0) {
      return;
    }

    this.appendEvent({
      event_type: "scroll",
      timestamp_ms: Date.now(),
      tab_id: current.tab_id,
      session_id: current.session_id || extra.session_id || null,
      full_url: current.full_url || "",
      domain: current.domain || "",
      path: current.path || "",
      scroll_delta_cumulative: current.scroll_delta_cumulative,
      scroll_depth_max: current.scroll_depth_max,
      scroll_event_count: current.scroll_event_count,
      flush_reason: reason,
      ...extra
    });

    this._scrollByTab.delete(key);
  }

  flushAllScroll(reason, extra = {}) {
    const keys = Array.from(this._scrollByTab.keys());
    for (const key of keys) {
      this.flushScroll(key, reason, extra);
    }
  }

  peekBatch(limit = 200) {
    if (limit <= 0) {
      return [];
    }
    return this._events.slice(0, limit).map((item) => ({ ...item }));
  }

  dropBatch(count) {
    if (count <= 0) {
      return;
    }
    this._events.splice(0, count);
  }

  size() {
    return this._events.length;
  }
}

