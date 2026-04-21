export class Sender {
  constructor(baseUrl) {
    this._baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async heartbeat(payload) {
    return this._post("/v1/extensions/heartbeat", payload);
  }

  async sendEvents(payload) {
    return this._post("/v1/extensions/events", payload);
  }

  async _post(path, payload) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4500);

    try {
      const response = await fetch(`${this._baseUrl}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      if (!response.ok) {
        return null;
      }
      return await response.json();
    } catch (_error) {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }
}

