/**
 * ActivityTracker — modular implementation.
 *
 * NOTE: Logic is inlined in content_script.js for deployment.
 *       See scroll_tracker.js header for rationale.
 *
 * Tracks user activity signals to support idle detection in the service worker:
 *  - mousemove, keydown, mousedown → send ACTIVITY_PING (throttled to 5 s)
 *  - visibilitychange              → send VISIBILITY_CHANGE immediately
 *
 * The service worker compares the last ping timestamp against IDLE_THRESHOLD_MS
 * to decide when to emit an `idle` event.
 */
export class ActivityTracker {
  constructor(pingFn, visibilityFn, throttleMs = 5_000) {
    this._ping        = pingFn;
    this._visibility  = visibilityFn;
    this._throttle    = throttleMs;
    this._lastPing    = 0;
    this._handlers    = {};
  }

  start() {
    const throttled = this._onActivity.bind(this);
    this._handlers = {
      mousemove:  throttled,
      keydown:    throttled,
      mousedown:  throttled,
      touchstart: throttled,
    };

    for (const [ev, fn] of Object.entries(this._handlers)) {
      window.addEventListener(ev, fn, { passive: true });
    }

    document.addEventListener('visibilitychange', this._onVisibilityChange.bind(this));
  }

  stop() {
    for (const [ev, fn] of Object.entries(this._handlers)) {
      window.removeEventListener(ev, fn);
    }
    document.removeEventListener('visibilitychange', this._onVisibilityChange.bind(this));
  }

  _onActivity() {
    const now = Date.now();
    if (now - this._lastPing < this._throttle) return;
    this._lastPing = now;
    this._ping();
  }

  _onVisibilityChange() {
    this._visibility(document.visibilityState === 'visible');
  }
}
