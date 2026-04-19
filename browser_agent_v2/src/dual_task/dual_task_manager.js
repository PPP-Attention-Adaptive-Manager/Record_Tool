/**
 * DualTaskManager — scheduling utilities.
 *
 * The actual probe lifecycle (schedule → trigger → collect → record) is
 * implemented in SessionManager (session_manager.js) because it needs direct
 * access to the alarm API, active tab state, and the InfluxWriter.
 *
 * This module provides the pure-function helpers used by SessionManager so the
 * timing logic can be tested in isolation.
 */

export const RESPONSE_WINDOW_MS = 3_000;

/**
 * Return a random inter-probe delay in milliseconds within [minMs, maxMs].
 * Using uniform distribution — no clustering at the boundaries.
 *
 * @param {number} minMs
 * @param {number} maxMs
 * @returns {number}
 */
export function nextProbeDelayMs(minMs, maxMs) {
  return minMs + Math.random() * (maxMs - minMs);
}

/**
 * Determine whether a probe response is a hit, miss, or error.
 *
 * @param {{ reaction_time: number, success: number, error: number }} response
 * @returns {{ is_hit: boolean, is_miss: boolean, is_error: boolean }}
 */
export function classifyProbeResponse(response) {
  return {
    is_hit:   response.success === 1,
    is_miss:  response.reaction_time === -1,
    is_error: response.error === 1,
  };
}
