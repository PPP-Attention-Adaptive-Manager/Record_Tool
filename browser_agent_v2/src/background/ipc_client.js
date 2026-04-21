import { CORE_CONFIG, STORAGE_KEYS } from '../shared/constants.js';
import { validateEvent } from '../shared/schemas.js';

const DB_NAME = 'recording_tool_core_sync';
const STORE_NAME = 'pending_events';
const MAX_BATCH_SIZE = CORE_CONFIG.EVENT_BATCH_SIZE;

export class CoreIpcClient {
  constructor() {
    this._flushTimer = null;
    this._flushing = false;
    this._retryCount = 0;
    this._status = {
      connection_state: 'idle',
      queued_events: 0,
      last_error: '',
      last_flush_at: null,
    };
    this._dbPromise = openDatabase();
    this.refreshQueueCount();
  }

  async startSession(payload) {
    const response = await this._request('/session/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    this._status.connection_state = 'online';
    this._status.last_error = '';
    return response;
  }

  async stopSession(sessionId) {
    await this.flushAll();
    const response = await this._request('/session/stop', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });
    this._status.connection_state = 'online';
    this._status.last_error = '';
    return response;
  }

  async getSessionStatus() {
    return this._request('/session/status', { method: 'GET' });
  }

  async shutdownCore(force = false) {
    return this._request('/shutdown', {
      method: 'POST',
      body: JSON.stringify({ force }),
    });
  }

  async enqueue(event) {
    const validation = validateEvent(event);
    if (!validation.ok) {
      throw new Error(validation.error);
    }

    await this._putEvent(event);
    await this.refreshQueueCount();
    this._status.connection_state = this._status.connection_state === 'offline'
      ? 'offline'
      : 'buffering';
    this._scheduleFlush(0);
  }

  async flushAll() {
    await this._flush(true);
  }

  async getStatus() {
    await this.refreshQueueCount();
    return { ...this._status };
  }

  async refreshQueueCount() {
    this._status.queued_events = await countEvents(this._dbPromise);
    return this._status.queued_events;
  }

  _scheduleFlush(delayMs) {
    if (this._flushTimer) {
      clearTimeout(this._flushTimer);
    }
    this._flushTimer = setTimeout(() => {
      this._flush().catch((error) => {
        console.warn('[CoreIpcClient] flush failed:', error.message);
      });
    }, delayMs ?? CORE_CONFIG.FLUSH_INTERVAL_MS);
  }

  async _flush(force = false) {
    if (this._flushing) {
      return;
    }
    this._flushing = true;

    if (this._flushTimer) {
      clearTimeout(this._flushTimer);
      this._flushTimer = null;
    }

    try {
      while (true) {
        const batch = await takeBatch(this._dbPromise, MAX_BATCH_SIZE);
        if (batch.length === 0) {
          this._status.connection_state = 'online';
          this._status.last_error = '';
          this._retryCount = 0;
          break;
        }

        const sessionId = batch[0].event.session_id;
        const payload = {
          session_id: sessionId,
          events: batch.map((item) => item.event),
        };

        try {
          await this._request('/events', {
            method: 'POST',
            body: JSON.stringify(payload),
          });
          await deleteBatch(this._dbPromise, batch.map((item) => item.id));
          this._status.last_flush_at = Date.now();
          await this.refreshQueueCount();

          if (!force && this._status.queued_events < MAX_BATCH_SIZE) {
            break;
          }
        } catch (error) {
          this._status.connection_state = 'offline';
          this._status.last_error = error.message;
          this._retryCount += 1;
          const backoff = Math.min(
            CORE_CONFIG.BACKOFF_BASE_MS * (2 ** Math.max(0, this._retryCount - 1)),
            30_000,
          );
          this._scheduleFlush(backoff);
          break;
        }
      }
    } finally {
      this._flushing = false;
    }
  }

  async _request(path, options) {
    const config = await loadConfig();
    const url = `${config.URL}${path}`;
    let response;

    try {
      response = await fetch(url, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...(options.headers ?? {}),
        },
      });
    } catch (error) {
      throw new Error(`Core unreachable at ${config.URL}: ${error.message}`);
    }

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.message || body.error || `HTTP ${response.status}`);
    }
    return body;
  }

  async _putEvent(event) {
    const db = await this._dbPromise;
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      const store = tx.objectStore(STORE_NAME);
      store.add({
        session_id: event.session_id,
        timestamp: event.timestamp,
        created_at: Date.now(),
        event,
      });
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error || new Error('Failed to store event'));
      tx.onabort = () => reject(tx.error || new Error('Storage transaction aborted'));
    });

    const maxPending = CORE_CONFIG.MAX_PENDING_EVENTS;
    const queued = await countEvents(this._dbPromise);
    if (queued > maxPending) {
      await trimOldest(this._dbPromise, queued - maxPending);
    }
  }
}

async function loadConfig() {
  try {
    const stored = await chrome.storage.local.get(STORAGE_KEYS.CORE_CONFIG);
    return { ...CORE_CONFIG, ...(stored[STORAGE_KEYS.CORE_CONFIG] ?? {}) };
  } catch {
    return { ...CORE_CONFIG };
  }
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: 'id',
          autoIncrement: true,
        });
        store.createIndex('session_id', 'session_id', { unique: false });
        store.createIndex('created_at', 'created_at', { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('Failed to open IndexedDB'));
  });
}

async function countEvents(dbPromise) {
  const db = await dbPromise;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const request = tx.objectStore(STORE_NAME).count();
    request.onsuccess = () => resolve(request.result ?? 0);
    request.onerror = () => reject(request.error || new Error('Failed to count pending events'));
  });
}

async function takeBatch(dbPromise, limit) {
  const db = await dbPromise;
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const store = tx.objectStore(STORE_NAME);
    const items = [];
    let firstSessionId = null;
    const request = store.openCursor();

    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor || items.length >= limit) {
        resolve(items);
        return;
      }
      if (firstSessionId === null) {
        firstSessionId = cursor.value.event.session_id;
      }
      if (cursor.value.event.session_id !== firstSessionId) {
        resolve(items);
        return;
      }
      items.push({ id: cursor.value.id, event: cursor.value.event });
      cursor.continue();
    };
    request.onerror = () => reject(request.error || new Error('Failed to read pending events'));
  });
}

async function deleteBatch(dbPromise, ids) {
  if (!ids.length) {
    return;
  }
  const db = await dbPromise;
  await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    for (const id of ids) {
      store.delete(id);
    }
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error || new Error('Failed to delete flushed events'));
    tx.onabort = () => reject(tx.error || new Error('Delete transaction aborted'));
  });
}

async function trimOldest(dbPromise, countToDelete) {
  if (countToDelete <= 0) {
    return;
  }
  const db = await dbPromise;
  const ids = await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const store = tx.objectStore(STORE_NAME);
    const idsToDelete = [];
    const request = store.openCursor();

    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor || idsToDelete.length >= countToDelete) {
        resolve(idsToDelete);
        return;
      }
      idsToDelete.push(cursor.value.id);
      cursor.continue();
    };
    request.onerror = () => reject(request.error || new Error('Failed to trim pending events'));
  });
  await deleteBatch(dbPromise, ids);
}
