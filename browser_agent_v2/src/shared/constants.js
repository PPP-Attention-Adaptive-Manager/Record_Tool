export const SESSION_STATES = Object.freeze({
  INACTIVE: 'inactive',
  RUNNING: 'running',
  HIDDEN: 'hidden',
  BACKGROUND: 'background',
  IDLE: 'idle',
  FINISHED: 'finished',
});

export const EVENT_TYPES = Object.freeze({
  START_SESSION: 'start_session',
  SWITCH: 'switch',
  SCROLL: 'scroll',
  TAB_HIDDEN: 'tab_hidden',
  BACKGROUND: 'background',
  IDLE: 'idle',
  FOCUS: 'focus',
  END_SESSION: 'end_session',
  DUAL_TASK: 'dual_task',
  QUESTIONNAIRE: 'questionnaire',
});

export const IDLE_THRESHOLD_MS = 5 * 60 * 1_000;
export const FOCUS_CHECKPOINT_INTERVAL = 5;
export const IDLE_CHECK_INTERVAL = 1;
export const DUAL_TASK_MIN_MS = 60 * 1_000;
export const DUAL_TASK_MAX_MS = 120 * 1_000;
export const DUAL_TASK_RESPONSE_WINDOW = 3_000;
export const SCROLL_BATCH_INTERVAL_MS = 2_000;

export const CORE_CONFIG = Object.freeze({
  URL: 'http://localhost:8765',
  EVENT_BATCH_SIZE: 50,
  FLUSH_INTERVAL_MS: 5_000,
  MAX_RETRIES: 3,
  BACKOFF_BASE_MS: 1_000,
  MAX_PENDING_EVENTS: 1_000,
});

export const SITE_TYPES = Object.freeze({
  SOCIAL: 'social',
  PRODUCTIVITY: 'productivity',
  ENTERTAINMENT: 'entertainment',
  SEARCH: 'search',
  NEWS: 'news',
  DEVELOPMENT: 'development',
  SHOPPING: 'shopping',
  EDUCATION: 'education',
  COMMUNICATION: 'communication',
  UNKNOWN: 'unknown',
});

export const TASK_HINTS = Object.freeze({
  READING: 'reading',
  CODING: 'coding',
  SEARCHING: 'searching',
  WATCHING: 'watching',
  WRITING: 'writing',
  BROWSING: 'browsing',
  COMMUNICATING: 'communicating',
  SHOPPING: 'shopping',
  UNKNOWN: 'unknown',
});

export const STORAGE_KEYS = Object.freeze({
  SESSION_STATE: 'session_state',
  CORE_CONFIG: 'core_config',
});

export const ALARM_NAMES = Object.freeze({
  SESSION_END: 'alarm_session_end',
  IDLE_CHECK: 'alarm_idle_check',
  FOCUS_CHECKPOINT: 'alarm_focus_checkpoint',
  DUAL_TASK: 'alarm_dual_task',
  IPC_FLUSH: 'alarm_ipc_flush',
});
