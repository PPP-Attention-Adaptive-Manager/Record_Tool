// ─────────────────────────────────────────────────────────────────────────────
// Session state machine states
// ─────────────────────────────────────────────────────────────────────────────
export const SESSION_STATES = Object.freeze({
  INACTIVE:   'inactive',
  RUNNING:    'running',
  HIDDEN:     'hidden',
  BACKGROUND: 'background',
  IDLE:       'idle',
  FINISHED:   'finished',
});

// ─────────────────────────────────────────────────────────────────────────────
// Event types — exactly the canonical set
// ─────────────────────────────────────────────────────────────────────────────
export const EVENT_TYPES = Object.freeze({
  START_SESSION: 'start_session',
  NEW_TAB:       'new_tab',
  SWITCH:        'switch',
  SCROLL:        'scroll',
  TAB_HIDDEN:    'tab_hidden',
  BACKGROUND:    'background',
  IDLE:          'idle',
  FOCUS:         'focus',
  END_SESSION:   'end_session',
  DUAL_TASK:     'dual_task',
  QUESTIONNAIRE: 'questionnaire',
});

// ─────────────────────────────────────────────────────────────────────────────
// Timing constants (all in milliseconds)
// ─────────────────────────────────────────────────────────────────────────────
export const IDLE_THRESHOLD_MS          = 5 * 60 * 1_000; // 5 minutes no activity
export const FOCUS_CHECKPOINT_INTERVAL  = 5;              // minutes (used as Chrome alarm)
export const IDLE_CHECK_INTERVAL        = 1;              // minutes (used as Chrome alarm)
export const DUAL_TASK_MIN_MS           = 60  * 1_000;   // 60 s minimum inter-probe delay
export const DUAL_TASK_MAX_MS           = 120 * 1_000;   // 120 s maximum
export const DUAL_TASK_RESPONSE_WINDOW  = 3_000;          // ms before probe expires
export const SCROLL_BATCH_INTERVAL_MS   = 2_000;          // content script flush cadence

// ─────────────────────────────────────────────────────────────────────────────
// InfluxDB connection — edit to match your deployment
// ─────────────────────────────────────────────────────────────────────────────
export const INFLUX_CONFIG = Object.freeze({
  URL:    'http://localhost:8086',
  TOKEN:  '',        // set your token here
  ORG:    'research',
  BUCKET: 'behavior',
});

// ─────────────────────────────────────────────────────────────────────────────
// Semantic classification vocabularies
// ─────────────────────────────────────────────────────────────────────────────
export const SITE_TYPES = Object.freeze({
  SOCIAL:          'social',
  PRODUCTIVITY:    'productivity',
  ENTERTAINMENT:   'entertainment',
  SEARCH:          'search',
  NEWS:            'news',
  DEVELOPMENT:     'development',
  SHOPPING:        'shopping',
  EDUCATION:       'education',
  COMMUNICATION:   'communication',
  UNKNOWN:         'unknown',
});

export const TASK_HINTS = Object.freeze({
  READING:        'reading',
  CODING:         'coding',
  SEARCHING:      'searching',
  WATCHING:       'watching',
  WRITING:        'writing',
  BROWSING:       'browsing',
  COMMUNICATING:  'communicating',
  UNKNOWN:        'unknown',
});

// ─────────────────────────────────────────────────────────────────────────────
// Chrome storage keys
// ─────────────────────────────────────────────────────────────────────────────
export const STORAGE_KEYS = Object.freeze({
  SESSION_STATE: 'session_state',
});

// ─────────────────────────────────────────────────────────────────────────────
// Chrome alarm names — single source of truth to avoid typos
// ─────────────────────────────────────────────────────────────────────────────
export const ALARM_NAMES = Object.freeze({
  SESSION_END:       'alarm_session_end',
  IDLE_CHECK:        'alarm_idle_check',
  FOCUS_CHECKPOINT:  'alarm_focus_checkpoint',
  DUAL_TASK:         'alarm_dual_task',
});
