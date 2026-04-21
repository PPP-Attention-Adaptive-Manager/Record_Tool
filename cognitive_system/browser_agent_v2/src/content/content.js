/**
 * Content script: scroll aggregation + visibility events.
 * Runs in every page. Does NOT use ES modules (content script restriction).
 * Accumulates scroll delta and flushes on pause (2s idle) or tab hide.
 */

(function () {
  "use strict";

  const FLUSH_DELAY_MS = 2_000;

  let prevScrollY     = window.scrollY;
  let scrollAccumulator = 0;
  let flushTimer      = null;

  function flush() {
    if (scrollAccumulator === 0) {
      flushTimer = null;
      return;
    }
    const delta = scrollAccumulator;
    const total = window.scrollY;
    scrollAccumulator = 0;
    flushTimer        = null;

    chrome.runtime.sendMessage({
      type:           "scroll_event",
      scroll_delta_y: delta,
      scroll_total_y: total,
    }).catch(() => {});
  }

  window.addEventListener("scroll", () => {
    const current = window.scrollY;
    scrollAccumulator += current - prevScrollY;
    prevScrollY       = current;

    clearTimeout(flushTimer);
    flushTimer = setTimeout(flush, FLUSH_DELAY_MS);
  }, { passive: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(flushTimer);
      flush();
      chrome.runtime.sendMessage({ type: "tab_hidden" }).catch(() => {});
    }
  });
})();
