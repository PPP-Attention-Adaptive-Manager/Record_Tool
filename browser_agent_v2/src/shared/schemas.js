/**
 * Canonical event schemas — factory functions that guarantee all fields are
 * present and typed correctly.  Every event written to InfluxDB must be built
 * through one of these factories.
 */
import { generateUUID, nowMs } from './utils.js';

// ─────────────────────────────────────────────────────────────────────────────
// Behavioral event (all canonical event_types except dual_task/questionnaire)
// ─────────────────────────────────────────────────────────────────────────────
export function createBehaviorEvent(overrides = {}) {
  return {
    // ── Metadata ──────────────────────────────────────────────────────────
    session_id:               '',
    user_id:                  'anonymous',
    event_id:                 generateUUID(),
    event_type:               '',
    timestamp:                nowMs(),         // ms; converted to ns on write
    duration_since_last_event: 0,              // ms

    // ── Tab data ──────────────────────────────────────────────────────────
    tab_id:       null,
    window_id:    null,
    full_url:     '',
    domain:       '',
    path:         '',
    query_string: '',
    title:        '',

    // ── Behavioral aggregates ─────────────────────────────────────────────
    scroll_delta_cumulative: 0,   // total px scrolled since last flush
    scroll_depth_last:       0.0, // 0–1, last observed depth
    scroll_depth_max:        0.0, // 0–1, max ever observed for this interval
    scroll_event_count:      0,   // raw scroll event count (not written as raw events)

    // ── Context flags ─────────────────────────────────────────────────────
    tab_active:           true,
    visibility_state:     'visible',   // 'visible' | 'hidden'
    chrome_in_foreground: true,

    // ── Semantic helpers (rule-based, not ML) ─────────────────────────────
    site_type:  'unknown',
    task_hint:  'unknown',

    // Caller overrides any of the above
    ...overrides,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Dual-task probe event
// ─────────────────────────────────────────────────────────────────────────────
export function createDualTaskEvent(overrides = {}) {
  return {
    session_id:       '',
    user_id:          'anonymous',
    event_id:         generateUUID(),
    event_type:       'dual_task',
    timestamp:        nowMs(),

    reaction_time:    -1,  // ms; -1 = missed
    success:          0,   // 1 if responded in time
    error:            0,   // 1 if incorrect (reserved for future stimuli)
    missed_response:  0,   // 1 if no response within window

    ...overrides,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// End-of-session questionnaire event
// ─────────────────────────────────────────────────────────────────────────────
export function createQuestionnaireEvent(overrides = {}) {
  return {
    session_id:  '',
    user_id:     'anonymous',
    event_id:    generateUUID(),
    event_type:  'questionnaire',
    timestamp:   nowMs(),

    // NASA-TLX raw subscales (0–100)
    mental_demand:    0,
    physical_demand:  0,
    temporal_demand:  0,
    performance:      0,
    effort:           0,
    frustration:      0,

    // Stress (0–100)
    stress_self_report: 0,

    // Affect grid
    valence: 0,   // -50 … +50
    arousal: 0,   // 0 … 100

    ...overrides,
  };
}
