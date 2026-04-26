const DEFAULT_SERVER_CONFIG = Object.freeze({
  http_host: "localhost",
  http_port: 8080,
  websocket_host: "localhost",
  websocket_port: 8765,
});

let runtimeConfigPromise = null;

function normalizeHost(value, fallback) {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function normalizePort(value, fallback) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeRuntimeConfig(rawConfig) {
  const rawServer =
    rawConfig && typeof rawConfig === "object" && rawConfig.server && typeof rawConfig.server === "object"
      ? rawConfig.server
      : {};

  return {
    server: {
      http_host: normalizeHost(rawServer.http_host, DEFAULT_SERVER_CONFIG.http_host),
      http_port: normalizePort(rawServer.http_port, DEFAULT_SERVER_CONFIG.http_port),
      websocket_host: normalizeHost(rawServer.websocket_host, DEFAULT_SERVER_CONFIG.websocket_host),
      websocket_port: normalizePort(rawServer.websocket_port, DEFAULT_SERVER_CONFIG.websocket_port),
    },
  };
}

export async function loadRuntimeConfig() {
  if (!runtimeConfigPromise) {
    runtimeConfigPromise = (async () => {
      try {
        const response = await fetch(chrome.runtime.getURL("config/runtime_config.json"), {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        return normalizeRuntimeConfig(payload);
      } catch (error) {
        console.warn("[config] Falling back to default runtime config", error);
        return normalizeRuntimeConfig({});
      }
    })();
  }

  return runtimeConfigPromise;
}

export function buildSystemAgentUrls(runtimeConfig) {
  const server = normalizeRuntimeConfig(runtimeConfig).server;
  return {
    httpBaseUrl: `http://${server.http_host}:${server.http_port}`,
    websocketUrl: `ws://${server.websocket_host}:${server.websocket_port}`,
  };
}
