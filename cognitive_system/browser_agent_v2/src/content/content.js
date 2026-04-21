(function () {
  "use strict";

  const SCROLL_FLUSH_DELAY_MS = 2000;
  const PROBE_DEFAULT_TIMEOUT_MS = 3000;

  let previousScrollY = window.scrollY;
  let scrollAccumulator = 0;
  let scrollFlushTimer = null;

  let activeProbe = null;

  function sendMessage(payload) {
    chrome.runtime.sendMessage(payload).catch(() => {});
  }

  function flushScroll() {
    if (scrollAccumulator === 0) {
      scrollFlushTimer = null;
      return;
    }

    const delta = scrollAccumulator;
    const total = window.scrollY;
    scrollAccumulator = 0;
    scrollFlushTimer = null;

    sendMessage({
      type: "scroll_event",
      scroll_delta_y: delta,
      scroll_total_y: total,
    });
  }

  function trackScroll() {
    const currentY = window.scrollY;
    scrollAccumulator += currentY - previousScrollY;
    previousScrollY = currentY;

    clearTimeout(scrollFlushTimer);
    scrollFlushTimer = setTimeout(flushScroll, SCROLL_FLUSH_DELAY_MS);
  }

  function notifyTabHidden() {
    clearTimeout(scrollFlushTimer);
    flushScroll();
    sendMessage({ type: "tab_hidden" });
  }

  function clearProbe(probe) {
    if (!probe) return;
    clearTimeout(probe.timeoutHandle);
    document.removeEventListener("click", probe.onOutsideClick, true);
    probe.overlay.remove();
    activeProbe = null;
  }

  function submitProbeResult(event) {
    sendMessage({
      type: "dual_task_result",
      event,
    });
  }

  function showProbe({ probeId, timeoutMs }) {
    if (activeProbe) {
      return { ok: false, reason: "probe_already_active" };
    }

    const startedAt = performance.now();
    const root = document.createElement("div");
    root.id = "__cog_dual_task_overlay__";
    root.style.position = "fixed";
    root.style.inset = "0";
    root.style.zIndex = "2147483647";
    root.style.pointerEvents = "none";
    root.style.display = "flex";
    root.style.alignItems = "center";
    root.style.justifyContent = "center";
    root.style.background = "rgba(12, 18, 35, 0.18)";

    const box = document.createElement("button");
    box.type = "button";
    box.textContent = "CLICK";
    box.style.width = "74px";
    box.style.height = "74px";
    box.style.border = "2px solid #12324b";
    box.style.background = "#25c4f5";
    box.style.color = "#062035";
    box.style.font = "700 11px/1 Arial, sans-serif";
    box.style.letterSpacing = "1px";
    box.style.cursor = "pointer";
    box.style.borderRadius = "6px";
    box.style.pointerEvents = "auto";
    box.style.boxShadow = "0 8px 22px rgba(0,0,0,0.35)";

    const instruction = document.createElement("div");
    instruction.textContent = "Click the square as fast as possible";
    instruction.style.position = "absolute";
    instruction.style.bottom = "24px";
    instruction.style.left = "50%";
    instruction.style.transform = "translateX(-50%)";
    instruction.style.background = "rgba(6, 18, 35, 0.92)";
    instruction.style.color = "#dcefff";
    instruction.style.font = "600 12px/1.2 Arial, sans-serif";
    instruction.style.padding = "8px 12px";
    instruction.style.borderRadius = "999px";
    instruction.style.pointerEvents = "none";

    root.appendChild(box);
    root.appendChild(instruction);
    document.documentElement.appendChild(root);

    function finish(result) {
      if (!activeProbe) return;
      clearProbe(activeProbe);
      submitProbeResult({
        probe_id: probeId,
        reaction_time_ms: result.reactionTimeMs,
        miss: result.miss,
        error: result.error,
        response_type: result.responseType,
      });
    }

    box.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const reactionTimeMs = Math.max(0, performance.now() - startedAt);
      finish({
        reactionTimeMs: Number(reactionTimeMs.toFixed(2)),
        miss: false,
        error: false,
        responseType: "target_click",
      });
    });

    const onOutsideClick = (event) => {
      if (event.target === box) {
        return;
      }
      const reactionTimeMs = Math.max(0, performance.now() - startedAt);
      finish({
        reactionTimeMs: Number(reactionTimeMs.toFixed(2)),
        miss: false,
        error: true,
        responseType: "invalid_click",
      });
    };
    document.addEventListener("click", onOutsideClick, true);

    const timeoutHandle = window.setTimeout(() => {
      finish({
        reactionTimeMs: 0,
        miss: true,
        error: false,
        responseType: "timeout",
      });
    }, timeoutMs);

    activeProbe = {
      overlay: root,
      onOutsideClick,
      timeoutHandle,
    };
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "show_dual_task_probe") {
      const result = showProbe({
        probeId: String(message.probe_id || ""),
        timeoutMs: Number(message.timeout_ms || PROBE_DEFAULT_TIMEOUT_MS),
      });
      sendResponse(result);
      return true;
    }
    return false;
  });

  window.addEventListener("scroll", trackScroll, { passive: true });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      notifyTabHidden();
    }
  });
})();

