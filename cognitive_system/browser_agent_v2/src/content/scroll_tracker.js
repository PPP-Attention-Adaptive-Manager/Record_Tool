(function bootstrapScrollTracker() {
  if (window.ScrollTracker) {
    return;
  }

  const listeners = new Set();
  let started = false;
  let deltaAccumulator = 0;
  let depthMax = 0;
  let eventCount = 0;
  let lastY = window.scrollY || 0;
  let flushTimer = null;

  function currentScrollY() {
    return window.scrollY || document.documentElement.scrollTop || 0;
  }

  function flush() {
    if (eventCount <= 0) {
      return;
    }
    const payload = {
      timestamp_ms: Date.now(),
      scroll_delta: deltaAccumulator,
      scroll_depth: depthMax,
      scroll_event_count: eventCount,
      url: window.location.href
    };
    for (const listener of listeners) {
      listener(payload);
    }
    deltaAccumulator = 0;
    depthMax = 0;
    eventCount = 0;
  }

  function onScroll() {
    const currentY = currentScrollY();
    const delta = Math.abs(currentY - lastY);
    lastY = currentY;

    deltaAccumulator += delta;
    depthMax = Math.max(depthMax, currentY);
    eventCount += 1;
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    lastY = currentScrollY();
    window.addEventListener("scroll", onScroll, { passive: true });
    flushTimer = setInterval(flush, 350);
    window.addEventListener("beforeunload", flush);
  }

  function stop() {
    if (!started) {
      return;
    }
    started = false;
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener("beforeunload", flush);
    if (flushTimer) {
      clearInterval(flushTimer);
      flushTimer = null;
    }
    flush();
  }

  window.ScrollTracker = {
    start(callback) {
      if (typeof callback === "function") {
        listeners.add(callback);
      }
      start();
    },
    stop(callback) {
      if (callback) {
        listeners.delete(callback);
      }
      if (listeners.size === 0) {
        stop();
      }
    }
  };
})();

