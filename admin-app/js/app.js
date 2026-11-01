import { ApiClient } from '/shared/js/api.js';
import { showLogin, logout } from '/shared/js/auth.js';
import { createMap, drawCampus } from '/shared/js/map.js';
import { themeToggle } from '/shared/js/theme.js';
import { $, $$, busy, dateTime, distance, drawer, html, modal, raw, rupees, showError, statusBadge, toast } from '/shared/js/ui.js';

const api = new ApiClient('campusrick.admin.token');
const S = { user: null, tab: 'overview', timer: null, config: null };

const SETTING_LABELS = {
  fare_paise: ['Base fare per passenger (paise)', '1000 paise = ₹10. A ride costs (base + boost) × passengers. Applies to new requests only.'],
  max_boost_paise: ['Max boost per passenger (paise)', 'Highest extra a passenger can offer per person to attract a driver at peak times.'],
  max_boosts_per_ride: ['Boosts per ride', 'How many times a searching passenger can raise their boost.'],
  cancel_fee_percent: ['Cancellation fee (%)', 'Share of the fare charged when the passenger cancels while the driver is approaching.'],
  cancel_fee_min_paise: ['Minimum cancellation fee (paise)', 'The fee is never lower than this (capped at the fare).'],
  cancel_window_s: ['Approach window (s)', 'Cancellation is free if the driver has not moved closer to the pickup in this time.'],
  demand_window_min: ['Demand map window (min)', 'Drivers see where passengers booked within this many recent minutes.'],
  approach_min_m: ['Approach threshold (m)', 'Net metres closer within the window that count as approaching (filters GPS jitter).'],
  driver_share_percent: ['Driver share (%)', 'Share of each fare credited to the driver in earnings.'],
  dispatch_radius_m: ['Dispatch radius (m)', 'Maximum straight-line distance from a driver to the pickup.'],
  offer_timeout_s: ['Offer timeout (s)', 'Time a driver has to accept before the next driver is asked.'],
  max_offers_per_ride: ['Max offers per ride', 'After this many drivers, the ride ends as "no driver available".'],
  search_timeout_s: ['Search window (s)', 'How long a request keeps looking for a driver.'],
  location_stale_s: ['Location freshness (s)', 'Drivers whose last position is older than this are not offered rides.'],
  arrival_radius_m: ['Arrival radius (m)', 'How close to the pickup a driver must be to mark arrival.'],
  no_show_wait_s: ['No-show wait (s)', 'Minimum wait at pickup before a driver can report a no-show.'],
  require_driver_verification: ['Require driver approval', 'Unapproved drivers cannot go on duty.'],
};

async function boot() {
  $('#theme-slot').replaceWith(themeToggle('btn btn-outline btn-sm'));
  api.onUnauthorized = () => { api.token = null; location.reload(); };
  if (!api.token) return askLogin();
  try {
    const me = await api.get('/api/auth/me');
    if (me.role !== 'admin') { api.token = null; return askLogin(); }
    start(me);
  } catch { api.token = null; askLogin(); }
}

function askLogin() {
  showLogin({ api, app: 'admin', title: 'Administrator sign in', subtitle: 'Transport office staff only. Accounts are created with the management command.', onSuccess: start });
}

async function start(user) {
  S.user = user;
  S.config = await api.get('/api/config', { auth: false });
  $('#admin').hidden = false;
  $('#who').textContent = `${user.name || 'Admin'} · ${user.phone}`;
  $('#logout-btn').addEventListener('click', () => logout(api));
  $$('.tabs [data-tab]').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  show(location.hash.slice(1) || 'overview');
}

const VIEWS = { overview, rides, fees, drivers, users, campus, settings, reports, audit };

function show(tab) {
  if (!VIEWS[tab]) tab = 'overview';
  S.tab = tab;
  history.replaceState(null, '', `#${tab}`);
  $$('.tabs [data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  clearInterval(S.timer);
  $('#view').innerHTML = '<div class="skeleton" style="height:28px;width:240px"></div><div class="skeleton" style="height:120px"></div>';
  VIEWS[tab]().catch((err) => { $('#view').innerHTML = html`<div class="banner banner-danger">${err.message}</div>`; });
}

function refreshEvery(ms, fn) {
  clearInterval(S.timer);
  const tab = S.tab;
  S.timer = setInterval(() => { if (S.tab === tab && document.visibilityState === 'visible') fn().catch(() => {}); }, ms);
}

const tile = (label, value) => html`<div class="tile"><div class="value">${value}</div><div class="label">${label}</div></div>`;
const table = (head, rows, empty = 'Nothing to show.') => rows.length
  ? html`<div class="table-wrap"><table><thead><tr>${head.map((h) => raw(html`<th>${h}</th>`))}</tr></thead><tbody>${raw(rows.join(''))}</tbody></table></div>`
  : html`<div class="card empty">${empty}</div>`;

// ---------------------------------------------------------------- overview

async function overview() {
  const load = async () => {
    const [o, active] = await Promise.all([api.get('/api/admin/overview'), api.get('/api/admin/rides?active_only=true')]);
    const a = o.active_rides, d = o.last_24h;
    $('#view').innerHTML = html`
      <div class="row-between"><h2>Live operations</h2><span class="small muted">Refreshes every 10 s</span></div>
      <div class="tiles">
        ${raw(tile('Searching', (a.REQUESTED || 0) + (a.OFFERED || 0)))}${raw(tile('Driver assigned', (a.ACCEPTED || 0) + (a.ARRIVED || 0)))}
        ${raw(tile('On trip', a.IN_PROGRESS || 0))}${raw(tile('Drivers on duty', o.drivers_online))}
        ${raw(tile('Drivers with fresh GPS', o.drivers_with_fresh_location))}${raw(tile('Awaiting approval', o.drivers_pending_verification))}
      </div>
      <h2>Last 24 hours</h2>
      <div class="tiles">${raw(tile('Completed', d.COMPLETED || 0))}${raw(tile('Cancelled', d.CANCELLED || 0))}${raw(tile('No driver found', d.NO_DRIVER_AVAILABLE || 0))}</div>
      <h2>Active rides</h2>${raw(rideTable(active, 'No active rides right now.'))}`;
    bindRideRows();
  };
  await load();
  refreshEvery(10000, load);
}

function rideTable(list, empty) {
  return table(['Requested', 'Passenger', 'Driver', 'Route', 'Status', 'Fare'], list.map((r) => html`
    <tr class="clickable" data-ride="${r.id}"><td>${dateTime(r.created_at)}</td><td>${r.passenger?.name || ''}</td>
    <td>${r.driver?.name || '—'}${r.driver?.vehicle_number ? raw(html`<br><span class="small muted">${r.driver.vehicle_number}</span>`) : ''}</td>
    <td>${r.pickup.name} → ${r.dropoff.name}</td><td>${raw(statusBadge(r.status))}</td><td>${r.fare.display}</td></tr>`), empty);
}

function bindRideRows() {
  $$('#view [data-ride]').forEach((row) => row.addEventListener('click', () => openRide(row.dataset.ride)));
}

async function openRide(id) {
  const d = drawer('Ride details', '<div class="skeleton" style="height:120px"></div>');
  const r = await api.get(`/api/admin/rides/${id}`);
  const active = !['COMPLETED', 'CANCELLED', 'NO_DRIVER_AVAILABLE'].includes(r.status);
  d.body.innerHTML = html`
    <div class="card stack"><div class="row-between"><strong>${r.pickup.name} → ${r.dropoff.name}</strong>${raw(statusBadge(r.status))}</div>
      <div class="small">Passenger: <strong>${r.passenger?.name || ''}</strong> · ${r.passenger?.phone || ''}</div>
      <div class="small">Driver: <strong>${r.driver?.name || '—'}</strong> ${r.driver ? `· ${r.driver.phone} · ${r.driver.vehicle_number || ''}` : ''}</div>
      <div class="small">Fare ${r.fare.display} = (${r.fare_breakdown.base_per_passenger.display} + ${r.fare_breakdown.boost_per_passenger.display} boost) × ${r.passenger_count} passenger${r.passenger_count > 1 ? 's' : ''}
        · ${distance(r.route.distance_m)} ${r.route.approximate ? '(straight-line estimate)' : '(road route)'}</div>
      ${r.cancellation_fee ? raw(html`<div class="small">Cancellation fee: <strong>${r.cancellation_fee.display}</strong> (see Fees)</div>`) : ''}
      ${r.cancel_reason ? raw(html`<div class="small">Cancelled by ${r.cancelled_by}: ${r.cancel_reason}</div>`) : ''}
      ${r.payment ? raw(html`<div class="small">Payment: ${r.payment.status} (${r.payment.method})</div>`) : ''}</div>
    <div class="card"><h3>Status history</h3><ul class="timeline">${r.timeline.map((t) => raw(html`<li><span><strong>${t.to}</strong>
      <span class="small muted"> by ${t.by} · ${dateTime(t.at)}${t.note ? ` · ${t.note}` : ''}</span></span></li>`))}</ul></div>
    <div class="card"><h3>Dispatch offers</h3>${r.offers.length ? raw(r.offers.map((o) => html`<div class="small">${o.driver}: <strong>${o.status}</strong>
      · ${distance(o.distance_to_pickup_m)} away · ${dateTime(o.created_at)}</div>`).join('')) : raw('<div class="small muted">No offers made.</div>')}</div>
    ${active ? raw('<button class="btn btn-danger" data-cancel>Cancel this ride</button>') : ''}`;
  d.body.querySelector('[data-cancel]')?.addEventListener('click', async () => {
    const reason = await modal({ title: 'Cancel ride', body: 'The passenger and driver are notified immediately.', danger: true, confirmText: 'Cancel ride', input: { label: 'Reason (recorded in the audit log)' } });
    if (!reason) return;
    try { await api.post(`/api/admin/rides/${id}/cancel`, { reason }); toast('Ride cancelled', 'success'); d.close(); show(S.tab); } catch (err) { showError(err); }
  });
}

// ---------------------------------------------------------------- fees

async function fees(status = 'PENDING') {
  const list = await api.get(`/api/admin/fees?status=${status}`);
  $('#view').innerHTML = html`<h2>Cancellation fees</h2>
    <p class="muted small" style="margin:0">Charged when a passenger cancels while the driver is approaching. A passenger with an unpaid fee cannot book.
      Mark a fee paid once the cash is received; it is credited to that driver's earnings.</p>
    <div class="row" style="margin:10px 0">${['PENDING', 'PAID', 'WAIVED'].map((s) => raw(html`<button class="btn btn-sm ${s === status ? 'btn-primary' : 'btn-outline'}" data-fee-filter="${s}">${s.toLowerCase()}</button>`))}</div>
    ${raw(table(['Cancelled', 'Passenger', 'Driver', 'Pickup', 'Fee', ''], list.map((f) => html`<tr>
      <td>${dateTime(f.cancelled_at)}</td><td>${f.passenger.name || ''}<br><span class="small muted">${f.passenger.phone}</span></td>
      <td>${f.driver || '—'}</td><td>${f.pickup}</td><td class="num">${f.amount.display}</td>
      <td>${f.status === 'PENDING' ? raw(html`<button class="btn btn-sm btn-primary" data-settle="${f.payment_id}" data-to="PAID">Mark paid</button>
        <button class="btn btn-sm btn-outline" data-settle="${f.payment_id}" data-to="WAIVED">Waive</button>`) : f.status.toLowerCase()}</td></tr>`),
      status === 'PENDING' ? 'No unpaid fees.' : 'Nothing here yet.'))}`;
  $$('#view [data-fee-filter]').forEach((b) => b.addEventListener('click', () => fees(b.dataset.feeFilter)));
  $$('#view [data-settle]').forEach((b) => b.addEventListener('click', async () => {
    const waive = b.dataset.to === 'WAIVED';
    const note = await modal({ title: waive ? 'Waive this fee?' : 'Mark fee as paid?', confirmText: waive ? 'Waive fee' : 'Mark paid',
      body: 'The passenger can book again. Recorded in the audit log.', input: { label: 'Note (optional)' } });
    if (note === null) return;
    await busy(b, async () => {
      try { await api.post(`/api/admin/fees/${b.dataset.settle}`, { status: b.dataset.to, note: note || null }); toast('Fee updated', 'success'); fees(status); }
      catch (err) { showError(err); }
    });
  }));
}

// ---------------------------------------------------------------- rides

async function rides() {
  const statuses = ['', 'REQUESTED', 'OFFERED', 'ACCEPTED', 'ARRIVED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'NO_DRIVER_AVAILABLE'];
  $('#view').innerHTML = html`<div class="toolbar"><h2 style="flex:1">Rides</h2>
    <label class="sr-only" for="ride-status">Status</label><select id="ride-status" class="input">${statuses.map((s) => raw(html`<option value="${s}">${s ? s.replace(/_/g, ' ').toLowerCase() : 'All statuses'}</option>`))}</select></div>
    <div id="ride-list"></div>`;
  const load = async () => {
    const status = $('#ride-status').value;
    const list = await api.get(`/api/admin/rides?limit=100${status ? `&status=${status}` : ''}`);
    $('#ride-list').innerHTML = rideTable(list, 'No rides match this filter.');
    bindRideRows();
  };
  $('#ride-status').addEventListener('change', load);
  await load();
}

// ---------------------------------------------------------------- drivers

async function drivers() {
  const list = await api.get('/api/admin/drivers');
  const badge = (v) => html`<span class="badge ${{ approved: 'badge-ok', pending: 'badge-warn' }[v] || 'badge-danger'}">${v}</span>`;
  $('#view').innerHTML = html`<h2>Drivers</h2>
    <p class="muted small" style="margin:0">Approve drivers after checking their licence and vehicle. Suspending a driver takes them off duty immediately.</p>
    ${raw(table(['Driver', 'Vehicle', 'Verification', 'Duty / GPS', 'Trips', 'Rating', 'Actions'], list.map((d) => html`<tr>
      <td><strong>${d.name || 'Unnamed'}</strong><br><span class="small muted">${d.phone}${d.account_status !== 'active' ? ' · account suspended' : ''}</span></td>
      <td>${d.vehicle_number || '—'}</td><td>${raw(badge(d.verification_status))}${d.verification_note ? raw(html`<br><span class="small muted">${d.verification_note}</span>`) : ''}</td>
      <td>${d.is_online ? raw('<span class="badge badge-ok">On duty</span>') : raw('<span class="badge">Off duty</span>')}<br>
        <span class="small muted">${d.location_age_s == null ? 'no location' : `${d.location_fresh ? 'fresh' : 'stale'} · ${d.location_age_s}s ago${d.location_source === 'simulator' ? ' · SIMULATED' : ''}`}</span></td>
      <td class="num">${d.completed_rides}</td><td>${d.rating ? `★ ${d.rating} (${d.rating_count})` : '—'}</td>
      <td><div class="actions">
        ${d.verification_status !== 'approved' ? raw(html`<button class="btn btn-sm btn-primary" data-verify="approved" data-id="${d.id}">Approve</button>`) : ''}
        ${d.verification_status === 'pending' ? raw(html`<button class="btn btn-sm btn-outline" data-verify="rejected" data-id="${d.id}">Reject</button>`) : ''}
        ${d.verification_status === 'approved' ? raw(html`<button class="btn btn-sm btn-ghost-danger" data-verify="suspended" data-id="${d.id}">Suspend</button>`) : ''}
      </div></td></tr>`), 'No drivers have signed up yet.'))}`;
  $$('#view [data-verify]').forEach((b) => b.addEventListener('click', async () => {
    const status = b.dataset.verify;
    const verb = { approved: 'Approve', rejected: 'Reject', suspended: 'Suspend' }[status];
    const note = await modal({
      title: `${verb} driver?`, confirmText: verb, danger: status !== 'approved',
      body: status === 'approved' ? 'The driver will be able to go on duty and receive rides.'
        : status === 'suspended' ? 'The driver is taken off duty immediately and cannot receive rides.'
          : 'The driver will not be able to go on duty.',
      input: { label: 'Note for the driver (optional)', placeholder: status === 'approved' ? 'e.g. Licence and vehicle checked' : 'Reason' },
    });
    if (note === null) return;
    try { await api.post(`/api/admin/drivers/${b.dataset.id}/verification`, { status, note: note || null }); toast('Driver updated', 'success'); drivers(); } catch (err) { showError(err); }
  }));
}

// ---------------------------------------------------------------- accounts

async function users() {
  $('#view').innerHTML = html`<div class="toolbar"><h2 style="flex:1">Accounts</h2>
    <input id="u-q" class="input" placeholder="Search name or phone" maxlength="40">
    <select id="u-role" class="input"><option value="">All roles</option><option>passenger</option><option>driver</option><option>admin</option></select></div><div id="u-list"></div>`;
  const load = async () => {
    const q = $('#u-q').value.trim(), role = $('#u-role').value;
    const list = await api.get(`/api/admin/users?${new URLSearchParams({ ...(q && { q }), ...(role && { role }) })}`);
    $('#u-list').innerHTML = table(['Name', 'Phone', 'Role', 'Status', 'Joined', ''], list.map((u) => html`<tr>
      <td>${u.name || '—'}</td><td>${u.phone}</td><td>${u.role}</td>
      <td><span class="badge ${u.status === 'active' ? 'badge-ok' : 'badge-danger'}">${u.status}</span></td><td>${dateTime(u.joined_at)}</td>
      <td>${u.id === S.user.id ? '' : raw(html`<button class="btn btn-sm ${u.status === 'active' ? 'btn-ghost-danger' : 'btn-outline'}" data-user="${u.id}" data-to="${u.status === 'active' ? 'suspended' : 'active'}">
        ${u.status === 'active' ? 'Suspend' : 'Reactivate'}</button>`)}</td></tr>`), 'No accounts match.');
    $$('#u-list [data-user]').forEach((b) => b.addEventListener('click', async () => {
      const reason = await modal({ title: b.dataset.to === 'suspended' ? 'Suspend account' : 'Reactivate account', danger: b.dataset.to === 'suspended',
        body: b.dataset.to === 'suspended' ? 'They are signed out everywhere and any active request is cancelled.' : '', input: { label: 'Reason (audit log)' } });
      if (reason === null) return;
      try { await api.post(`/api/admin/users/${b.dataset.user}/status`, { status: b.dataset.to, reason }); toast('Account updated', 'success'); load(); } catch (err) { showError(err); }
    }));
  };
  let t;
  $('#u-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 300); });
  $('#u-role').addEventListener('change', load);
  await load();
}

// ---------------------------------------------------------------- campus

async function campus() {
  const [lms, campusData] = await Promise.all([api.get('/api/admin/landmarks'), api.get('/api/campus', { auth: false })]);
  $('#view').innerHTML = html`<div class="row-between"><h2>Campus geography</h2><button class="btn btn-outline btn-sm" id="add-lm">Add landmark</button></div>
    <div class="banner banner-info small">${campusData.data_source}<br>${campusData.verification}</div>
    <div id="campus-map"></div>
    ${raw(table(['Name', 'Category', 'Pickup point', 'Active', 'Source'], lms.map((l) => html`<tr>
      <td>${l.name}</td><td>${l.category}</td>
      <td><input type="checkbox" aria-label="Pickup point" data-lm="${l.id}" data-field="is_pickup_point" ${l.is_pickup_point ? 'checked' : ''}></td>
      <td><input type="checkbox" aria-label="Active" data-lm="${l.id}" data-field="is_active" ${l.is_active ? 'checked' : ''}></td>
      <td class="small muted">${l.osm_ref ? raw(html`<code>${l.osm_ref}</code>`) : l.source}</td></tr>`)))}`;
  const map = createMap($('#campus-map'), S.config);
  if (map) drawCampus(map, campusData);
  let adding = false;
  $('#add-lm').addEventListener('click', () => { adding = true; toast('Click the map where the landmark is.'); });
  map?.on('click', async (e) => {
    if (!adding) return;
    adding = false;
    const name = await modal({ title: 'New landmark', confirmText: 'Next', input: { label: 'Name' } });
    if (!name) return;
    const category = await modal({ title: 'Category', confirmText: 'Add', input: { label: 'Category', options: ['academic', 'hostel', 'gate', 'landmark', 'services', 'sports', 'health'] } });
    if (!category) return;
    try {
      await api.post('/api/admin/landmarks', { name, category, lat: e.latlng.lat, lng: e.latlng.lng, is_pickup_point: false });
      toast('Landmark added', 'success'); campus();
    } catch (err) { showError(err); }
  });
  $$('#view [data-lm]').forEach((box) => box.addEventListener('change', async () => {
    try { await api.patch(`/api/admin/landmarks/${box.dataset.lm}`, { [box.dataset.field]: box.checked }); toast('Saved', 'success'); }
    catch (err) { box.checked = !box.checked; showError(err); }
  }));
}

// ---------------------------------------------------------------- settings

async function settings() {
  const { values, limits } = await api.get('/api/admin/settings');
  $('#view').innerHTML = html`<h2>Fares &amp; dispatch</h2>
    <p class="muted small" style="margin:0">Changes apply to new requests immediately and are recorded in the audit log. Fares are stored in paise to avoid rounding errors.</p>
    <form class="card" id="settings-form"><div class="settings-grid">
      ${Object.entries(SETTING_LABELS).map(([key, [label, help]]) => raw(typeof values[key] === 'boolean'
        ? html`<div class="field"><label><input type="checkbox" name="${key}" ${values[key] ? 'checked' : ''}> ${label}</label><span class="hint">${help}</span></div>`
        : html`<div class="field"><label for="s-${key}">${label}</label><input id="s-${key}" class="input" type="number" name="${key}" value="${values[key]}"
            min="${limits[key]?.[0] ?? 0}" max="${limits[key]?.[1] ?? ''}" step="1" required><span class="hint">${help}${key.endsWith('_paise') ? ` Currently ${rupees(values[key])}.` : ''}</span></div>`))}
    </div><button class="btn btn-primary" type="submit">Save changes</button></form>`;
  $('#settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const changes = {};
    for (const [key, original] of Object.entries(values)) {
      const input = e.target.elements[key];
      if (!input) continue;
      const value = input.type === 'checkbox' ? input.checked : Number(input.value);
      if (value !== original) changes[key] = value;
    }
    if (!Object.keys(changes).length) { toast('No changes to save.'); return; }
    await busy(e.submitter, async () => {
      try { await api.put('/api/admin/settings', changes); toast('Settings saved', 'success'); settings(); } catch (err) { showError(err); }
    });
  });
}

// ---------------------------------------------------------------- reports

async function reports() {
  $('#view').innerHTML = html`<div class="toolbar"><h2 style="flex:1">Reports</h2><select id="r-days" class="input">
    <option value="7">Last 7 days</option><option value="14" selected>Last 14 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></div><div id="r-body"></div>`;
  const load = async () => {
    const r = await api.get(`/api/admin/reports?days=${$('#r-days').value}`);
    const max = Math.max(1, ...r.daily.map((d) => d.requested));
    const secs = (s) => (s == null ? '—' : s < 90 ? `${s} s` : `${Math.round(s / 60)} min`);
    $('#r-body').innerHTML = html`
      <div class="tiles">${raw(tile('Avg wait to driver acceptance', secs(r.avg_wait_to_accept_s)))}${raw(tile('Avg trip duration', secs(r.avg_trip_duration_s)))}
        ${raw(tile('Requests', r.daily.reduce((a, d) => a + d.requested, 0)))}${raw(tile('Completed', r.daily.reduce((a, d) => a + d.COMPLETED, 0)))}</div>
      <h2>Daily demand (IST)</h2>${raw(table(['Date', 'Requested', 'Completed', 'Cancelled', 'No driver', ''], r.daily.map((d) => html`<tr>
        <td>${d.date}</td><td class="num">${d.requested}</td><td class="num">${d.COMPLETED}</td><td class="num">${d.CANCELLED}</td><td class="num">${d.NO_DRIVER_AVAILABLE}</td>
        <td style="width:30%"><div class="bar ok" style="width:${(d.COMPLETED / max) * 100}%"></div><div class="bar danger" style="width:${((d.CANCELLED + d.NO_DRIVER_AVAILABLE) / max) * 100}%;margin-top:3px"></div></td></tr>`), 'No rides in this period.'))}
      <h2>Busiest pickups</h2>${raw(table(['Pickup', 'Rides'], r.top_pickups.map((p) => html`<tr><td>${p.name}</td><td class="num">${p.rides}</td></tr>`), 'No data yet.'))}
      <h2>Cancellations by</h2>${raw(table(['Cancelled by', 'Rides'], Object.entries(r.cancellations_by).map(([k, v]) => html`<tr><td>${k}</td><td class="num">${v}</td></tr>`), 'No cancellations.'))}
      <h2>Driver offer outcomes</h2>${raw(table(['Driver', 'Accepted', 'Rejected', 'Expired', 'Cancelled'], r.driver_offers.map((d) => html`<tr><td>${d.driver}</td>
        <td class="num">${d.ACCEPTED || 0}</td><td class="num">${d.REJECTED || 0}</td><td class="num">${d.EXPIRED || 0}</td><td class="num">${d.CANCELLED || 0}</td></tr>`), 'No offers in this period.'))}`;
  };
  $('#r-days').addEventListener('change', load);
  await load();
}

// ---------------------------------------------------------------- audit

async function audit() {
  const list = await api.get('/api/admin/audit?limit=200');
  $('#view').innerHTML = html`<h2>Audit log</h2>${raw(table(['When', 'Actor', 'Action', 'Target', 'Details'], list.map((a) => html`<tr>
    <td>${dateTime(a.at)}</td><td>${a.actor}<br><span class="small muted">${a.actor_role}</span></td><td><code>${a.action}</code></td>
    <td class="small">${a.target_type || ''} ${a.target_id ? raw(html`<code>${a.target_id.slice(0, 8)}</code>`) : ''}</td>
    <td class="small muted">${a.details ? JSON.stringify(a.details) : ''}</td></tr>`), 'No audit entries yet.'))}`;
}

boot();
