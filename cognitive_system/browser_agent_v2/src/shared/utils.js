/**
 * Format seconds as MM:SS string.
 * @param {number} seconds
 * @returns {string}
 */
export function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Unix timestamp (seconds, float).
 * @returns {number}
 */
export function nowSec() {
  return Date.now() / 1000;
}
