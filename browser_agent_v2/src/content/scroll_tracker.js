/**
 * ScrollTracker — modular implementation.
 *
 * NOTE: Chrome content scripts do not support ES module imports natively.
 *       This file is the canonical, readable source.  Its logic is inlined
 *       verbatim inside content_script.js (the deployed file).
 *       Add a webpack/Rollup build step to use this file directly.
 *
 * Design:
 *  - Listens to window scroll events (passive, no jank)
 *  - Accumulates delta, depth, and event count
 *  - Batches and sends to the service worker every FLUSH_INTERVAL_MS
 *  - Does NOT send raw events — only the accumulated batch
 */
export class ScrollTracker {
  constructor(sendFn, flushIntervalMs = 2_000) {
    this._send          = sendFn;
    this._flushInterval = flushIntervalMs;

    // Accumulators (reset after each batch send)
    this._delta      = 0;
    this._depthMax   = 0;
    this._depthLast  = 0;
    this._eventCount = 0;
    this._lastY      = null;
    this._timer      = null;
  }

  start() {
    this._lastY = window.scrollY;
    window.addEventListener('scroll', this._onScroll, { passive: true });
  }

  stop() {
    window.removeEventListener('scroll', this._onScroll);
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
  }

  // Arrow function to preserve `this` when passed as event listener
  _onScroll = () => {
    const y           = window.scrollY;
    const docHeight   = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight,
    );
    const viewHeight  = window.innerHeight;
    const maxScroll   = docHeight - viewHeight;
    const depth       = maxScroll > 0 ? Math.min(y / maxScroll, 1.0) : 0;

    if (this._lastY !== null) {
      this._delta += Math.abs(y - this._lastY);
    }
    this._lastY      = y;
    this._depthLast  = depth;
    this._depthMax   = Math.max(this._depthMax, depth);
    this._eventCount++;

    if (!this._timer) {
      this._timer = setTimeout(() => this._flush(), this._flushInterval);
    }
  };

  _flush() {
    this._timer = null;
    if (this._eventCount === 0) return;

    this._send({
      delta:       this._delta,
      depth_last:  this._depthLast,
      depth_max:   this._depthMax,
      event_count: this._eventCount,
    });

    // Reset per-batch accumulators; depth values are re-observed on next scroll
    this._delta      = 0;
    this._eventCount = 0;
    // depth_max and depth_last intentionally NOT reset — service worker's
    // EventBuffer tracks the max across the whole flush interval.
  }
}
