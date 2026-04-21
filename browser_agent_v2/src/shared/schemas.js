import { generateEventId, nowMs } from './utils.js';

const EVENT_TYPES = new Set([
  'start_session',
  'end_session',
  'switch',
  'scroll',
  'tab_hidden',
  'background',
  'idle',
  'focus',
  'app_focus',
  'dual_task',
  'questionnaire',
]);

const SITE_TYPES = new Set([
  'development',
  'search',
  'communication',
  'social',
  'entertainment',
  'productivity',
  'shopping',
  'education',
  'news',
  'unknown',
]);

const TASK_HINTS = new Set([
  'coding',
  'searching',
  'watching',
  'writing',
  'communicating',
  'reading',
  'browsing',
  'shopping',
  'unknown',
]);

export function createBehaviorEvent(overrides = {}) {
  return {
    session_id: '',
    user_id: 'P000',
    event_id: generateEventId(),
    event_type: '',
    timestamp: nowMs(),
    duration_since_last_event: 0,
    source: 'browser',
    tab_id: null,
    window_id: null,
    full_url: '',
    domain: '',
    path: '',
    query_string: '',
    title: '',
    scroll_delta_cumulative: 0,
    scroll_depth_last: 0,
    scroll_depth_max: 0,
    scroll_event_count: 0,
    tab_active: true,
    visibility_state: 'visible',
    chrome_in_foreground: true,
    site_type: 'unknown',
    task_hint: 'unknown',
    ...overrides,
  };
}

export function createDualTaskEvent(overrides = {}) {
  return {
    session_id: '',
    user_id: 'P000',
    event_id: generateEventId(),
    event_type: 'dual_task',
    timestamp: nowMs(),
    source: 'browser',
    reaction_time: -1,
    dual_task_success: 0,
    dual_task_error: 0,
    missed_response: 0,
    ...overrides,
  };
}

export function createQuestionnaireEvent(overrides = {}) {
  return {
    session_id: '',
    user_id: 'P000',
    event_id: generateEventId(),
    event_type: 'questionnaire',
    timestamp: nowMs(),
    source: 'browser',
    mental_demand: 0,
    physical_demand: 0,
    temporal_demand: 0,
    performance: 0,
    effort: 0,
    frustration: 0,
    stress_self_report: 0,
    valence: 0,
    arousal: 0,
    ...overrides,
  };
}

export function validateEvent(event) {
  if (!event || typeof event !== 'object') {
    return { ok: false, error: 'Event must be an object' };
  }
  if (!/^sess_[0-9]{8}_[0-9]{6}_[a-f0-9]{6}$/.test(event.session_id || '')) {
    return { ok: false, error: 'session_id is missing or invalid' };
  }
  if (!/^P[0-9]{3}$/.test(event.user_id || '')) {
    return { ok: false, error: 'user_id must match P001-P999' };
  }
  if (!/^evt_[a-f0-9]{8}$/.test(event.event_id || '')) {
    return { ok: false, error: 'event_id is missing or invalid' };
  }
  if (!EVENT_TYPES.has(event.event_type)) {
    return { ok: false, error: `Unsupported event_type: ${event.event_type}` };
  }
  if (!Number.isInteger(event.timestamp) || event.timestamp < 1704067200000) {
    return { ok: false, error: 'timestamp must be a Unix millisecond integer' };
  }
  if (event.duration_since_last_event != null && Number(event.duration_since_last_event) < 0) {
    return { ok: false, error: 'duration_since_last_event must be >= 0' };
  }
  if (event.scroll_depth_last != null && !isBetween(event.scroll_depth_last, 0, 1)) {
    return { ok: false, error: 'scroll_depth_last must be between 0 and 1' };
  }
  if (event.scroll_depth_max != null && !isBetween(event.scroll_depth_max, 0, 1)) {
    return { ok: false, error: 'scroll_depth_max must be between 0 and 1' };
  }
  if (event.site_type != null && !SITE_TYPES.has(event.site_type)) {
    return { ok: false, error: `Unsupported site_type: ${event.site_type}` };
  }
  if (event.task_hint != null && !TASK_HINTS.has(event.task_hint)) {
    return { ok: false, error: `Unsupported task_hint: ${event.task_hint}` };
  }
  return { ok: true };
}

function isBetween(value, min, max) {
  const number = Number(value);
  return Number.isFinite(number) && number >= min && number <= max;
}
