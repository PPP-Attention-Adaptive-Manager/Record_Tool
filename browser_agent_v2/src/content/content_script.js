/**
 * Content Script — deployed to every page.
 *
 * This file is intentionally self-contained (no ES module imports) because
 * Chrome does not support import/export in manifest-declared content scripts
 * without a bundler.  The three classes below are the canonical implementations
 * from scroll_tracker.js, activity_tracker.js, and dual_task/stimulus_ui.js —
 * kept in sync by convention.  Add Webpack/Rollup to eliminate this duplication.
 *
 * Responsibilities:
 *  1. Accumulate scroll data and batch-send to the service worker (2 s cadence)
 *  2. Throttle activity pings (5 s) for idle detection
 *  3. Forward visibility changes immediately
 *  4. Render dual-task stimulus overlays on demand
 */

// ─────────────────────────────────────────────────────────────────────────────
// ScrollTracker
// ─────────────────────────────────────────────────────────────────────────────
class ScrollTracker {
  constructor(sendFn, flushIntervalMs = 2_000) {
    this._send          = sendFn;
    this._flushInterval = flushIntervalMs;
    this._delta         = 0;
    this._depthMax      = 0;
    this._depthLast     = 0;
    this._eventCount    = 0;
    this._lastY         = null;
    this._timer         = null;
  }

  start() {
    this._lastY = window.scrollY;
    window.addEventListener('scroll', this._onScroll, { passive: true });
  }

  stop() {
    window.removeEventListener('scroll', this._onScroll);
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
  }

  _onScroll = () => {
    const y         = window.scrollY;
    const docH      = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight,
    );
    const viewH     = window.innerHeight;
    const maxScroll = docH - viewH;
    const depth     = maxScroll > 0 ? Math.min(y / maxScroll, 1.0) : 0;

    if (this._lastY !== null) this._delta += Math.abs(y - this._lastY);
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

    this._delta      = 0;
    this._eventCount = 0;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// ActivityTracker
// ─────────────────────────────────────────────────────────────────────────────
class ActivityTracker {
  constructor(pingFn, visibilityFn, throttleMs = 5_000) {
    this._ping       = pingFn;
    this._visibility = visibilityFn;
    this._throttle   = throttleMs;
    this._lastPing   = 0;
  }

  start() {
    const throttled = this._onActivity.bind(this);
    window.addEventListener('mousemove',  throttled, { passive: true });
    window.addEventListener('keydown',    throttled, { passive: true });
    window.addEventListener('mousedown',  throttled, { passive: true });
    window.addEventListener('touchstart', throttled, { passive: true });

    document.addEventListener('visibilitychange', () => {
      this._visibility(document.visibilityState === 'visible');
    });
  }

  _onActivity() {
    const now = Date.now();
    if (now - this._lastPing < this._throttle) return;
    this._lastPing = now;
    this._ping();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Dual-task stimulus UI
// ─────────────────────────────────────────────────────────────────────────────
function showStimulus(probeId, onResult) {
  const WINDOW_MS = 3_000;
  const triggerMs = Date.now();
  let responded   = false;

  const style = document.createElement('style');
  style.textContent = `
    @keyframes cog-probe-in {
      from { opacity:0; transform:translateY(-8px); }
      to   { opacity:1; transform:translateY(0); }
    }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.style.cssText = `
    all:initial;position:fixed;top:20px;right:20px;z-index:2147483647;
    background:rgba(15,15,15,.92);color:#fff;padding:14px 20px;
    border-radius:8px;border:2px solid #4ade80;
    font:600 14px/1.4 system-ui,sans-serif;cursor:pointer;
    box-shadow:0 4px 16px rgba(0,0,0,.5);user-select:none;
    animation:cog-probe-in 120ms ease-out;
  `;
  overlay.innerHTML = `
    <div style="margin-bottom:6px;letter-spacing:.5px;">● PRESS SPACE or CLICK</div>
    <div style="font-weight:400;font-size:12px;opacity:.65;">Respond as fast as you can</div>
  `;
  document.body.appendChild(overlay);

  const respond = () => {
    if (responded) return;
    responded = true;
    cleanup();
    onResult({ probeId, reaction_time: Date.now() - triggerMs, success: 1, error: 0 });
  };

  const onKey = (e) => {
    if (e.code === 'Space' || e.code === 'Enter') { e.preventDefault(); respond(); }
  };

  overlay.addEventListener('click', respond);
  document.addEventListener('keydown', onKey, { capture: true });

  const expiry = setTimeout(() => {
    if (responded) return;
    responded = true;
    cleanup();
    onResult({ probeId, reaction_time: -1, success: 0, error: 0 });
  }, WINDOW_MS);

  function cleanup() {
    clearTimeout(expiry);
    document.removeEventListener('keydown', onKey, { capture: true });
    overlay.remove();
    style.remove();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Initialise trackers
// ─────────────────────────────────────────────────────────────────────────────

// Guard: don't inject twice into the same document (e.g. bfcache restore)
if (!window.__cogLoadCollectorActive) {
  window.__cogLoadCollectorActive = true;

  const scroll = new ScrollTracker((data) => {
    safeSend({ type: 'SCROLL_DATA', payload: data });
  });

  const activity = new ActivityTracker(
    ()       => safeSend({ type: 'ACTIVITY_PING' }),
    (visible) => safeSend({ type: 'VISIBILITY_CHANGE', payload: { visible } }),
  );

  scroll.start();
  activity.start();
}

// ─────────────────────────────────────────────────────────────────────────────
// Inbound messages from service worker
// ─────────────────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'DUAL_TASK_SHOW') {
    showStimulus(message.probeId, (result) => {
      safeSend({ type: 'DUAL_TASK_RESPONSE', payload: result });
    });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * chrome.runtime.sendMessage can throw if the extension context was invalidated
 * (e.g. after an extension update).  Swallow silently to avoid console noise.
 */
function safeSend(message) {
  try {
    chrome.runtime.sendMessage(message);
  } catch {
    // Extension context invalidated — no-op
  }
}
