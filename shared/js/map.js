// Leaflet + OpenStreetMap helpers. `L` is the global from the Leaflet script tag.
import { esc } from './ui.js';

export const icons = () => ({
  pickup: L.divIcon({ className: '', html: '<div class="pin pin-pickup"></div>', iconSize: [30, 30], iconAnchor: [15, 30] }),
  dropoff: L.divIcon({ className: '', html: '<div class="pin pin-dropoff"></div>', iconSize: [30, 30], iconAnchor: [15, 30] }),
  offer: L.divIcon({ className: '', html: '<div class="pin pin-offer"></div>', iconSize: [30, 30], iconAnchor: [15, 30] }),
  me: L.divIcon({ className: '', html: '<div class="me-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }),
  available: L.divIcon({ className: '', html: '<div class="available-dot"></div>', iconSize: [14, 14], iconAnchor: [7, 7] }),
  // Online rickshaws on the passenger map: free ones in the brand colour, busy ones greyed out.
  rickshaw: L.divIcon({ className: '', html: '<div class="rickshaw-marker">&#x1F6FA;</div>', iconSize: [28, 28], iconAnchor: [14, 14] }),
  rickshawBusy: L.divIcon({ className: '', html: '<div class="rickshaw-marker busy">&#x1F6FA;</div>', iconSize: [28, 28], iconAnchor: [14, 14] }),
  vehicle: (state = '') => L.divIcon({ className: '', html: `<div class="vehicle-marker ${state}">&#x1F6FA;</div>`, iconSize: [38, 38], iconAnchor: [19, 19] }),
});

export function createMap(element, config, { onTilesFailed } = {}) {
  if (typeof L === 'undefined') {
    element.innerHTML = '<div class="empty"><div class="icon">&#x1F5FA;</div>The map library could not load. Check your connection and reload.</div>';
    return null;
  }
  const map = L.map(element, { zoomControl: false, attributionControl: true })
    .setView([config.campus_center.lat, config.campus_center.lng], 16);
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  const tiles = L.tileLayer(config.map.tile_url, { maxZoom: 19, attribution: config.map.attribution }).addTo(map);
  let loaded = 0;
  let failed = 0;
  tiles.on('tileload', () => { loaded += 1; });
  tiles.on('tileerror', () => {
    failed += 1;
    if (failed === 6 && loaded === 0 && onTilesFailed) onTilesFailed();
  });
  return map;
}

export function drawCampus(map, campus, { landmarks = true } = {}) {
  const layer = L.layerGroup().addTo(map);
  campus.zones.filter((z) => z.kind === 'boundary').forEach((zone) => {
    L.geoJSON(zone.geometry, { interactive: false, style: { color: '#2e7d32', weight: 2, dashArray: '6 6', fillOpacity: 0.03 } }).addTo(layer);
  });
  if (landmarks) {
    campus.landmarks.forEach((lm) => {
      L.marker([lm.lat, lm.lng], {
        icon: L.divIcon({ className: '', html: `<div class="landmark-dot ${lm.is_pickup_point ? 'pickup-point' : ''}"></div>`, iconSize: [10, 10], iconAnchor: [5, 5] }),
        keyboard: false, title: lm.name,
      }).bindTooltip(esc(lm.name), { direction: 'top', offset: [0, -4] }).addTo(layer);
    });
  }
  return layer;
}

export function fitTo(map, points, padding = [60, 60]) {
  const valid = points.filter(Boolean).map((p) => (Array.isArray(p) ? p : [p.lat, p.lng]));
  if (!map || !valid.length) return;
  if (valid.length === 1) { map.setView(valid[0], Math.max(map.getZoom(), 17)); return; }
  // On phones the bottom sheet covers up to ~60% of the screen; keep fitted points above it.
  const sheet = document.querySelector('.sheet');
  const bottomPad = window.innerWidth < 900 ? Math.min(Math.round(window.innerHeight * 0.7), sheet ? sheet.offsetHeight + 24 : Math.round(window.innerHeight * 0.6)) : padding[1];
  const leftPad = window.innerWidth >= 900 ? 430 : padding[0];
  map.fitBounds(L.latLngBounds(valid), { paddingTopLeft: [leftPad, 80], paddingBottomRight: [padding[0], bottomPad], maxZoom: 18 });
}

export function externalNavigationUrl(lat, lng) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}
