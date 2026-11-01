import { ApiClient } from '/shared/js/api.js';
import { Realtime } from '/shared/js/realtime.js';
import { showLogin, logout } from '/shared/js/auth.js';
import { createMap, drawCampus, externalNavigationUrl, fitTo, icons } from '/shared/js/map.js';
import { GeoWatcher, permissionState, toReading, unsupportedReason, usesNativeLocation } from '/shared/js/geo.js';
import { themeToggle } from '/shared/js/theme.js';
import {
  $, ago, busy, connectionPill, dateTime, distance, drawer, duration, html, modal, raw, rupees,
  secondsSince, showError, statusBadge, toast,
} from '/shared/js/ui.js';

const api = new ApiClient('campusrick.driver.token');
const rt = new Realtime(() => api.token);
const SEND_EVERY_MS = 3000;
const RELEASE_REASONS = ['Vehicle problem', 'Too far from pickup', 'Emergency', 'Other'];

const S = {
  config: null, campus: null, user: null, map: null, I: null, layers: {},
  st: null,              // last /api/driver/state snapshot (authoritative)
  clockOffset: 0,        // server time minus local time, for offer countdowns
  gps: { status: 'off', message: '', reading: null, sentAt: null, ack: null },
  sim: { active: false, timer: null, pos: null },
  route: null, routeAt: 0, routeFrom: null, finished: null,
};

const watcher = new GeoWatcher({ onReading: onGpsReading, onError: onGpsError });

// ------------------------------------------------------------------ boot

async function boot() {
  api.onUnauthorized = () => { api.token = null; location.reload(); };
  try {
    [S.config, S.campus] = await Promise.all([api.get('/api/config', { auth: false }), api.get('/api/campus', { auth: false })]);
  } catch (err) {
    $('#sheet-body').innerHTML = html`<div class="empty"><div class="icon">&#x26A0;</div>${err.message}
      <div style="margin-top:12px"><button class="btn btn-primary" id="retry">Try again</button></div></div>`;
    $('#retry').addEventListener('click', () => location.reload());
    return;
  }
  S.I = icons();
  S.map = createMap($('#map'), S.config, { onTilesFailed: () => notice('Map tiles are not loading. Trip actions still work.', 'warn') });
  if (S.map) {
    drawCampus(S.map, S.campus);
    S.map.on('click', (e) => { if (S.sim.active) simulateAt(e.latlng.lat, e.latlng.lng); });
  }
  $('#theme-slot').replaceWith(themeToggle());
  connectionPill($('#conn'), rt);
  $('#sheet-handle').addEventListener('click', () => $('#sheet').classList.toggle('collapsed'));
  $('#center-btn').addEventListener('click', () => {
    const p = myPosition();
    if (p) S.map?.setView([p.lat, p.lng], 17); else toast('Your position is not known yet.');
  });
  $('#menu-btn').addEventListener('click', openMenu);
  if (!api.token) return askLogin();
  try {
    const me = await api.get('/api/auth/me');
    if (me.role !== 'driver') { api.token = null; return askLogin(); }
    start(me);
  } catch (err) {
    if (err.status === 401 || err.status === 403) { api.token = null; askLogin(); }
  }
}

function askLogin() {
  showLogin({ api, app: 'driver', askName: true, title: 'Driver sign in', subtitle: 'Campus e-rickshaw drivers only. New accounts need approval from the transport office.', onSuccess: start });
}

async function start(user) {
  S.user = user;
  $('#menu-btn').hidden = false;
  rt.on('sync', (data) => { if (data.role === 'driver') applyState(data); });
  rt.on('offer.new', (offer) => {
    S.st.offer = offer;
    navigator.vibrate?.([300, 150, 300]);
    render();
    fitTo(S.map, [myPosition(), offer.pickup]);
  });
  rt.on('offer.closed', (data) => {
    if (S.st?.offer?.offer_id !== data.offer_id) return;
    S.st.offer = null;
    if (data.reason === 'EXPIRED') toast('The ride offer expired.');
    if (data.reason === 'CANCELLED') toast('The passenger cancelled that request.');
    render();
  });
  rt.on('ride.updated', onRideUpdated);
  rt.on('ride.released', () => { S.st.active_ride = null; render(); });
  rt.on('notification', (n) => { if (['ride_cancelled', 'verification'].includes(n.kind)) toast(n.title + (n.body ? ` — ${n.body}` : '')); });
  rt.on('account.updated', () => refresh());
  rt.on('auth-failed', (e) => { stopGps(); api.token = null; toast(e.reason || 'Signed out', 'error'); setTimeout(() => location.reload(), 1500); });
  rt.on('status', ({ status }) => { if (status === 'online') flushLocation(); });
  rt.start();
  await refresh();
  setInterval(tick, 1000);
  setInterval(() => { if (rt.status !== 'online') refresh(true); }, 5000); // REST fallback while the socket is down
  setInterval(refreshDemand, 30000);
}

async function refresh(quiet = false) {
  try { applyState(await api.get('/api/driver/state')); } catch (err) { if (!quiet) showError(err); }
}

function applyState(st) {
  S.clockOffset = new Date(st.server_time).getTime() - Date.now();
  const before = S.st;
  S.st = st;
  if (!S.user.name && st.profile.name) S.user.name = st.profile.name;
  const onDuty = st.profile.is_online || st.active_ride;
  if (onDuty && !S.sim.active) startGps(); else if (!onDuty) stopGps();
  if (before?.active_ride?.id !== st.active_ride?.id) { S.route = null; S.routeAt = 0; }
  render();
  refreshRoute();
  if (st.profile.is_online && !before?.profile.is_online) refreshDemand();
}

function onRideUpdated(ride) {
  const current = S.st.active_ride;
  if (current && current.id === ride.id && ride.version < current.version) return;
  if (['ACCEPTED', 'ARRIVED', 'IN_PROGRESS'].includes(ride.status)) {
    const changed = current?.status !== ride.status;
    S.st.active_ride = ride;
    S.st.offer = null;
    if (changed) { S.route = null; S.routeAt = 0; }
  } else if (current && current.id === ride.id) {
    S.st.active_ride = null;
    if (ride.status === 'COMPLETED') S.finished = ride;
    if (ride.status === 'CANCELLED') toast(`Trip cancelled${ride.cancel_reason ? `: ${ride.cancel_reason}` : ''}`, 'error', 7000);
  }
  render();
  refreshRoute();
}

// ------------------------------------------------------------------ location

function myPosition() {
  if (S.sim.active && S.sim.pos) return S.sim.pos;
  return S.gps.reading || S.st?.location || null;
}

async function startGps() {
  if (watcher.active || S.sim.active) return;
  const reason = unsupportedReason();
  if (reason) { S.gps = { ...S.gps, status: 'error', message: reason }; render(); return; }
  // In the Android app the native plugin asks for permission itself (see shared/js/geo.js).
  if (!usesNativeLocation() && await permissionState() === 'denied') {
    S.gps = { ...S.gps, status: 'error', message: 'Location permission is blocked. Allow location for this site in browser settings, then reload.' };
    render();
    return;
  }
  S.gps = { ...S.gps, status: 'waiting', message: 'Waiting for GPS…' };
  watcher.start();
  requestWakeLock();
  render();
}

function stopGps() {
  watcher.stop();
  S.gps = { status: 'off', message: '', reading: null, sentAt: null, ack: null };
  releaseWakeLock();
}

function onGpsReading(reading) {
  S.gps.reading = reading;
  S.gps.status = 'active';
  S.gps.message = '';
  drawMe();
  maybeSend(reading);
  refreshRoute();
}

function onGpsError(err) {
  S.gps.status = err.code === 'timeout' && S.gps.reading ? 'active' : 'error';
  S.gps.message = err.message;
  if (err.code === 'denied') watcher.stop();
  render();
}

let lastSent = 0;
let queued = null;
function maybeSend(reading) {
  queued = reading;
  if (Date.now() - lastSent >= SEND_EVERY_MS) flushLocation();
}

async function flushLocation() {
  const reading = queued;
  if (!reading || !(S.st?.profile.is_online || S.st?.active_ride)) return;
  queued = null;
  lastSent = Date.now();
  let result;
  try {
    if (rt.connected) {
      const reply = await rt.request('location', reading);
      result = reply.type === 'location.ack' ? reply.data : { accepted: false, rejected: reply.data };
    } else {
      result = await api.post('/api/driver/location', reading);
    }
  } catch (err) {
    result = { accepted: false, rejected: { code: err.code || 'network', detail: err.message || 'Could not send location' } };
  }
  S.gps.ack = result;
  if (result.accepted) {
    S.gps.sentAt = new Date().toISOString();
    S.st.location = { ...reading, received_at: S.gps.sentAt };
    S.st.location_eligible = !!result.inside_campus;
  }
  updateGpsLine();
}

// Test simulator: explicit, labelled, and only available when the server enables it.
function simulateAt(lat, lng) {
  S.sim.pos = { lat, lng, accuracy_m: 5 };
  drawMe();
  queued = { lat, lng, accuracy_m: 5, heading: null, speed_mps: null, timestamp: new Date().toISOString(), source: 'simulator' };
  flushLocation();
  refreshRoute();
}

function startSimulator() {
  watcher.stop();
  S.sim.active = true;
  $('#sim-banner').hidden = false;
  const p = S.gps.reading || S.st.location || S.config.campus_center;
  simulateAt(p.lat, p.lng);
  render();
}

function stopSimulator() {
  clearInterval(S.sim.timer);
  S.sim = { active: false, timer: null, pos: null };
  $('#sim-banner').hidden = true;
  if (S.st.profile.is_online || S.st.active_ride) startGps();
  render();
}

function driveAlongRoute() {
  clearInterval(S.sim.timer);
  const path = S.route?.geometry?.length > 1 ? S.route.geometry.map(([lat, lng]) => ({ lat, lng })) : null;
  if (!path) { toast('No route to follow yet. Accept a ride or tap the map to place the vehicle.'); return; }
  const points = densify(path, 25);
  let i = 0;
  S.sim.timer = setInterval(() => {
    if (!S.sim.active || i >= points.length) { clearInterval(S.sim.timer); S.sim.timer = null; return; }
    simulateAt(points[i].lat, points[i].lng);
    i += 1;
  }, 1500);
}

function densify(path, stepM) {
  const out = [path[0]];
  for (let k = 1; k < path.length; k += 1) {
    const a = path[k - 1], b = path[k];
    const n = Math.max(1, Math.round(haversine(a, b) / stepM));
    for (let j = 1; j <= n; j += 1) out.push({ lat: a.lat + ((b.lat - a.lat) * j) / n, lng: a.lng + ((b.lng - a.lng) * j) / n });
  }
  return out;
}

let wakeLock = null;
async function requestWakeLock() {
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { wakeLock = null; }
}
function releaseWakeLock() { wakeLock?.release().catch(() => {}); wakeLock = null; }
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && watcher.active) requestWakeLock();
});

// ------------------------------------------------------------------ routing

function nextStop() {
  const r = S.st?.active_ride;
  if (!r) return S.st?.offer ? S.st.offer.pickup : null;
  return r.status === 'IN_PROGRESS' ? r.dropoff : r.pickup;
}

async function refreshRoute() {
  const from = myPosition();
  const to = nextStop();
  if (!from || !to || !S.st?.active_ride) { if (!S.st?.active_ride) S.route = null; drawLayers(); return; }
  const moved = S.routeFrom ? haversine(S.routeFrom, from) : Infinity;
  if (S.route && Date.now() - S.routeAt < 30000 && moved < 60) return;
  S.routeAt = Date.now();
  S.routeFrom = { lat: from.lat, lng: from.lng };
  try {
    S.route = await api.get(`/api/routes/preview?from_lat=${from.lat}&from_lng=${from.lng}&to_lat=${to.lat}&to_lng=${to.lng}`);
    render();
  } catch { /* keep last route */ }
}

// ------------------------------------------------------------------ rendering

function render() {
  if (!S.st) return;
  const st = S.st;
  const duty = $('#duty');
  duty.hidden = false;
  duty.className = `pill ${st.profile.is_online ? 'conn-online' : ''}`;
  duty.innerHTML = st.profile.is_online ? '<span class="dot"></span> On duty' : 'Off duty';

  let content;
  if (!st.profile.name || !st.vehicle) content = profileForm();
  else if (S.finished) content = finishedView(S.finished);
  else if (st.active_ride) content = tripView(st.active_ride);
  else if (st.offer) content = offerView(st.offer);
  else content = idleView();
  $('#sheet-body').innerHTML = content + (S.config.simulation_enabled && st.vehicle ? simPanel() : '');
  bind();
  drawLayers();
  updateGpsLine();
  tick();
}

function verificationBanner() {
  const v = S.st.profile.verification_status;
  if (v === 'approved' || !S.st.policy.require_verification) return '';
  const text = {
    pending: 'Your account is waiting for approval by the campus transport office. You can go online once it is approved.',
    rejected: 'Your driver application was not approved.',
    suspended: 'Your driver account is suspended.',
  }[v];
  return html`<div class="banner ${v === 'pending' ? 'banner-warn' : 'banner-danger'}" style="margin-bottom:12px"><div>${text}
    ${S.st.profile.verification_note ? raw(html`<br><span class="small">Note: ${S.st.profile.verification_note}</span>`) : ''}</div></div>`;
}

function profileForm() {
  return html`<h2>Driver profile</h2>
    <p class="muted">Passengers see your name and vehicle number when you accept a ride.</p>
    <form id="profile-form">
      <div class="field"><label for="p-name">Full name</label><input id="p-name" class="input" required maxlength="80" value="${S.st.profile.name || ''}" autocomplete="name"></div>
      <div class="field"><label for="p-vehicle">Vehicle registration number</label><input id="p-vehicle" class="input" required maxlength="20" placeholder="UK 08 ER 1234" value="${S.st.vehicle?.number || ''}" style="text-transform:uppercase"></div>
      <div class="field"><label for="p-capacity">Passenger seats</label><input id="p-capacity" class="input" type="number" required min="1" max="6" step="1" value="${S.st.vehicle?.capacity || 4}">
        <span class="hint">You are only offered rides whose group fits in your rickshaw.</span></div>
      <button class="btn btn-primary btn-block" type="submit">Save profile</button>
    </form>`;
}

function profileBody(root) {
  return { name: root.querySelector('#p-name').value.trim(), vehicle_number: root.querySelector('#p-vehicle').value.trim(),
    capacity: Number(root.querySelector('#p-capacity').value) };
}

function idleView() {
  const st = S.st;
  const online = st.profile.is_online;
  const approved = st.profile.verification_status === 'approved' || !st.policy.require_verification;
  return html`${raw(verificationBanner())}
    <button class="btn duty-toggle ${online ? 'btn-outline' : 'btn-primary'}" id="duty-btn" ${approved || online ? '' : 'disabled'}>
      ${online ? 'Go off duty' : 'Go on duty'}</button>
    <div class="gps-line" data-gps></div>
    ${online ? raw(html`<div class="empty" style="padding:16px 8px"><div class="icon">\u{1F4E1}</div>
      <strong>Waiting for ride requests</strong><br><span class="small">Offers appear here automatically. Keep this screen open while on duty.</span></div>
      ${raw(demandSummary())}`)
      : raw(html`<p class="muted small" style="text-align:center">Going on duty shares your live location with the dispatch system until you go off duty.</p>`)}`;
}

function demandSummary() {
  const cells = S.demand?.cells || [];
  if (!cells.length) return html`<p class="small muted" style="text-align:center;margin:0">No ride requests on campus in the last ${S.demand?.window_min || 60} min.</p>`;
  const waiting = cells.reduce((n, c) => n + c.waiting, 0);
  return html`<p class="small" style="text-align:center;margin:0">${waiting ? raw(html`<span class="badge badge-warn">${waiting} waiting now</span> `) : ''}
    Shaded areas on the map show where passengers booked in the last ${S.demand.window_min} min. Darker = busier.</p>`;
}

function partySize(n = 1) {
  return `${n} passenger${n > 1 ? 's' : ''}`;
}

function offerView(o) {
  return html`<div class="offer" role="alertdialog" aria-labelledby="offer-title">
    <div class="row-between"><h2 id="offer-title">New ride request</h2><span class="badge badge-accent">${o.fare.display} cash</span></div>
    <div class="big num">${distance(o.distance_to_pickup_m)} <span class="small muted">to pickup</span></div>
    <div class="row-between" style="margin-top:6px"><span class="strong">\u{1F465} ${partySize(o.passenger_count)}</span>
      ${o.fare_breakdown?.boost_per_passenger.paise ? raw(html`<span class="badge badge-ok">+${o.fare_breakdown.boost_per_passenger.display}/person boost</span>`) : ''}</div>
    <div class="route-stops small" style="margin:8px 0">
      <div>\u{1F7E2} <strong>${o.pickup.name}</strong></div><div>\u{1F534} ${o.dropoff.name}</div>
      <div class="muted">Trip ${distance(o.route.distance_m)}${o.route.approximate ? ' (approx.)' : ''} · passenger ${o.passenger_first_name}</div>
    </div>
    <div class="countdown" aria-hidden="true"><div data-countdown style="width:100%"></div></div>
    <div class="small muted" data-countdown-text></div>
    <div class="offer-actions" style="margin-top:10px">
      <button class="btn btn-outline" id="reject-btn">Decline</button>
      <button class="btn btn-primary primary-action" id="accept-btn">Accept</button>
    </div></div><div class="gps-line" data-gps></div>`;
}

function tripView(r) {
  const p = r.passenger || {};
  const target = r.status === 'IN_PROGRESS' ? r.dropoff : r.pickup;
  const steps = {
    ACCEPTED: ['Head to pickup', 'I have arrived', 'arrived'],
    ARRIVED: ['Waiting for passenger', 'Passenger on board — start trip', 'start'],
    IN_PROGRESS: ['Drive to drop-off', 'Complete trip', 'complete'],
  }[r.status];
  const eta = S.route ? html`<span class="num">${duration(S.route.duration_s)} · ${distance(S.route.distance_m)}${S.route.approximate ? ' (approx.)' : ''}</span>` : '';
  return html`
    <div class="step-head"><h2>${steps[0]}</h2>${raw(statusBadge(r.status))}</div>
    <div class="next-target">${target.name}</div>
    <div class="small muted">${raw(eta)}${r.status === 'ARRIVED' ? raw(html`<span data-waited></span>`) : ''}</div>
    <div class="passenger-row"><div><div class="strong">${p.name || 'Passenger'} · \u{1F465} ${partySize(r.passenger_count)}</div><div class="small muted">${r.fare.display} cash · ${r.pickup.name} → ${r.dropoff.name}</div></div>
      ${p.phone ? raw(html`<a class="btn btn-outline btn-icon" href="tel:${p.phone}" aria-label="Call passenger">\u{1F4DE}</a>`) : ''}</div>
    <button class="btn btn-primary btn-block primary-action" id="step-btn" data-step="${steps[2]}">${steps[1]}</button>
    <div class="action-row">
      <a class="btn btn-outline" href="${externalNavigationUrl(target.lat, target.lng)}" target="_blank" rel="noopener">\u{1F9ED} Navigate</a>
      ${r.status === 'ACCEPTED' ? raw('<button class="btn btn-ghost-danger" id="release-btn">Can’t make it</button>')
        : r.status === 'ARRIVED' ? raw('<button class="btn btn-ghost-danger" id="noshow-btn">Passenger no-show</button>') : raw('<span></span>')}
    </div>
    <div class="gps-line" data-gps></div>`;
}

function finishedView(r) {
  return html`<div class="step-head"><h2>Trip completed</h2>${raw(statusBadge('COMPLETED'))}</div>
    <div class="stat-grid"><div class="stat"><div class="label">Fare</div><div class="value">${r.fare.display}</div></div>
      <div class="stat"><div class="label">Payment</div><div class="value" style="font-size:16px">${r.payment?.status === 'PAID' ? 'Cash received' : 'Cash pending'}</div></div></div>
    ${r.payment?.status !== 'PAID' ? raw('<button class="btn btn-outline btn-block" style="margin-top:10px" id="collect-btn">Mark cash received</button>') : ''}
    <h3 style="text-align:center;margin-top:16px">Rate ${r.passenger?.name || 'the passenger'} (optional)</h3>
    <div class="stars">${[1, 2, 3, 4, 5].map((n) => raw(`<button type="button" data-star="${n}" aria-label="${n} star${n > 1 ? 's' : ''}">★</button>`))}</div>
    <button class="btn btn-primary btn-block" id="finish-btn">Back to waiting for rides</button>`;
}

function simPanel() {
  return html`<details class="sim-panel" ${S.sim.active ? 'open' : ''}>
    <summary>Test location simulator</summary>
    <p class="small" style="margin:8px 0 0">For demos and testing only. Positions are sent and shown as <strong>SIMULATED</strong>, never as GPS.
      ${S.sim.active ? 'Tap the map to move the vehicle.' : ''}</p>
    <div class="row">
      ${S.sim.active ? raw(`<button class="btn btn-sm btn-outline" id="sim-drive">Drive along route</button>
        <button class="btn btn-sm btn-outline" id="sim-pickup">Jump to pickup</button>
        <button class="btn btn-sm btn-danger" id="sim-stop">Stop simulator</button>`)
        : raw('<button class="btn btn-sm btn-outline" id="sim-start">Start simulator</button>')}
    </div></details>`;
}

function updateGpsLine() {
  const el = $('[data-gps]');
  if (!el || !S.st) return;
  const onDuty = S.st.profile.is_online || S.st.active_ride;
  if (!onDuty) { el.innerHTML = ''; return; }
  const g = S.gps;
  const parts = [];
  if (S.sim.active) parts.push('<span class="badge badge-sim">SIMULATED</span>');
  else if (g.status === 'active' && g.reading) parts.push(html`<span class="badge badge-ok">GPS ±${Math.round(g.reading.accuracy_m)} m</span>`);
  else if (g.status === 'waiting') parts.push('<span class="badge badge-warn">Waiting for GPS…</span>');
  else if (g.status === 'error') parts.push('<span class="badge badge-danger">No location</span>');
  if (g.ack && !g.ack.accepted && g.ack.rejected) parts.push(html`<span class="badge badge-danger">Not sent</span><span class="muted">${g.ack.rejected.detail}</span>`);
  else if (g.sentAt) parts.push(html`<span class="muted">Shared ${ago(g.sentAt)}</span>`);
  if (S.st.location && !S.st.location_eligible && g.ack?.warning) parts.push(html`<span class="badge badge-warn">${g.ack.warning}</span>`);
  if (g.status === 'error' && g.message) parts.push(html`<div class="banner banner-danger small" style="width:100%">${g.message}
    ${S.st.active_ride ? '' : ' You will not receive ride offers without a location.'}</div>`);
  el.innerHTML = parts.join(' ');
}

const REFRESH_FIX_MS = 15000;
let fixInFlight = false;
function keepLocationFresh() {
  if (queued && Date.now() - lastSent >= SEND_EVERY_MS) flushLocation();
  if (S.sim.active && S.sim.pos && !S.sim.timer && Date.now() - lastSent > REFRESH_FIX_MS) simulateAt(S.sim.pos.lat, S.sim.pos.lng);
  // A stationary phone may stop firing watchPosition; ask for a new real fix so the
  // server keeps a fresh position (we never re-stamp an old reading).
  const last = S.gps.reading ? new Date(S.gps.reading.timestamp).getTime() : 0;
  if (watcher.active && !usesNativeLocation() && !fixInFlight && Date.now() - last > REFRESH_FIX_MS) {
    fixInFlight = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => { fixInFlight = false; onGpsReading(toReading(pos)); },
      () => { fixInFlight = false; },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 },
    );
  }
}

function tick() {
  keepLocationFresh();
  const o = S.st?.offer;
  const bar = $('[data-countdown]');
  if (o && bar) {
    const left = (new Date(o.expires_at).getTime() - (Date.now() + S.clockOffset)) / 1000;
    const total = S.st.policy.offer_timeout_s;
    bar.style.width = `${Math.max(0, Math.min(100, (left / total) * 100))}%`;
    const text = $('[data-countdown-text]');
    if (text) text.textContent = left > 0 ? `Respond within ${Math.ceil(left)} s` : 'Offer expiring…';
  }
  const waited = $('[data-waited]');
  const r = S.st?.active_ride;
  if (waited && r?.arrived_at) waited.textContent = ` Waiting ${Math.floor(secondsSince(r.arrived_at) / 60)} min ${Math.floor(secondsSince(r.arrived_at) % 60)} s`;
  updateGpsLine();
}

function bind() {
  $('#profile-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.currentTarget.querySelector('button');
    await busy(btn, async () => {
      try {
        applyState(await api.put('/api/driver/profile', profileBody(document)));
        toast('Profile saved', 'success');
      } catch (err) { showError(err); }
    });
  });
  $('#duty-btn')?.addEventListener('click', (e) => busy(e.currentTarget, async () => {
    const goOnline = !S.st.profile.is_online;
    try {
      applyState(await api.post('/api/driver/online', { online: goOnline }));
      toast(goOnline ? 'You are on duty. Location sharing started.' : 'You are off duty. Location sharing stopped.', 'success');
    } catch (err) { showError(err); }
  }));
  $('#accept-btn')?.addEventListener('click', (e) => busy(e.currentTarget, async () => {
    const o = S.st.offer;
    try {
      const ride = await api.post(`/api/driver/rides/${o.ride_id}/accept`);
      S.st.offer = null;
      onRideUpdated(ride);
      fitTo(S.map, [myPosition(), ride.pickup]);
    } catch (err) { showError(err); S.st.offer = null; render(); }
  }));
  $('#reject-btn')?.addEventListener('click', (e) => busy(e.currentTarget, async () => {
    const o = S.st.offer;
    try { await api.post(`/api/driver/rides/${o.ride_id}/reject`); } catch (err) { if (err.status !== 409) showError(err); }
    S.st.offer = null;
    render();
  }));
  $('#step-btn')?.addEventListener('click', (e) => tripStep(e.currentTarget));
  $('#release-btn')?.addEventListener('click', async () => {
    const reason = await modal({ title: 'Release this ride?', body: 'The passenger will be offered to another driver.', danger: true,
      confirmText: 'Release ride', cancelText: 'Keep ride', input: { label: 'Reason', options: RELEASE_REASONS } });
    if (reason === null) return;
    try { await api.post(`/api/driver/rides/${S.st.active_ride.id}/cancel`, { reason }); S.st.active_ride = null; render(); } catch (err) { showError(err); }
  });
  $('#noshow-btn')?.addEventListener('click', async () => {
    const ok = await modal({ title: 'Report a no-show?', body: 'Only do this after waiting at the pickup point. The ride will be cancelled.', danger: true, confirmText: 'Cancel ride' });
    if (!ok) return;
    try { await api.post(`/api/driver/rides/${S.st.active_ride.id}/cancel`, { reason: 'Passenger no-show' }); await refresh(); } catch (err) { showError(err); }
  });
  $('#collect-btn')?.addEventListener('click', (e) => busy(e.currentTarget, async () => {
    try { await api.post(`/api/driver/rides/${S.finished.id}/collect-cash`); S.finished.payment = { status: 'PAID', method: 'cash' }; render(); } catch (err) { showError(err); }
  }));
  document.querySelectorAll('[data-star]').forEach((el) => el.addEventListener('click', async () => {
    const stars = Number(el.dataset.star);
    document.querySelectorAll('[data-star]').forEach((s) => s.classList.toggle('on', Number(s.dataset.star) <= stars));
    try { await api.post(`/api/rides/${S.finished.id}/rating`, { stars }); toast('Rating saved', 'success'); } catch (err) { showError(err); }
  }));
  $('#finish-btn')?.addEventListener('click', () => { S.finished = null; render(); });
  $('#sim-start')?.addEventListener('click', startSimulator);
  $('#sim-stop')?.addEventListener('click', stopSimulator);
  $('#sim-drive')?.addEventListener('click', driveAlongRoute);
  $('#sim-pickup')?.addEventListener('click', () => { const t = nextStop(); if (t) simulateAt(t.lat, t.lng); else toast('No pickup to jump to.'); });
}

async function tripStep(button) {
  const r = S.st.active_ride;
  const step = button.dataset.step;
  let body = {};
  if (step === 'start' && r.requires_pin) {
    const pin = await modal({ title: 'Enter the passenger’s PIN', body: 'Ask the passenger for the 4-digit PIN shown in their app. It confirms you picked up the right person.',
      confirmText: 'Start trip', cancelText: 'Back', input: { label: 'PIN', placeholder: '1234', inputmode: 'numeric', maxlength: 4 } });
    if (pin === null) return;
    body = { pin: pin.trim() };
  }
  if (step === 'complete') {
    const choice = await modal({ title: 'Complete trip', body: html`Collect <strong>${r.fare.display}</strong> in cash from the passenger.`,
      confirmText: 'Cash received — complete', cancelText: 'Not yet' });
    if (choice === null) return;
    body = { cash_collected: true };
  }
  await busy(button, async () => {
    try {
      flushLocation();
      const ride = await api.post(`/api/driver/rides/${r.id}/${step}`, body);
      onRideUpdated(ride);
    } catch (err) {
      showError(err);
      if (err.code === 'stale_ride' || err.code === 'invalid_transition') refresh(true);
    }
  });
}

// ------------------------------------------------------------------ map

function setMarker(key, p, icon, opts = {}) {
  if (!S.map) return;
  if (!p) { S.layers[key]?.remove(); delete S.layers[key]; return; }
  if (!S.layers[key]) S.layers[key] = L.marker([p.lat, p.lng], { icon, ...opts }).addTo(S.map);
  else { S.layers[key].setLatLng([p.lat, p.lng]); S.layers[key].setIcon(icon); }
}

function drawMe() {
  const p = myPosition();
  setMarker('me', p, S.I.vehicle(S.sim.active ? 'sim' : ''), { title: 'My rickshaw', zIndexOffset: 1000 });
  S.layers.acc?.remove();
  if (p && p.accuracy_m && S.map) S.layers.acc = L.circle([p.lat, p.lng], { radius: p.accuracy_m, color: '#1565c0', weight: 1, fillOpacity: 0.08, interactive: false }).addTo(S.map);
}

async function refreshDemand() {
  if (!S.st?.profile.is_online || S.st.active_ride || S.st.offer) return;
  try { S.demand = await api.get('/api/driver/demand'); } catch { return; } // keep the last map on a network blip
  if (!S.st.active_ride && !S.st.offer) render();
}

function drawDemand() {
  S.layers.demand?.remove();
  delete S.layers.demand;
  const cells = S.demand?.cells || [];
  if (!S.st.profile.is_online || S.st.active_ride || S.st.offer || !cells.length) return;
  const busiest = Math.max(...cells.map((c) => c.requests));
  S.layers.demand = L.layerGroup(cells.map((c) => L.circle([c.lat, c.lng], {
    radius: S.demand.cell_m * 0.6, stroke: c.waiting > 0, color: '#e65100', weight: 2,
    fillColor: c.waiting ? '#e65100' : '#f9a825', fillOpacity: 0.15 + 0.4 * (c.requests / busiest),
  }).bindTooltip(`${c.waiting ? `${c.waiting} waiting now · ` : ''}${c.requests} request${c.requests > 1 ? 's' : ''} in ${S.demand.window_min} min`
    + `${c.missed ? ` · ${c.missed} found no driver` : ''}`))).addTo(S.map);
}

function drawLayers() {
  if (!S.map || !S.st) return;
  drawDemand();
  drawMe();
  const r = S.st.active_ride;
  const o = S.st.offer;
  setMarker('offer', !r && o ? o.pickup : null, S.I.offer, { title: 'Requested pickup' });
  setMarker('pickup', r && r.status !== 'IN_PROGRESS' ? r.pickup : null, S.I.pickup, { title: 'Pickup' });
  setMarker('dropoff', r ? r.dropoff : null, S.I.dropoff, { title: 'Drop-off' });
  S.layers.route?.remove();
  delete S.layers.route;
  if (r && S.route?.geometry?.length > 1) {
    S.layers.route = L.polyline(S.route.geometry, { color: '#1565c0', weight: 6, opacity: 0.85, dashArray: S.route.approximate ? '8 8' : null }).addTo(S.map);
  }
}

// ------------------------------------------------------------------ menu

function openMenu() {
  const d = drawer('Menu', html`
    <div class="card"><div class="strong">${S.st?.profile.name || 'Driver'}</div><div class="small muted">${S.user.phone} · ${S.st?.vehicle?.number || 'No vehicle'}</div>
      <div style="margin-top:6px">${raw(statusBadgeFor(S.st?.profile.verification_status))}</div></div>
    <button class="list-item" data-go="earnings">\u{1F4B0} <span>Earnings</span></button>
    <button class="list-item" data-go="history">\u{1F9FE} <span>Trip history</span></button>
    <button class="list-item" data-go="profile">✏️ <span>Edit profile</span></button>
    <button class="list-item" data-go="logout">↪ <span>Sign out</span></button>`);
  d.body.addEventListener('click', async (e) => {
    const go = e.target.closest('[data-go]')?.dataset.go;
    if (!go) return;
    if (go === 'logout') {
      if (S.st?.active_ride) { toast('Finish or release your trip before signing out.', 'error'); return; }
      if (S.st?.profile.is_online) { try { await api.post('/api/driver/online', { online: false }); } catch { /* signing out anyway */ } }
      stopGps();
      return logout(api, rt);
    }
    d.close();
    if (go === 'earnings') openEarnings();
    if (go === 'history') openHistory();
    if (go === 'profile') {
      if (S.st.active_ride) { toast('Finish your current trip first.', 'error'); return; }
      const dd = drawer('Edit profile', profileForm());
      dd.body.querySelector('#profile-form').addEventListener('submit', async (ev) => {
        ev.preventDefault();
        try {
          applyState(await api.put('/api/driver/profile', profileBody(dd.body)));
          dd.close(); toast('Profile saved', 'success');
        } catch (err) { showError(err); }
      });
    }
  });
}

function statusBadgeFor(v) {
  const cls = { approved: 'badge-ok', pending: 'badge-warn', rejected: 'badge-danger', suspended: 'badge-danger' }[v] || '';
  return html`<span class="badge ${cls}">${v === 'approved' ? 'Verified driver' : `Verification: ${v}`}</span>`;
}

async function openEarnings() {
  const d = drawer('Earnings', '<div class="skeleton" style="height:90px"></div>');
  try {
    const e = await api.get('/api/driver/earnings');
    const block = (label, w) => html`<div class="card"><h3>${label}</h3><div class="stat-grid">
      <div class="stat"><div class="label">Trips</div><div class="value">${w.trips}</div></div>
      <div class="stat"><div class="label">Your share</div><div class="value">${rupees(w.driver_share_paise)}</div></div>
      <div class="stat"><div class="label">Cash collected</div><div class="value">${rupees(w.cash_collected_paise)}</div></div>
      ${w.cancellation_fees_paise ? raw(html`<div class="stat"><div class="label">Cancellation fees</div><div class="value">${rupees(w.cancellation_fees_paise)}</div></div>`) : ''}</div></div>`;
    d.body.innerHTML = block('Today', e.today) + block('Last 7 days', e.last_7_days) + block('All time', e.all_time) + html`
      <div class="card small"><div>Rating: <strong>${e.rating.average ? `★ ${e.rating.average.toFixed(1)} (${e.rating.count})` : 'No ratings yet'}</strong></div>
      <div>Offer acceptance: <strong>${e.acceptance_rate == null ? '—' : `${Math.round(e.acceptance_rate * 100)}%`}</strong></div>
      <div class="muted" style="margin-top:6px">Calculated from completed trips, plus cancellation fees settled at the transport office. Driver share: ${e.driver_share_percent}% of fares. ${e.timezone_note}</div></div>`;
  } catch (err) { d.body.innerHTML = html`<div class="empty">${err.message}</div>`; }
}

async function openHistory() {
  const d = drawer('Trip history', '<div class="skeleton" style="height:64px"></div>');
  try {
    const rides = await api.get('/api/driver/rides?limit=50');
    d.body.innerHTML = rides.length ? rides.map((r) => html`<div class="list-item" style="cursor:default">
      <div style="flex:1;min-width:0"><div class="strong" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.pickup.name} → ${r.dropoff.name}</div>
      <div class="small muted">${dateTime(r.created_at)} · ${r.fare.display}${r.payment ? ` · ${r.payment.status === 'PAID' ? 'paid' : 'cash pending'}` : ''}</div></div>
      ${raw(statusBadge(r.status))}</div>`).join('') : '<div class="empty"><div class="icon">\u{1F6FA}</div>No trips yet.</div>';
  } catch (err) { d.body.innerHTML = html`<div class="empty">${err.message}</div>`; }
}

function notice(text, kind = 'info') {
  const el = $('#map-notice');
  el.className = `map-notice banner banner-${kind}`;
  el.textContent = text;
  el.hidden = false;
}

function haversine(a, b) {
  const R = 6371008.8, toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat), dLng = toRad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

boot();
