// Shared REST client: auth header, timeouts, and one normalised error shape for every app.

export class ApiError extends Error {
  constructor(status, detail, code = 'error', extra = {}) {
    super(detail);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
  get isNetwork() { return this.status === 0; }
}

function describeValidation(detail) {
  if (!Array.isArray(detail)) return String(detail || 'Request failed');
  const first = detail[0] || {};
  const field = Array.isArray(first.loc) ? first.loc.filter((p) => p !== 'body').join(' ') : '';
  return `${field ? field + ': ' : ''}${(first.msg || 'Invalid input').replace(/^Value error, /, '')}`;
}

export class ApiClient {
  constructor(storageKey) {
    this.storageKey = storageKey;
    this.onUnauthorized = null;
  }

  get token() { return localStorage.getItem(this.storageKey); }
  set token(value) {
    if (value) localStorage.setItem(this.storageKey, value);
    else localStorage.removeItem(this.storageKey);
  }

  async request(method, path, { body, headers = {}, timeout = 15000, auth = true } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const finalHeaders = { Accept: 'application/json', ...headers };
    if (body !== undefined) finalHeaders['Content-Type'] = 'application/json';
    if (auth && this.token) finalHeaders.Authorization = `Bearer ${this.token}`;
    let response;
    try {
      response = await fetch(path, {
        method, headers: finalHeaders, signal: controller.signal,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      throw new ApiError(0, err.name === 'AbortError'
        ? 'The server took too long to respond. Check your connection and try again.'
        : 'Cannot reach the server. Check your internet connection.', 'network');
    } finally {
      clearTimeout(timer);
    }
    if (response.status === 204) return null;
    let data = null;
    try { data = await response.json(); } catch { /* non-JSON error page */ }
    if (!response.ok) {
      const { detail, code, ...extra } = data || {};
      const error = new ApiError(response.status, describeValidation(detail) || response.statusText, code || `http_${response.status}`, extra);
      if (response.status === 401 && auth && this.onUnauthorized) this.onUnauthorized(error);
      throw error;
    }
    return data;
  }

  get(path, opts) { return this.request('GET', path, opts); }
  post(path, body = {}, opts = {}) { return this.request('POST', path, { ...opts, body }); }
  put(path, body = {}, opts = {}) { return this.request('PUT', path, { ...opts, body }); }
  patch(path, body = {}, opts = {}) { return this.request('PATCH', path, { ...opts, body }); }
  del(path, opts) { return this.request('DELETE', path, opts); }
}

export function newIdempotencyKey() {
  return (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
}
