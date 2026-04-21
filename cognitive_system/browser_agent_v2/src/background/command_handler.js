function controlEvent(eventType, sessionId, extra = {}) {
  return {
    event_type: eventType,
    timestamp_ms: Date.now(),
    session_id: sessionId || null,
    ...extra
  };
}

export function handleCommand(commandEnvelope, stateManager, eventBuffer) {
  const command = commandEnvelope?.command || "";
  const payload = commandEnvelope?.payload || {};
  const commandSessionId = payload.session_id || commandEnvelope?.session_id || null;

  if (!command) {
    return { applied: false, command: "" };
  }

  if (command === "start_recording") {
    const started = stateManager.startRecording(commandSessionId);
    if (started) {
      eventBuffer.appendEvent(
        controlEvent("focus", stateManager.getSessionId(), {
          focus_reason: "start_recording"
        })
      );
    }
    return { applied: started, command };
  }

  if (command === "pause_recording") {
    const sessionId = stateManager.getSessionId();
    eventBuffer.flushAllScroll("pause", { session_id: sessionId });
    const paused = stateManager.pauseRecording();
    if (paused) {
      eventBuffer.appendEvent(
        controlEvent("background", sessionId, {
          background_reason: payload.reason || "pause_recording"
        })
      );
    }
    return { applied: paused, command };
  }

  if (command === "resume_recording") {
    const resumed = stateManager.resumeRecording();
    if (resumed) {
      eventBuffer.appendEvent(
        controlEvent("focus", stateManager.getSessionId(), {
          focus_reason: "resume_recording"
        })
      );
    }
    return { applied: resumed, command };
  }

  if (command === "stop_recording") {
    const sessionId = stateManager.getSessionId() || commandSessionId;
    eventBuffer.flushAllScroll("stop", { session_id: sessionId });
    eventBuffer.appendEvent(
      controlEvent("background", sessionId, {
        background_reason: "stop_recording"
      })
    );
    const stopped = stateManager.stopRecording();
    return { applied: stopped, command };
  }

  return { applied: false, command };
}

