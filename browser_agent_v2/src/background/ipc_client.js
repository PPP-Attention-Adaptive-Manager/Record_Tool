/**
 * IPC Client for communicating with the Python core server.
 */
export class IPCClient {
  constructor(serverUrl = 'http://localhost:8765') {
    this.serverUrl = serverUrl;
    this.maxRetries = 3;
    this.retryDelay = 2000; // 2 seconds
  }

  async startSession(userId, durationMinutes) {
    return this._post('/session/start', {
      user_id: userId,
      duration_minutes: durationMinutes
    });
  }

  async stopSession(sessionId) {
    return this._post('/session/stop', {
      session_id: sessionId
    });
  }

  async sendEvents(sessionId, events) {
    return this._post('/events', {
      session_id: sessionId,
      events: events
    });
  }

  async getStatus() {
    try {
      const response = await fetch(`${this.serverUrl}/session/status`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' }
      });
      if (!response.ok) return { active: false };
      return await response.json();
    } catch (error) {
      console.error('[IPC] Failed to get status:', error);
      return { active: false };
    }
  }

  async _post(path, payload) {
    let attempt = 0;
    while (attempt < this.maxRetries) {
      try {
        const response = await fetch(`${this.serverUrl}${path}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (response.ok) {
          return await response.json();
        }

        const errorData = await response.json();
        throw new Error(errorData.message || `HTTP ${response.status}`);
      } catch (error) {
        attempt++;
        console.warn(`[IPC] POST ${path} failed (attempt ${attempt}/${this.maxRetries}):`, error);
        if (attempt >= this.maxRetries) throw error;
        await new Promise(resolve => setTimeout(resolve, this.retryDelay * Math.pow(2, attempt - 1)));
      }
    }
  }
}
