/**
 * Cryptographically random UUID v4.
 */
export function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function generateEventId() {
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    const bytes = new Uint8Array(4);
    crypto.getRandomValues(bytes);
    return `evt_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
  }
  return `evt_${Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0')}`;
}

/** Current time in milliseconds (Unix epoch). */
export function nowMs() {
  return Date.now();
}

/**
 * Current time as BigInt nanoseconds.
 * InfluxDB line protocol requires nanosecond precision timestamps.
 */
export function nowNs() {
  return BigInt(Date.now()) * 1_000_000n;
}

/**
 * Convert a millisecond timestamp to BigInt nanoseconds for InfluxDB.
 */
export function msToNs(ms) {
  return BigInt(ms) * 1_000_000n;
}

// ─────────────────────────────────────────────────────────────────────────────
// InfluxDB line-protocol escaping helpers
// Ref: https://docs.influxdata.com/influxdb/v2/reference/syntax/line-protocol/
// ─────────────────────────────────────────────────────────────────────────────

/** Escape a field value that is a string (must be double-quoted in LP). */
export function escapeFieldString(value) {
  if (typeof value !== 'string') return String(value);
  return value
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n');
}

/** Escape tag keys, tag values, and field keys (no spaces, commas, equals). */
export function escapeTag(value) {
  if (typeof value !== 'string') return String(value);
  return value
    .replace(/\\/g, '\\\\')
    .replace(/ /g, '\\ ')
    .replace(/,/g, '\\,')
    .replace(/=/g, '\\=');
}

/**
 * Clamp a number to [min, max].
 */
export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
