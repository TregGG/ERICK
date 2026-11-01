// Small UI toolkit shared by the passenger, driver and admin apps.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);

// Tagged template that escapes every interpolation unless wrapped with raw().
export function html(strings, ...values) {
  return strings.reduce((out, str, i) => {
    if (i === 0) return str;
    const v = values[i - 1];
    const rendered = Array.isArray(v) ? v.map((x) => (x && x.__raw !== undefined ? x.__raw : esc(x))).join('')
      : v && v.__raw !== undefined ? v.__raw : esc(v);
    return out + rendered + str;
  }, '');
}
export const raw = (value) => ({ __raw: String(value ?? '') });

let toastHost;
export function toast(message, kind = 'info', ms = 4000) {
  if (!toastHost) {
    toastHost = document.createElement('div');
    toastHost.className = 'toasts';
    toastHost.setAttribute('role', 'status');
    toastHost.setAttribute('aria-live', 'polite');
    document.body.appendChild(toastHost);
  }
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  toastHost.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function showError(err, fallback = 'Something went wrong') {
  toast(err?.message || fallback, 'error', 6000);
}

// Runs an async action while showing a spinner on the button and preventing double taps.
export async function busy(button, action) {
  if (!button || button.dataset.busy) return undefined;
  button.dataset.busy = '1';
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<span class="spinner" aria-hidden="true"></span><span class="sr-only">Working</span>';
  try {
    return await action();
  } finally {
    delete button.dataset.busy;
    button.disabled = false;
    button.innerHTML = original;
  }
}

export function modal({ title, body = '', confirmText = 'Confirm', cancelText = 'Cancel', danger = false, input = null }) {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML = html`
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <h2 id="modal-title">${title}</h2>
        <div class="muted" style="margin-bottom:14px">${raw(body)}</div>
        ${input ? raw(html`<div class="field"><label for="modal-input">${input.label}</label>
          ${input.options ? raw(html`<select id="modal-input" class="input">${input.options.map((o) => raw(html`<option>${o}</option>`))}</select>`)
            : raw(html`<input id="modal-input" class="input" maxlength="${input.maxlength || 200}" inputmode="${input.inputmode || 'text'}" autocomplete="off" placeholder="${input.placeholder || ''}">`)}</div>`) : ''}
        <div class="row" style="justify-content:flex-end">
          <button class="btn btn-ghost" data-act="cancel">${cancelText}</button>
          <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${confirmText}</button>
        </div>
      </div>`;
    const close = (value) => { backdrop.remove(); document.removeEventListener('keydown', onKey); resolve(value); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop || e.target.dataset.act === 'cancel') close(null);
      if (e.target.dataset.act === 'ok') close(input ? ($('#modal-input', backdrop).value || '') : true);
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(backdrop);
    ($('#modal-input', backdrop) || $('[data-act="ok"]', backdrop)).focus();
  });
}

export function drawer(title, contentHtml) {
  const backdrop = document.createElement('div');
  backdrop.className = 'drawer-backdrop';
  const panel = document.createElement('aside');
  panel.className = 'drawer';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', title);
  panel.innerHTML = html`<div class="drawer-head"><button class="btn btn-ghost btn-icon" data-close aria-label="Close">&#x2715;</button>
    <h2>${title}</h2></div><div class="drawer-body">${raw(contentHtml)}</div>`;
  const close = () => { backdrop.remove(); panel.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  backdrop.addEventListener('click', close);
  panel.querySelector('[data-close]').addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.append(backdrop, panel);
  panel.querySelector('[data-close]').focus();
  return { panel, body: panel.querySelector('.drawer-body'), close };
}

// ---------- formatting ----------
export function rupees(paise) {
  if (paise == null) return '';
  return paise % 100 === 0 ? `₹${paise / 100}` : `₹${(paise / 100).toFixed(2)}`;
}
export function distance(m) {
  if (m == null) return '';
  return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`;
}
export function duration(s) {
  if (s == null) return '';
  const min = Math.max(1, Math.round(s / 60));
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} min`;
}
export function ago(iso, now = Date.now()) {
  if (!iso) return 'never';
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}
export function secondsSince(iso) { return iso ? (Date.now() - new Date(iso).getTime()) / 1000 : Infinity; }
export function dateTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

export const STATUS_LABELS = {
  REQUESTED: ['Finding a driver', 'badge-info'],
  OFFERED: ['Finding a driver', 'badge-info'],
  ACCEPTED: ['Driver on the way', 'badge-accent'],
  ARRIVED: ['Driver arrived', 'badge-warn'],
  IN_PROGRESS: ['On trip', 'badge-accent'],
  COMPLETED: ['Completed', 'badge-ok'],
  CANCELLED: ['Cancelled', 'badge-danger'],
  NO_DRIVER_AVAILABLE: ['No driver found', 'badge-danger'],
};
export function statusBadge(status) {
  const [label, cls] = STATUS_LABELS[status] || [status, ''];
  return html`<span class="badge ${cls}">${label}</span>`;
}

export function connectionPill(el, realtime) {
  const render = ({ status }) => {
    const label = { online: 'Live', connecting: 'Connecting…', offline: 'Offline' }[status] || status;
    el.className = `pill conn-${status}`;
    el.innerHTML = html`<span class="dot" aria-hidden="true"></span><span class="pill-label">${label}</span>`;
    el.title = status === 'online' ? 'Real-time connection active' : 'Real-time connection unavailable; retrying';
  };
  render({ status: realtime.status });
  realtime.on('status', render);
}
