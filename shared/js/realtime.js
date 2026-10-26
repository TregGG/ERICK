// Authenticated WebSocket client with automatic reconnect and state resync.
//
// The server sends a full `sync` snapshot after every (re)connect, so missed
// events during a disconnect are recovered from authoritative state rather than replayed.

export class Realtime {
  constructor(getToken) {
    this.getToken = getToken;
    this.handlers = new Map();
    this.status = 'offline';
    this.ws = null;
    this.attempt = 0;
    this.retryTimer = null;
    this.stopped = true;
    this.pending = new Map();
    this.nextId = 1;
    this.heartbeat = null;
  }

  on(type, handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type).add(handler);
    return () => this.handlers.get(type).delete(handler);
  }

  emit(type, payload) {
    (this.handlers.get(type) || []).forEach((h) => {
      try { h(payload); } catch (err) { console.error(`handler for ${type} failed`, err); }
    });
  }

  setStatus(status, detail) {
    if (this.status === status) return;
    this.status = status;
    this.emit('status', { status, detail });
  }

  start() {
    this.stopped = false;
    this.connect();
    window.addEventListener('online', this._onOnline = () => this.reconnectNow());
    document.addEventListener('visibilitychange', this._onVisible = () => {
      if (document.visibilityState === 'visible' && this.status !== 'online') this.reconnectNow();
    });
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    clearInterval(this.heartbeat);
    window.removeEventListener('online', this._onOnline);
    document.removeEventListener('visibilitychange', this._onVisible);
    if (this.ws) { this.ws.onclose = null; this.ws.close(1000); this.ws = null; }
    this.setStatus('offline');
  }

  reconnectNow() {
    if (this.stopped) return;
    clearTimeout(this.retryTimer);
    this.attempt = 0;
    if (!this.ws || this.ws.readyState > 1) this.connect();
  }

  connect() {
    const token = this.getToken();
    if (!token || this.stopped) return;
    this.setStatus('connecting');
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${scheme}://${location.host}/ws`);
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }));
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.type === 'ready') {
        this.attempt = 0;
        this.setStatus('online');
        clearInterval(this.heartbeat);
        this.heartbeat = setInterval(() => this.send({ type: 'ping' }), 25000);
      }
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve, timer } = this.pending.get(msg.id);
        clearTimeout(timer);
        this.pending.delete(msg.id);
        resolve(msg);
      }
      this.emit(msg.type, msg.data, msg);
    };
    ws.onclose = (event) => {
      clearInterval(this.heartbeat);
      if (this.ws !== ws) return;
      this.ws = null;
      for (const { reject, timer } of this.pending.values()) { clearTimeout(timer); reject(new Error('socket closed')); }
      this.pending.clear();
      if (event.code === 4401 || event.code === 4403) {
        this.setStatus('offline', event.reason);
        this.stopped = true;
        this.emit('auth-failed', { code: event.code, reason: event.reason });
        return;
      }
      this.setStatus('offline');
      this.scheduleRetry();
    };
  }

  scheduleRetry() {
    if (this.stopped) return;
    const delay = Math.min(30000, 1000 * 2 ** this.attempt) * (0.7 + Math.random() * 0.6);
    this.attempt += 1;
    clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  get connected() { return this.ws && this.ws.readyState === WebSocket.OPEN && this.status === 'online'; }

  send(message) {
    if (!this.connected) return false;
    this.ws.send(JSON.stringify(message));
    return true;
  }

  // Send and wait for the reply carrying the same id (used for location acknowledgements).
  request(type, data, timeoutMs = 8000) {
    return new Promise((resolve, reject) => {
      if (!this.connected) { reject(new Error('not connected')); return; }
      const id = this.nextId++;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('timeout')); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ type, id, data }));
    });
  }

  requestSync() { this.send({ type: 'sync' }); }
}
