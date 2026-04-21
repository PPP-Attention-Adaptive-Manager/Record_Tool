(function bootstrapActivityTracker() {
  if (window.ActivityTracker) {
    return;
  }

  const listeners = new Set();
  const IDLE_TIMEOUT_MS = 30000;
  let started = false;
  let idleTimer = null;

  function emit(type, extra = {}) {
    const payload = {
      type,
      timestamp_ms: Date.now(),
      url: window.location.href,
      ...extra
    };
    for (const listener of listeners) {
      listener(payload);
    }
  }

  function resetIdleTimer() {
    if (idleTimer) {
      clearTimeout(idleTimer);
    }
    idleTimer = setTimeout(() => emit("idle"), IDLE_TIMEOUT_MS);
  }

  function onVisibilityChange() {
    if (document.hidden) {
      emit("tab_hidden");
    } else {
      emit("focus");
      resetIdleTimer();
    }
  }

  function onFocus() {
    emit("focus");
    resetIdleTimer();
  }

  function onBlur() {
    emit("background");
  }

  function onUserActivity() {
    resetIdleTimer();
  }

  function start() {
    if (started) {
      return;
    }
    started = true;
    document.addEventListener("visibilitychange", onVisibilityChange, true);
    window.addEventListener("focus", onFocus, true);
    window.addEventListener("blur", onBlur, true);
    window.addEventListener("mousemove", onUserActivity, true);
    window.addEventListener("keydown", onUserActivity, true);
    window.addEventListener("click", onUserActivity, true);
    window.addEventListener("scroll", onUserActivity, true);
    resetIdleTimer();
  }

  function stop() {
    if (!started) {
      return;
    }
    started = false;
    document.removeEventListener("visibilitychange", onVisibilityChange, true);
    window.removeEventListener("focus", onFocus, true);
    window.removeEventListener("blur", onBlur, true);
    window.removeEventListener("mousemove", onUserActivity, true);
    window.removeEventListener("keydown", onUserActivity, true);
    window.removeEventListener("click", onUserActivity, true);
    window.removeEventListener("scroll", onUserActivity, true);
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  window.ActivityTracker = {
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

