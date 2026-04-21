const TRANSITIONS = {
  idle: new Set(["recording"]),
  recording: new Set(["paused", "idle"]),
  paused: new Set(["recording", "idle"])
};

export class RecordingStateManager {
  constructor() {
    this._state = "idle";
    this._sessionId = null;
  }

  getState() {
    return this._state;
  }

  getSessionId() {
    return this._sessionId;
  }

  isRecording() {
    return this._state === "recording";
  }

  isPaused() {
    return this._state === "paused";
  }

  transition(nextState, sessionId = null) {
    const allowed = TRANSITIONS[this._state] || new Set();
    if (!allowed.has(nextState)) {
      return false;
    }
    this._state = nextState;
    if (nextState === "recording" && sessionId) {
      this._sessionId = sessionId;
    }
    if (nextState === "idle") {
      this._sessionId = null;
    }
    return true;
  }

  startRecording(sessionId) {
    if (!sessionId) {
      return false;
    }
    if (this._state === "idle") {
      return this.transition("recording", sessionId);
    }
    if (this._state === "paused") {
      this._sessionId = sessionId || this._sessionId;
      return this.transition("recording", this._sessionId);
    }
    return false;
  }

  pauseRecording() {
    if (this._state !== "recording") {
      return false;
    }
    return this.transition("paused");
  }

  resumeRecording() {
    if (this._state !== "paused") {
      return false;
    }
    return this.transition("recording", this._sessionId);
  }

  stopRecording() {
    if (this._state === "idle") {
      return false;
    }
    return this.transition("idle");
  }
}

