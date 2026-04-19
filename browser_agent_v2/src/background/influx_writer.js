/**
 * InfluxDB v2 writer — line protocol over HTTP.
 *
 * Design rules enforced here:
 *  - NO JSON storage, only line protocol
 *  - Batch writes (up to BATCH_SIZE lines, or every FLUSH_INTERVAL_MS)
 *  - On network failure, re-queues the batch (simple retry once)
 *  - Separate measurements for behavior_events / dual_task_events / questionnaire_events
 *    so each can have its own retention policy and downsampling task.
 */
import { INFLUX_CONFIG } from '../shared/constants.js';
import { escapeFieldString, escapeTag, msToNs } from '../shared/utils.js';

const WRITE_URL =
  `${INFLUX_CONFIG.URL}/api/v2/write` +
  `?org=${encodeURIComponent(INFLUX_CONFIG.ORG)}` +
  `&bucket=${encodeURIComponent(INFLUX_CONFIG.BUCKET)}` +
  `&precision=ns`;

// ─────────────────────────────────────────────────────────────────────────────
// Line protocol builders — one per measurement
// ─────────────────────────────────────────────────────────────────────────────

function buildBehaviorLine(e) {
  const tags = [
    `session_id=${escapeTag(e.session_id || 'none')}`,
    `user_id=${escapeTag(e.user_id     || 'anonymous')}`,
    `event_type=${escapeTag(e.event_type)}`,
    `domain=${escapeTag(e.domain       || 'unknown')}`,
    `site_type=${escapeTag(e.site_type  || 'unknown')}`,
    `task_hint=${escapeTag(e.task_hint  || 'unknown')}`,
  ].join(',');

  const fields = [
    // String fields — must be double-quoted
    `event_id="${escapeFieldString(e.event_id)}"`,
    `full_url="${escapeFieldString(e.full_url        || '')}"`,
    `path="${escapeFieldString(e.path               || '')}"`,
    `query_string="${escapeFieldString(e.query_string || '')}"`,
    `title="${escapeFieldString(e.title             || '')}"`,
    `visibility_state="${escapeFieldString(e.visibility_state || 'visible')}"`,

    // Integer fields — suffix i
    `duration_since_last_event=${Math.round(e.duration_since_last_event || 0)}i`,
    `scroll_delta_cumulative=${Math.round(e.scroll_delta_cumulative     || 0)}i`,
    `scroll_event_count=${Math.round(e.scroll_event_count               || 0)}i`,
    `tab_active=${e.tab_active            ? 1 : 0}i`,
    `chrome_in_foreground=${e.chrome_in_foreground ? 1 : 0}i`,

    // Float fields (no suffix)
    `scroll_depth_last=${+(e.scroll_depth_last || 0).toFixed(5)}`,
    `scroll_depth_max=${+(e.scroll_depth_max  || 0).toFixed(5)}`,
  ];

  if (e.tab_id    != null) fields.push(`tab_id=${e.tab_id}i`);
  if (e.window_id != null) fields.push(`window_id=${e.window_id}i`);

  return `behavior_events,${tags} ${fields.join(',')} ${msToNs(e.timestamp)}`;
}

function buildDualTaskLine(e) {
  const tags = [
    `session_id=${escapeTag(e.session_id || 'none')}`,
    `user_id=${escapeTag(e.user_id       || 'anonymous')}`,
  ].join(',');

  const fields = [
    `event_id="${escapeFieldString(e.event_id)}"`,
    `reaction_time=${Math.round(e.reaction_time ?? -1)}i`,
    `success=${e.success         ? 1 : 0}i`,
    `error=${e.error             ? 1 : 0}i`,
    `missed_response=${e.missed_response ? 1 : 0}i`,
  ].join(',');

  return `dual_task_events,${tags} ${fields} ${msToNs(e.timestamp)}`;
}

function buildQuestionnaireLine(e) {
  const tags = [
    `session_id=${escapeTag(e.session_id || 'none')}`,
    `user_id=${escapeTag(e.user_id       || 'anonymous')}`,
  ].join(',');

  const fields = [
    `event_id="${escapeFieldString(e.event_id)}"`,
    `mental_demand=${Math.round(e.mental_demand    || 0)}i`,
    `physical_demand=${Math.round(e.physical_demand || 0)}i`,
    `temporal_demand=${Math.round(e.temporal_demand || 0)}i`,
    `performance=${Math.round(e.performance        || 0)}i`,
    `effort=${Math.round(e.effort                  || 0)}i`,
    `frustration=${Math.round(e.frustration        || 0)}i`,
    `stress_self_report=${Math.round(e.stress_self_report || 0)}i`,
    `valence=${Math.round(e.valence                || 0)}i`,
    `arousal=${Math.round(e.arousal                || 0)}i`,
  ].join(',');

  return `questionnaire_events,${tags} ${fields} ${msToNs(e.timestamp)}`;
}

function toLineProtocol(event) {
  switch (event.event_type) {
    case 'dual_task':     return buildDualTaskLine(event);
    case 'questionnaire': return buildQuestionnaireLine(event);
    default:              return buildBehaviorLine(event);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Writer class
// ─────────────────────────────────────────────────────────────────────────────

export class InfluxWriter {
  constructor() {
    this._queue         = [];
    this._timer         = null;
    this.BATCH_SIZE     = 50;
    this.FLUSH_INTERVAL = 5_000; // ms
    this._retryPending  = false;
  }

  /**
   * Add one event to the write queue.
   * Triggers an immediate flush if the batch size is reached.
   */
  enqueue(event) {
    this._queue.push(toLineProtocol(event));

    if (this._queue.length >= this.BATCH_SIZE) {
      this._flush();
    } else if (!this._timer) {
      this._timer = setTimeout(() => this._flush(), this.FLUSH_INTERVAL);
    }
  }

  /** Drain the queue immediately — call before service worker shuts down. */
  async flushAll() {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    if (this._queue.length > 0) {
      await this._flush();
    }
  }

  // ─── Private ───────────────────────────────────────────────────────────────

  async _flush() {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    if (this._queue.length === 0) return;

    const batch = this._queue.splice(0);
    const body  = batch.join('\n');

    try {
      const res = await fetch(WRITE_URL, {
        method:  'POST',
        headers: {
          Authorization:  `Token ${INFLUX_CONFIG.TOKEN}`,
          'Content-Type': 'text/plain; charset=utf-8',
        },
        body,
      });

      if (!res.ok) {
        const detail = await res.text().catch(() => '');
        console.error(`[InfluxWriter] HTTP ${res.status}: ${detail}`);
        // Re-queue once; prevent infinite re-queue on persistent errors
        if (!this._retryPending) {
          this._retryPending = true;
          this._queue.unshift(...batch);
          setTimeout(() => {
            this._retryPending = false;
            this._flush();
          }, 10_000);
        }
      }
    } catch (err) {
      console.error('[InfluxWriter] Network error:', err.message);
      if (!this._retryPending) {
        this._retryPending = true;
        this._queue.unshift(...batch);
        setTimeout(() => {
          this._retryPending = false;
          this._flush();
        }, 10_000);
      }
    }
  }
}
