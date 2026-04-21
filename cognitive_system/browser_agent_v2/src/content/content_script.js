(function initContentBridge() {
  if (window.__COGNITIVE_AGENT_CONTENT__) {
    return;
  }
  window.__COGNITIVE_AGENT_CONTENT__ = true;

  function sendMessage(kind, payload) {
    try {
      chrome.runtime.sendMessage({ kind, payload });
    } catch (_error) {
      // Background service worker may be temporarily unavailable.
    }
  }

  if (window.ScrollTracker && typeof window.ScrollTracker.start === "function") {
    window.ScrollTracker.start((payload) => {
      sendMessage("content_scroll_update", payload);
    });
  }

  if (window.ActivityTracker && typeof window.ActivityTracker.start === "function") {
    window.ActivityTracker.start((payload) => {
      sendMessage("content_activity", payload);
    });
  }
})();

