import { ApiClient, newIdempotencyKey } from '/shared/js/api.js';
import { Realtime } from '/shared/js/realtime.js';
import { showLogin, logout } from '/shared/js/auth.js';
import { createMap, drawCampus, fitTo, icons } from '/shared/js/map.js';
import { currentPosition } from '/shared/js/geo.js';
import { themeToggle } from '/shared/js/theme.js';
import {
  $, ago, busy, connectionPill, dateTime, distance, drawer, duration, esc, html, modal, raw, rupees,
  secondsSince, showError, statusBadge, toast,
} from '/shared/js/ui.js';

const api = new ApiClient('campusrick.passenger.token');
const rt = new Realtime(() => api.token);
const TERMINAL = ['COMPLETED', 'CANCELLED', 'NO_DRIVER_AVAILABLE'];
const BOOST_STEPS = [0, 500, 1000, 2000];
const CANCEL_REASONS = ['Plans changed', 'Driver is taking too long', 'Booked by mistake', 'Found another ride', 'Other'];

const S = {
  config: null, campus: null, user: null, map: null, I: null,
  ride: null,            // authoritative ride from the server (active or just finished)
  pickup: null, dropoff: null, field: 'pickup', searching: false, query: '',
  preview: null, nearby: null, places: [], requestKey: null,
  passengers: 1, boost: 0,   // party size and per-passenger boost (paise) for the next request
  fees: [],                  // unpaid cancellation fees; booking is blocked until they are settled
  layers: {}, lastRouteAt: 0, lastRouteFrom: null, driverRoute: null,
};

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
  S.map = createMap($('#map'), S.config, { onTilesFailed: () => notice('Map tiles are not loading. Booking still works; check your connection.', 'warn') });
  if (S.map) {
    drawCampus(S.map, S.campus);
    S.map.on('click', (e) => onMapTap(e.latlng));
  }
  $('#theme-slot').replaceWith(themeToggle());
  connectionPill($('#conn'), rt);
  $('#sheet-handle').addEventListener('click', () => $('#sheet').classList.toggle('collapsed'));
  $('#locate-btn').addEventListener('click', locateMe);
  $('#menu-btn').addEventListener('click', openMenu);

  if (!api.token) return askLogin();
  try {
    const me = await api.get('/api/auth/me');
    if (me.role !== 'passenger') { api.token = null; return askLogin(); }
    start(me);
  } catch (err) {
    if (err.status === 401 || err.status === 403) { api.token = null; askLogin(); } else showFatal(err);
  }
}

function askLogin() {
  showLogin({
    api, app: 'passenger', askName: true, title: 'Book a campus e-rickshaw',
    subtitle: 'Sign in with your mobile number. Fair per-person fares, live tracking.', onSuccess: start,
  });
}

function showFatal(err) {
  $('#sheet-body').innerHTML = html`<div class="empty"><div class="icon">&#x26A0;</div>${err.message}</div>`;
}

async function start(user) {
  S.user = user;
  $('#menu-btn').hidden = false;
  rt.on('sync', (data) => {
    if (data.role !== 'passenger') return;
    S.fees = data.outstanding_fees || [];
    applyRide(data.active_ride, { fromSync: true });
  });
  rt.on('ride.updated', (ride) => applyRide(ride));
  rt.on('driver.location', onDriverLocation);
  rt.on('notification', (n) => {
    if (!['ride_accepted'].includes(n.kind)) toast(n.title + (n.body ? ` — ${n.body}` : ''));
    if (['cancellation_fee', 'fee_settled'].includes(n.kind)) refreshFees();
  });
  rt.on('auth-failed', () => { api.token = null; toast('Your session ended. Please sign in again.', 'error'); setTimeout(() => location.reload(), 1500); });
  rt.on('status', ({ status }) => { if (status !== 'online') ensurePolling(); });
  rt.start();
  try {
    const state = await api.get('/api/passenger/state');
    S.fees = state.outstanding_fees || [];
    applyRide(state.active_ride, { fromSync: true });
  } catch (err) { showError(err); render(); }
  api.get('/api/passenger/places').then((p) => { S.places = p; render(); }).catch(() => {});
  setInterval(tick, 1000);
  setInterval(refreshNearby, 15000);
}

// Temporary REST fallback while the WebSocket is down.
let pollTimer = null;
function ensurePolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    if (rt.status === 'online') { clearInterval(pollTimer); pollTimer = null; return; }
    if (!S.ride || TERMINAL.includes(S.ride.status)) return;
    try { applyRide((await api.get('/api/passenger/state')).active_ride, { fromSync: true }); } catch { /* keep retrying */ }
  }, 5000);
}

// ------------------------------------------------------------------ ride state

function applyRide(ride, { fromSync = false } = {}) {
  const current = S.ride;
  if (!ride) {
    // A sync with no active ride: keep showing a finished ride's summary, otherwise go back to booking.
    if (current && !TERMINAL.includes(current.status) && fromSync) {
      api.get(`/api/passenger/rides/${current.id}`).then((r) => applyRide(r)).catch(() => { S.ride = null; render(); });
      return;
    }
    if (!current || !TERMINAL.includes(current.status)) S.ride = null;
    render();
    return;
  }
  if (current && current.id === ride.id && ride.version < current.version) return; // stale or out-of-order event
  const changed = !current || current.id !== ride.id || current.status !== ride.status;
  S.ride = ride;
  if (changed) {
    S.driverRoute = null;
    S.lastRouteAt = 0;
    if (ride.status === 'ACCEPTED' && current?.status !== 'ACCEPTED') toast(`${ride.driver?.name || 'A driver'} accepted your ride`, 'success');
  }
  render();
  if (changed) fitRide();
  maybeRefreshDriverRoute();
}

function onDriverLocation(loc) {
  if (!S.ride || loc.ride_id !== S.ride.id) return;
  S.ride.driver_location = loc;
  drawRideLayers();
  updateFreshness();
  maybeRefreshDriverRoute();
}

async function maybeRefreshDriverRoute() {
  const r = S.ride;
  const loc = r?.driver_location;
  if (!r || !loc || !['ACCEPTED', 'IN_PROGRESS'].includes(r.status)) return;
  const target = r.status === 'ACCEPTED' ? r.pickup : r.dropoff;
  const moved = S.lastRouteFrom ? haversine(S.lastRouteFrom, loc) : Infinity;
  if (Date.now() - S.lastRouteAt < 20000 && moved < 60) return;
  S.lastRouteAt = Date.now();
  S.lastRouteFrom = { lat: loc.lat, lng: loc.lng };
  try {
    const route = await api.get(`/api/routes/preview?from_lat=${loc.lat}&from_lng=${loc.lng}&to_lat=${target.lat}&to_lng=${target.lng}`);
    if (S.ride?.id !== r.id || S.ride.status !== r.status) return;
    S.driverRoute = route;
    render();
  } catch { /* ETA stays on the last known value */ }
}

// ------------------------------------------------------------------ rendering

function render() {
  const body = $('#sheet-body');
  if (!S.user) return;
  const r = S.ride;
  if (!r) body.innerHTML = bookingView();
  else if (['REQUESTED', 'OFFERED'].includes(r.status)) body.innerHTML = searchingView(r);
  else if (['ACCEPTED', 'ARRIVED', 'IN_PROGRESS'].includes(r.status)) body.innerHTML = activeView(r);
  else body.innerHTML = finishedView(r);
  bindSheet();
  drawRideLayers();
  updateFreshness();
}

function locField(kind, label, point) {
  return html`<button class="loc-field ${S.field === kind && S.searching ? 'active' : ''}" data-field="${kind}" type="button" aria-label="Choose ${label}">
    <span class="marker ${kind}" aria-hidden="true"></span>
    <span class="text"><small>${label}</small>${point ? raw(html`<span>${point.name}</span>`) : raw(`<span class="placeholder">${kind === 'pickup' ? 'Where are you?' : 'Where to?'}</span>`)}</span>
  </button>`;
}

function fareTotal() {
  return (S.config.fare.paise + S.boost) * S.passengers;
}

function partyPicker() {
  const fare = S.config.fare;
  const steps = BOOST_STEPS.filter((b) => b <= fare.max_boost.paise);
  return html`<div class="party">
    <div class="row-between"><span class="strong">Passengers</span>
      <div class="stepper" role="group" aria-label="Number of passengers">
        <button type="button" class="btn btn-sm btn-outline" data-count="-1" aria-label="Fewer passengers" ${S.passengers <= 1 ? 'disabled' : ''}>−</button>
        <span class="num strong" aria-live="polite">${S.passengers}</span>
        <button type="button" class="btn btn-sm btn-outline" data-count="1" aria-label="More passengers" ${S.passengers >= fare.max_passengers ? 'disabled' : ''}>+</button>
      </div></div>
    ${steps.length > 1 ? raw(html`<div class="row-between"><span>Boost <span class="small muted">per person · helps at peak times</span></span>
      <div class="chips">${steps.map((b) => raw(html`<button type="button" class="btn btn-sm ${b === S.boost ? 'btn-primary' : 'btn-outline'}" data-boost="${b}"
        aria-pressed="${b === S.boost}">${b ? `+${rupees(b)}` : 'None'}</button>`))}</div></div>`) : ''}
    <div class="small muted">(${fare.display}${S.boost ? ` + ${rupees(S.boost)}` : ''}) × ${S.passengers} passenger${S.passengers > 1 ? 's' : ''} = <strong>${rupees(fareTotal())}</strong></div>
  </div>`;
}

function feesBanner() {
  if (!S.fees.length) return '';
  const total = S.fees.reduce((sum, f) => sum + f.amount.paise, 0);
  return html`<div class="banner banner-danger small" style="margin-bottom:10px"><div><strong>You owe a ${rupees(total)} cancellation fee.</strong>
    You cancelled while your driver was on the way. Settle it with the transport office to book again.</div></div>`;
}

function bookingView() {
  const p = S.preview;
  const total = rupees(fareTotal());
  let summary = '';
  if (S.pickup && S.dropoff) {
    summary = p ? html`<div class="trip-summary">
        <span class="strong">${distance(p.distance_m)} · ${duration(p.duration_s)}</span>
        <span class="fare num">${total}</span>
        <span class="small muted">${p.approximate ? 'Approximate straight-line estimate (road routing unavailable)' : 'Road route estimate'}</span>
      </div>` : '<div class="skeleton" style="height:62px;margin-bottom:12px"></div>';
  }
  const nearby = S.nearby == null ? '' : S.nearby.count > 0
    ? html`<p class="small" style="margin:0 0 10px"><span class="badge badge-ok">${S.nearby.count} nearby</span> rickshaw${S.nearby.count === 1 ? '' : 's'} online near your pickup</p>`
    : html`<div class="banner banner-warn small" style="margin-bottom:10px">No rickshaws are online near this pickup right now. You can still request; we will keep looking for a few minutes.</div>`;
  return html`
    <h2>Where to?</h2>
    <div class="loc-fields">${raw(locField('pickup', 'Pickup', S.pickup))}${raw(locField('dropoff', 'Drop-off', S.dropoff))}</div>
    ${S.searching ? raw(searchPanel()) : raw(html`<p class="hint" style="margin:-4px 0 12px">Tap a field to search, or tap the map to drop a pin. Pins can be dragged.</p>`)}
    ${raw(feesBanner())}${raw(summary)}${raw(partyPicker())}${raw(nearby)}
    <button class="btn btn-primary btn-block" id="request-btn" ${S.pickup && S.dropoff && !S.fees.length ? '' : 'disabled'}>
      Request rickshaw · ${total}</button>
    <p class="small muted" style="text-align:center;margin:10px 0 0">Fare per passenger, paid in cash to the driver.</p>`;
}

function searchPanel() {
  const q = S.query.trim().toLowerCase();
  const match = (name) => !q || name.toLowerCase().includes(q);
  const places = S.places.filter((p) => match(p.label) || match(p.name));
  const landmarks = S.campus.landmarks.filter((l) => match(l.name))
    .sort((a, b) => (b.is_pickup_point - a.is_pickup_point) || a.name.localeCompare(b.name));
  const item = (attrs, title, cat = '') => html`<button type="button" ${raw(attrs)}><span>${title}</span>${cat ? raw(html`<span class="cat">${cat}</span>`) : ''}</button>`;
  return html`<div class="search-panel">
    <label class="sr-only" for="place-search">Search campus places</label>
    <input id="place-search" class="input" placeholder="Search hostels, departments, gates…" value="${S.query}" autocomplete="off">
    <div class="search-results" role="listbox">
      ${S.field === 'pickup' && !q ? raw(item('data-use-location', '\u{1F4CD} Use my current location')) : ''}
      ${places.length ? raw(`<div class="group">Saved</div>${places.map((p) => item(`data-place="${esc(p.id)}"`, `★ ${p.label}`, p.name)).join('')}`) : ''}
      ${landmarks.length ? raw(`<div class="group">Campus places</div>${landmarks.map((l) => item(`data-landmark="${l.id}"`, l.name, l.is_pickup_point ? 'pickup point' : l.category)).join('')}`)
        : raw('<div class="empty small">No campus places match. Tap the map to drop a pin instead.</div>')}
    </div></div>`;
}

function trip(r) {
  return html`<div class="route-stops">
    <div><span class="loc-field-marker" aria-hidden="true">\u{1F7E2}</span><span>${r.pickup.name}</span></div>
    <div><span aria-hidden="true">\u{1F534}</span><span>${r.dropoff.name}</span></div></div>
    <div class="row-between small muted"><span>${distance(r.route.distance_m)} · ${duration(r.route.duration_s)}${r.route.approximate ? ' (approx.)' : ''}</span>
    <span class="strong" style="color:var(--text)">${r.fare.display} cash</span></div>
    <div class="small muted">\u{1F465} ${r.passenger_count} passenger${r.passenger_count > 1 ? 's' : ''}${r.fare_breakdown.boost_per_passenger.paise ? ` · includes +${r.fare_breakdown.boost_per_passenger.display}/person boost` : ''}</div>`;
}

function searchingView(r) {
  const left = Math.max(0, Math.round((new Date(r.search_expires_at) - Date.now()) / 1000));
  return html`
    <div class="pulse" aria-hidden="true">\u{1F6FA}</div>
    <h2 style="text-align:center;margin-bottom:4px">Finding you a rickshaw</h2>
    <p class="muted" style="text-align:center;margin:0 0 14px">${r.status === 'OFFERED' ? 'A nearby driver is reviewing your request…' : 'Looking for the nearest available driver…'}
      <br><span class="small" data-search-left>${left > 0 ? `Searching for up to ${Math.ceil(left / 60)} more min` : 'Finishing search…'}</span></p>
    ${raw(trip(r))}${raw(boostOffer(r))}<hr class="divider">
    <button class="btn btn-ghost-danger btn-block" id="cancel-btn">Cancel request</button>`;
}

function boostOffer(r) {
  const fare = S.config.fare;
  const steps = BOOST_STEPS.filter((b) => b > r.fare_breakdown.boost_per_passenger.paise && b <= fare.max_boost.paise);
  if (!steps.length || r.fare_breakdown.boost_count >= fare.max_boosts_per_ride) return '';
  return html`<div class="party" style="margin-top:12px"><span class="small strong">Taking a while? Offer drivers more (per person)</span>
    <div class="chips">${steps.map((b) => raw(html`<button type="button" class="btn btn-sm btn-outline" data-raise="${b}">+${rupees(b)}</button>`))}</div></div>`;
}

function driverCard(r) {
  const d = r.driver || {};
  const rating = d.rating?.average ? html`★ ${d.rating.average.toFixed(1)} (${d.rating.count})` : 'New driver';
  return html`<div class="driver-card">
    <div class="avatar" aria-hidden="true">${(d.name || 'D').trim().charAt(0).toUpperCase()}</div>
    <div><div class="strong">${d.name || 'Your driver'}</div><div class="small muted">${raw(rating)}</div>
      <div class="plate">${d.vehicle_number || ''}</div></div>
    ${d.phone ? raw(html`<a class="btn btn-outline btn-icon" href="tel:${d.phone}" aria-label="Call driver">\u{1F4DE}</a>`) : ''}
  </div><div class="freshness" data-freshness></div>`;
}

function activeView(r) {
  const titles = { ACCEPTED: 'Driver on the way', ARRIVED: 'Your rickshaw is here', IN_PROGRESS: 'On your way' };
  const route = S.driverRoute;
  let eta = '';
  if (r.status === 'ARRIVED') eta = html`<p class="muted" style="margin:0 0 12px">Waiting at <strong>${r.pickup.name}</strong>. Please head to the rickshaw.</p>`;
  else if (route) eta = html`<p style="margin:0 0 12px"><span class="eta-big num">${duration(route.duration_s)}</span>
      <span class="muted"> ${r.status === 'ACCEPTED' ? 'to pickup' : 'to destination'} · ${distance(route.distance_m)}${route.approximate ? ' (approx.)' : ''}</span></p>`;
  else if (!r.driver_location) eta = '<p class="muted" style="margin:0 0 12px">Waiting for the driver’s live location…</p>';
  else eta = '<div class="skeleton" style="height:34px;width:50%;margin-bottom:12px"></div>';
  return html`
    <div class="status-head"><h2>${titles[r.status]}</h2></div>
    ${raw(eta)}${r.start_pin ? raw(html`<div class="start-pin"><span class="small muted">Tell your driver this PIN to start the trip</span>
      <span class="pin-digits num" aria-label="PIN ${r.start_pin.split('').join(' ')}">${r.start_pin}</span></div>`) : ''}
    ${raw(driverCard(r))}<hr class="divider">${raw(trip(r))}
    ${r.status !== 'IN_PROGRESS' ? raw('<hr class="divider"><button class="btn btn-ghost-danger btn-block" id="cancel-btn">Cancel ride</button>') : ''}`;
}

function finishedView(r) {
  if (r.status === 'COMPLETED') {
    const rated = r.my_rating != null;
    return html`
      <div class="status-head"><h2>You have arrived</h2>${raw(statusBadge(r.status))}</div>
      <div class="trip-summary"><span class="strong">${r.dropoff.name}</span><span class="fare num">${r.fare.display}</span>
        <span class="small muted">${r.payment?.status === 'PAID' ? 'Paid in cash' : 'Cash payment pending with driver'}</span></div>
      ${rated ? raw(html`<p class="muted" style="text-align:center">You rated this trip ${'★'.repeat(r.my_rating)}</p>`) : raw(html`
        <h3 style="text-align:center">How was your ride with ${r.driver?.name || 'your driver'}?</h3>
        <div class="stars" role="radiogroup" aria-label="Rating">${[1, 2, 3, 4, 5].map((n) => raw(`<button type="button" data-star="${n}" aria-label="${n} star${n > 1 ? 's' : ''}">★</button>`))}</div>
        <div class="field"><label for="rating-comment">Comments (optional)</label><input id="rating-comment" class="input" maxlength="500"></div>
        <button class="btn btn-outline btn-block" id="rate-btn" disabled>Submit rating</button>`)}
      <button class="btn btn-primary btn-block" style="margin-top:10px" id="done-btn">Book another ride</button>`;
  }
  const message = r.status === 'NO_DRIVER_AVAILABLE'
    ? 'No driver accepted within the search window. Rickshaws may be busy; please try again shortly.'
    : `Cancelled${r.cancelled_by ? ` by ${r.cancelled_by === 'admin' ? 'the transport office' : r.cancelled_by}` : ''}. ${r.cancel_reason || ''}`;
  return html`
    <div class="status-head"><h2>${r.status === 'NO_DRIVER_AVAILABLE' ? 'No rickshaw available' : 'Ride cancelled'}</h2>${raw(statusBadge(r.status))}</div>
    <p class="muted">${message}</p>${raw(trip(r))}
    ${r.cancellation_fee ? raw(html`<div class="banner banner-warn small" style="margin-top:10px">Cancellation fee ${r.cancellation_fee.display}: settle it with the transport office before your next ride.</div>`) : ''}
    <button class="btn btn-primary btn-block" style="margin-top:14px" id="retry-btn">${r.status === 'NO_DRIVER_AVAILABLE' ? 'Try again' : 'Book a new ride'}</button>
    <button class="btn btn-ghost btn-block" id="done-btn">Close</button>`;
}

function bindSheet() {
  const body = $('#sheet-body');
  body.querySelectorAll('[data-field]').forEach((el) => el.addEventListener('click', () => {
    S.field = el.dataset.field; S.searching = true; S.query = ''; render();
    $('#place-search')?.focus();
  }));
  const search = $('#place-search');
  if (search) {
    search.addEventListener('input', () => { S.query = search.value; const pos = search.selectionStart; render(); const s = $('#place-search'); s.focus(); s.setSelectionRange(pos, pos); });
    search.addEventListener('keydown', (e) => { if (e.key === 'Escape') { S.searching = false; render(); } });
  }
  body.querySelectorAll('[data-landmark]').forEach((el) => el.addEventListener('click', () => {
    const lm = S.campus.landmarks.find((l) => String(l.id) === el.dataset.landmark);
    setPoint(S.field, { lat: lm.lat, lng: lm.lng, name: lm.name });
  }));
  body.querySelectorAll('[data-place]').forEach((el) => el.addEventListener('click', () => {
    const p = S.places.find((x) => x.id === el.dataset.place);
    setPoint(S.field, { lat: p.lat, lng: p.lng, name: p.name });
  }));
  $('[data-use-location]', body)?.addEventListener('click', async (e) => {
    await busy(e.currentTarget, async () => {
      try {
        const pos = await currentPosition();
        if (pos.accuracy_m > 150) toast(`Your location is approximate (±${Math.round(pos.accuracy_m)} m). Drag the pin to adjust.`);
        setPoint('pickup', { lat: pos.lat, lng: pos.lng, name: nameFor(pos.lat, pos.lng, 'My location') });
        showMe(pos);
      } catch (err) { toast(err.message, 'error', 7000); }
    });
  });
  body.querySelectorAll('[data-count]').forEach((el) => el.addEventListener('click', () => {
    S.passengers = Math.min(S.config.fare.max_passengers, Math.max(1, S.passengers + Number(el.dataset.count)));
    S.requestKey = null; render();
  }));
  body.querySelectorAll('[data-boost]').forEach((el) => el.addEventListener('click', () => {
    S.boost = Number(el.dataset.boost); S.requestKey = null; render();
  }));
  body.querySelectorAll('[data-raise]').forEach((el) => el.addEventListener('click', () => boostRide(Number(el.dataset.raise), el)));
  $('#request-btn')?.addEventListener('click', (e) => requestRide(e.currentTarget));
  $('#cancel-btn')?.addEventListener('click', () => cancelRide());
  $('#done-btn')?.addEventListener('click', () => { S.ride = null; resetBooking(); });
  $('#retry-btn')?.addEventListener('click', () => {
    const r = S.ride; S.ride = null; resetBooking();
    setPoint('pickup', { ...r.pickup }, false); setPoint('dropoff', { ...r.dropoff });
  });
  let stars = 0;
  body.querySelectorAll('[data-star]').forEach((el) => el.addEventListener('click', () => {
    stars = Number(el.dataset.star);
    body.querySelectorAll('[data-star]').forEach((s) => s.classList.toggle('on', Number(s.dataset.star) <= stars));
    $('#rate-btn').disabled = false;
  }));
  $('#rate-btn')?.addEventListener('click', (e) => busy(e.currentTarget, async () => {
    try {
      await api.post(`/api/rides/${S.ride.id}/rating`, { stars, comment: $('#rating-comment').value || null });
      S.ride.my_rating = stars; toast('Thanks for the feedback!', 'success'); render();
    } catch (err) { showError(err); }
  }));
}

// ------------------------------------------------------------------ booking actions

function nameFor(lat, lng, fallback) {
  let best = null;
  for (const lm of S.campus.landmarks) {
    const d = haversine({ lat, lng }, lm);
    if (!best || d < best.d) best = { d, lm };
  }
  if (best && best.d < 40) return best.lm.name;
  if (best && best.d < 150) return `${fallback} near ${best.lm.name}`;
  return `${fallback} (${lat.toFixed(5)}, ${lng.toFixed(5)})`;
}

function onMapTap(latlng) {
  if (S.ride || !S.user) return;
  const field = S.pickup && !S.searching ? (S.dropoff ? S.field : 'dropoff') : S.field;
  setPoint(field, { lat: latlng.lat, lng: latlng.lng, name: nameFor(latlng.lat, latlng.lng, 'Pin') });
}

function setPoint(field, point, refresh = true) {
  S[field] = point;
  S.searching = false;
  S.query = '';
  S.field = field === 'pickup' && !S.dropoff ? 'dropoff' : field;
  S.preview = null;
  S.requestKey = null;
  if (refresh) {
    render();
    fitTo(S.map, [S.pickup, S.dropoff]);
    if (S.pickup && S.dropoff) loadPreview();
    if (field === 'pickup') refreshNearby();
  }
}

function resetBooking() {
  S.pickup = null; S.dropoff = null; S.preview = null; S.field = 'pickup'; S.searching = false; S.requestKey = null; S.boost = 0;
  render();
}

async function loadPreview() {
  const { pickup, dropoff } = S;
  try {
    const p = await api.get(`/api/routes/preview?from_lat=${pickup.lat}&from_lng=${pickup.lng}&to_lat=${dropoff.lat}&to_lng=${dropoff.lng}`);
    if (S.pickup === pickup && S.dropoff === dropoff) { S.preview = p; render(); }
  } catch (err) {
    if (err.status !== 429) showError(err);
  }
}

async function refreshNearby() {
  if (!S.user || S.ride || !S.pickup) return;
  try {
    S.nearby = await api.get(`/api/passenger/nearby-drivers?lat=${S.pickup.lat}&lng=${S.pickup.lng}`);
    if (!S.ride) { render(); }
  } catch { S.nearby = null; }
}

async function requestRide(button) {
  S.requestKey = S.requestKey || newIdempotencyKey(); // same key on retry => no duplicate booking
  await busy(button, async () => {
    try {
      const ride = await api.post('/api/passenger/rides', { pickup: S.pickup, dropoff: S.dropoff, passenger_count: S.passengers, boost_paise: S.boost },
        { headers: { 'Idempotency-Key': S.requestKey } });
      S.requestKey = null;
      applyRide(ride);
    } catch (err) {
      if (err.code === 'active_ride_exists') {
        const state = await api.get('/api/passenger/state');
        applyRide(state.active_ride);
      } else if (err.code === 'fee_outstanding') {
        S.fees = err.extra.fees || []; render(); showError(err);
      } else showError(err);
    }
  });
}

async function boostRide(boost, button) {
  const r = S.ride;
  const total = (r.fare_breakdown.base_per_passenger.paise + boost) * r.passenger_count;
  const ok = await modal({ title: `Boost to +${rupees(boost)} per person?`,
    body: html`Your fare becomes <strong>${rupees(total)}</strong>. Drivers who passed on your request will see it again.`,
    confirmText: 'Boost fare', cancelText: 'Not now' });
  if (!ok) return;
  await busy(button, async () => {
    try { applyRide(await api.post(`/api/passenger/rides/${r.id}/boost`, { boost_paise: boost })); toast('Fare boosted', 'success'); }
    catch (err) { showError(err); }
  });
}

async function refreshFees() {
  try { S.fees = (await api.get('/api/passenger/state')).outstanding_fees || []; render(); } catch { /* next sync will catch up */ }
}

async function cancelRide(quote = null) {
  const r = S.ride;
  if (!quote && r.driver) {
    try { quote = await api.get(`/api/passenger/rides/${r.id}/cancel-quote`); } catch (err) { showError(err); return; }
  }
  const fee = quote?.fee;
  const body = fee
    ? html`<strong>Cancelling now costs ${fee.display}.</strong> ${quote.reason} You will need to settle the fee with the transport office before your next ride.`
    : r.driver ? html`Free cancellation. ${quote?.reason || ''}` : 'We will stop looking for a driver.';
  const reason = await modal({
    title: 'Cancel this ride?', body,
    confirmText: fee ? `Cancel and pay ${fee.display}` : 'Cancel ride', cancelText: 'Keep ride', danger: true,
    input: { label: 'Reason', options: CANCEL_REASONS },
  });
  if (reason === null) return;
  try {
    applyRide(await api.post(`/api/passenger/rides/${r.id}/cancel`, { reason, accepted_fee_paise: fee?.paise || 0 }));
    if (fee) refreshFees();
  } catch (err) {
    if (err.code === 'fee_changed') return cancelRide(err.extra.quote); // the driver started approaching; confirm the new fee
    showError(err);
  }
}

// ------------------------------------------------------------------ map layers

function setMarker(key, latlng, icon, opts = {}) {
  if (!S.map) return null;
  if (!latlng) { if (S.layers[key]) { S.layers[key].remove(); delete S.layers[key]; } return null; }
  if (!S.layers[key]) {
    S.layers[key] = L.marker([latlng.lat, latlng.lng], { icon, ...opts }).addTo(S.map);
  } else {
    S.layers[key].setLatLng([latlng.lat, latlng.lng]);
    S.layers[key].setIcon(icon);
  }
  return S.layers[key];
}

function setLine(key, coords, style) {
  if (!S.map) return;
  if (S.layers[key]) { S.layers[key].remove(); delete S.layers[key]; }
  if (coords && coords.length > 1) S.layers[key] = L.polyline(coords, style).addTo(S.map);
}

function drawRideLayers() {
  if (!S.map) return;
  const r = S.ride;
  const pickup = r ? r.pickup : S.pickup;
  const dropoff = r ? r.dropoff : S.dropoff;
  const pm = setMarker('pickup', pickup, S.I.pickup, { draggable: !r, title: 'Pickup' });
  const dm = setMarker('dropoff', dropoff, S.I.dropoff, { draggable: !r, title: 'Drop-off' });
  [pm, dm].forEach((m) => m?.dragging && (r ? m.dragging.disable() : m.dragging.enable()));
  if (!r) {
    pm?.off('dragend').on('dragend', (e) => { const ll = e.target.getLatLng(); setPoint('pickup', { lat: ll.lat, lng: ll.lng, name: nameFor(ll.lat, ll.lng, 'Pin') }); });
    dm?.off('dragend').on('dragend', (e) => { const ll = e.target.getLatLng(); setPoint('dropoff', { lat: ll.lat, lng: ll.lng, name: nameFor(ll.lat, ll.lng, 'Pin') }); });
  }
  // Trip line: road geometry when known, dashed when it is only an approximation.
  const preview = !r ? S.preview : null;
  if (preview) setLine('trip', preview.geometry, { color: '#2e7d32', weight: 5, opacity: 0.8, dashArray: preview.approximate ? '8 8' : null });
  else if (!r) setLine('trip', null);
  const loc = r?.driver_location;
  const assigned = r && ['ACCEPTED', 'ARRIVED', 'IN_PROGRESS'].includes(r.status);
  if (assigned && loc) {
    const stale = secondsSince(loc.received_at) > S.config.location_stale_s;
    setMarker('driver', loc, S.I.vehicle(loc.source === 'simulator' ? 'sim' : stale ? 'stale' : ''), { title: 'Your rickshaw', zIndexOffset: 1000 });
  } else setMarker('driver', null);
  if (assigned && S.driverRoute) setLine('trip', S.driverRoute.geometry, { color: '#1565c0', weight: 5, opacity: 0.85, dashArray: S.driverRoute.approximate ? '8 8' : null });
  else if (r && !assigned) setLine('trip', null);
  // Available drivers (approximate positions) only while booking.
  if (S.layers.available) { S.layers.available.remove(); delete S.layers.available; }
  if (!r && S.nearby?.drivers?.length) {
    S.layers.available = L.layerGroup(S.nearby.drivers.map((d) => L.marker([d.lat, d.lng], { icon: S.I.available, interactive: false, keyboard: false }))).addTo(S.map);
  }
}

function fitRide() {
  const r = S.ride;
  if (!r || !S.map) return;
  fitTo(S.map, [r.pickup, ['ACCEPTED', 'ARRIVED'].includes(r.status) ? r.driver_location : null, r.status === 'IN_PROGRESS' ? r.dropoff : null, r.status === 'IN_PROGRESS' ? r.driver_location : null]);
}

function updateFreshness() {
  const el = $('[data-freshness]');
  const r = S.ride;
  if (!el || !r) return;
  const loc = r.driver_location;
  if (!loc) { el.innerHTML = '<span class="badge">Live location not shared yet</span>'; return; }
  const age = secondsSince(loc.received_at);
  const stale = age > S.config.location_stale_s;
  el.innerHTML = html`
    ${loc.source === 'simulator' ? raw('<span class="badge badge-sim" title="Test mode: this position comes from a simulator, not real GPS">SIMULATED LOCATION</span>') : ''}
    <span class="badge ${stale ? 'badge-warn' : 'badge-ok'}">${stale ? 'Location delayed' : 'Live'}</span>
    <span class="muted">Updated ${ago(loc.received_at)} · ±${Math.round(loc.accuracy_m)} m</span>`;
}

function tick() {
  updateFreshness();
  const el = $('[data-search-left]');
  if (el && S.ride) {
    const left = Math.max(0, Math.round((new Date(S.ride.search_expires_at) - Date.now()) / 1000));
    el.textContent = left > 0 ? `Searching for up to ${Math.ceil(left / 60)} more min` : 'Finishing search…';
  }
}

// ------------------------------------------------------------------ misc

function notice(text, kind = 'info') {
  const el = $('#map-notice');
  el.className = `map-notice banner banner-${kind}`;
  el.textContent = text;
  el.hidden = false;
}

async function locateMe(e) {
  await busy(e.currentTarget, async () => {
    try {
      const pos = await currentPosition();
      showMe(pos);
      S.map?.setView([pos.lat, pos.lng], 17);
      if (!S.ride && !S.pickup) setPoint('pickup', { lat: pos.lat, lng: pos.lng, name: nameFor(pos.lat, pos.lng, 'My location') });
    } catch (err) { toast(err.message, 'error', 7000); }
  });
}

function showMe(pos) {
  if (!S.map) return;
  setMarker('me', pos, S.I.me, { interactive: false, keyboard: false });
  if (S.layers.meAcc) S.layers.meAcc.remove();
  S.layers.meAcc = L.circle([pos.lat, pos.lng], { radius: pos.accuracy_m, color: '#1a73e8', weight: 1, fillOpacity: 0.08, interactive: false }).addTo(S.map);
}

function haversine(a, b) {
  const R = 6371008.8, toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat), dLng = toRad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function openMenu() {
  const d = drawer('Menu', html`
    <div class="card"><div class="strong">${S.user.name || 'Passenger'}</div><div class="small muted">${S.user.phone}</div></div>
    <button class="list-item" data-go="history">\u{1F9FE} <span>Ride history</span></button>
    <button class="list-item" data-go="places">★ <span>Saved places</span></button>
    <button class="list-item" data-go="profile">\u{1F464} <span>Edit name</span></button>
    <button class="list-item" data-go="logout">↪ <span>Sign out</span></button>
    <p class="small muted">Map data © OpenStreetMap contributors. Campus places are OpenStreetMap features and may not match official stops.</p>`);
  d.body.addEventListener('click', async (e) => {
    const go = e.target.closest('[data-go]')?.dataset.go;
    if (!go) return;
    if (go === 'logout') return logout(api, rt);
    d.close();
    if (go === 'history') openHistory();
    if (go === 'places') openPlaces();
    if (go === 'profile') {
      const name = await modal({ title: 'Your name', confirmText: 'Save', input: { label: 'Name shown to drivers', placeholder: S.user.name || '' } });
      if (name) { try { S.user = await api.patch('/api/auth/me', { name }); toast('Name updated', 'success'); } catch (err) { showError(err); } }
    }
  });
}

async function openHistory() {
  const d = drawer('Ride history', '<div class="skeleton" style="height:64px"></div><div class="skeleton" style="height:64px"></div>');
  try {
    const rides = await api.get('/api/passenger/rides?limit=50');
    d.body.innerHTML = rides.length ? rides.map((r) => html`<button class="list-item" data-ride="${r.id}">
        <div style="flex:1;min-width:0"><div class="strong" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${r.pickup.name} → ${r.dropoff.name}</div>
        <div class="small muted">${dateTime(r.created_at)} · ${r.fare.display}</div></div>${raw(statusBadge(r.status))}</button>`).join('')
      : '<div class="empty"><div class="icon">\u{1F6FA}</div>No rides yet. Your trips will appear here.</div>';
    d.body.addEventListener('click', async (e) => {
      const id = e.target.closest('[data-ride]')?.dataset.ride;
      if (!id) return;
      const r = await api.get(`/api/passenger/rides/${id}`);
      d.body.innerHTML = html`<button class="btn btn-ghost btn-sm" data-back>← All rides</button>
        <div class="card stack"><div class="row-between"><strong>${dateTime(r.created_at)}</strong>${raw(statusBadge(r.status))}</div>${raw(trip(r))}
        ${r.driver ? raw(html`<div class="small">Driver: <strong>${r.driver.name}</strong> · ${r.driver.vehicle_number || ''}</div>`) : ''}
        ${r.payment ? raw(html`<div class="small">Payment: ${r.payment.status === 'PAID' ? 'Paid (cash)' : 'Pending'}</div>`) : ''}
        ${r.cancel_reason ? raw(html`<div class="small">Reason: ${r.cancel_reason}</div>`) : ''}</div>
        <div class="card"><h3>Timeline</h3><ul class="timeline">${r.timeline.map((t) => raw(html`<li><span><strong>${t.to.replace(/_/g, ' ').toLowerCase()}</strong>
          <span class="small muted"> · ${dateTime(t.at)}${t.note ? ` · ${t.note}` : ''}</span></span></li>`))}</ul></div>`;
      d.body.querySelector('[data-back]').addEventListener('click', () => { d.close(); openHistory(); });
    }, { once: true });
  } catch (err) {
    d.body.innerHTML = html`<div class="empty">${err.message}</div>`;
  }
}

async function openPlaces() {
  const d = drawer('Saved places', '');
  const renderPlaces = () => {
    d.body.innerHTML = html`<p class="small muted">Save up to 10 frequent places. Your current drop-off can be saved below.</p>
      ${S.places.length ? raw(S.places.map((p) => html`<div class="list-item" style="cursor:default"><div style="flex:1"><div class="strong">★ ${p.label}</div>
        <div class="small muted">${p.name}</div></div><button class="btn btn-ghost btn-sm" data-del="${p.id}">Remove</button></div>`).join(''))
        : raw('<div class="empty">No saved places yet.</div>')}
      ${S.dropoff || S.pickup ? raw(html`<button class="btn btn-outline btn-block" data-save>Save “${(S.dropoff || S.pickup).name}”</button>`) : ''}`;
  };
  renderPlaces();
  d.body.addEventListener('click', async (e) => {
    const del = e.target.closest('[data-del]')?.dataset.del;
    try {
      if (del) {
        await api.del(`/api/passenger/places/${del}`);
        S.places = S.places.filter((p) => p.id !== del);
        renderPlaces();
      } else if (e.target.closest('[data-save]')) {
        const point = S.dropoff || S.pickup;
        const label = await modal({ title: 'Save place', confirmText: 'Save', input: { label: 'Label (e.g. Hostel, Lab)', placeholder: 'Hostel' } });
        if (!label) return;
        const saved = await api.post('/api/passenger/places', { label, name: point.name, lat: point.lat, lng: point.lng });
        S.places = [...S.places.filter((p) => p.label !== saved.label), saved];
        renderPlaces();
        toast('Place saved', 'success');
      }
    } catch (err) { showError(err); }
  });
}

boot();
// Installable app (home screen / Android TWA); sw.js caches nothing.
navigator.serviceWorker?.register('sw.js').catch(() => {});
