(function () {
  const REPORT_DELAY_MS = 750;

  const pending = {
    scroll_delta: 0,
    scroll_depth: 0
  };

  let lastScrollY = window.scrollY;
  let reportTimer = null;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function computeScrollDepth() {
    const bodyHeight = document.body ? document.body.scrollHeight : 0;
    const docHeight = document.documentElement
      ? document.documentElement.scrollHeight
      : 0;
    const maxHeight = Math.max(bodyHeight, docHeight, window.innerHeight, 1);
    const depth = (window.scrollY + window.innerHeight) / maxHeight;
    return clamp(depth, 0, 1);
  }

  function hasPendingData() {
    return pending.scroll_delta !== 0;
  }

  function resetPending() {
    pending.scroll_delta = 0;
    pending.scroll_depth = 0;
  }

  function flushPending() {
    reportTimer = null;
    if (!hasPendingData()) {
      return;
    }

    const payload = {
      type: "page_activity",
      url: window.location.href,
      scroll_delta: pending.scroll_delta,
      scroll_depth: pending.scroll_depth
    };

    safeSendMessage(payload);
    resetPending();
  }

  function scheduleFlush() {
    if (reportTimer != null) {
      return;
    }
    reportTimer = setTimeout(flushPending, REPORT_DELAY_MS);
  }

  window.addEventListener(
    "scroll",
    () => {
      const currentY = window.scrollY;
      const delta = currentY - lastScrollY;
      pending.scroll_delta += delta;
      pending.scroll_depth = Math.max(pending.scroll_depth, computeScrollDepth());
      lastScrollY = currentY;
      scheduleFlush();
    },
    { passive: true }
  );

  // Click and keyboard tracking intentionally disabled:
  // keep this content script focused on scroll-only behavior.

  document.addEventListener("visibilitychange", () => {
    sendVisibilityState();
    if (document.hidden) {
      flushPending();
    }
  });

  function canSendToRuntime() {
    return Boolean(globalThis.chrome && chrome.runtime && chrome.runtime.id);
  }

  function safeSendMessage(payload) {
    if (!canSendToRuntime()) {
      return;
    }

    try {
      chrome.runtime.sendMessage(payload, () => {
        // Access guarded to avoid "Extension context invalidated" noise
        // when the extension reloads while old content scripts are still alive.
        if (chrome?.runtime?.lastError) {
          return;
        }
      });
    } catch (_error) {
      // Extension context can be invalidated during reload/update/navigation.
      // We intentionally ignore this to keep page-side script stable.
    }
  }

  function sendInterrupt(reason) {
    safeSendMessage({
      type: "page_interrupt",
      reason,
      url: window.location.href
    });
  }

  function sendVisibilityState() {
    safeSendMessage({
      type: "page_visibility",
      visible: !document.hidden,
      url: window.location.href
    });
  }

  document.addEventListener("copy", () => sendInterrupt("copy"), true);
  document.addEventListener("paste", () => sendInterrupt("paste"), true);
  document.addEventListener("cut", () => sendInterrupt("cut"), true);

  sendVisibilityState();
  window.addEventListener("beforeunload", flushPending);
})();
