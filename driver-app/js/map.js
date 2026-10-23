/**
 * Map management module for Driver App using Leaflet.js
 */
class MapManager {
  constructor() {
    this.map = null;
    this.driverMarker = null;
    this.routePolyline = null;
    this.stopMarkers = [];
    this.detourPolyline = null;
  }

  init(containerId) {
    if (typeof L === 'undefined') {
      console.warn('Leaflet not loaded');
      return;
    }

    const center = [CONFIG.CAMPUS_CENTER.lat, CONFIG.CAMPUS_CENTER.lng];
    this.map = L.map(containerId, {
      zoomControl: true,
      attributionControl: true
    }).setView(center, 16);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '© OpenStreetMap contributors'
    }).addTo(this.map);

    this.driverIcon = L.divIcon({
      className: 'driver-marker-icon',
      html: '<div style="background:#1565C0;color:white;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;box-shadow:0 3px 10px rgba(0,0,0,0.5);border:2px solid white;font-size:20px;">🛺</div>',
      iconSize: [36, 36],
      iconAnchor: [18, 18]
    });

    // Allow clicking on map to update driver position (convenient for desktop testing)
    this.map.on('click', (e) => {
      const { lat, lng } = e.latlng;
      this.updatePosition(lat, lng);
      if (typeof wsClient !== 'undefined' && wsClient.sendLocation) {
        wsClient.sendLocation(lat, lng, 0);
      }
    });
  }

  updatePosition(lat, lng) {
    if (!this.map) return;
    if (!this.driverMarker) {
      this.driverMarker = L.marker([lat, lng], {
        icon: this.driverIcon,
        draggable: true
      }).addTo(this.map);
      this.driverMarker.bindPopup('<b>My Rickshaw</b><br><small>👉 Tap map or drag rick to move</small>');
      this.driverMarker.on('dragend', (e) => {
        const pos = e.target.getLatLng();
        this.updatePosition(pos.lat, pos.lng);
        if (typeof wsClient !== 'undefined' && wsClient.sendLocation) {
          wsClient.sendLocation(pos.lat, pos.lng, 0);
        }
      });
      this.map.panTo([lat, lng]);
    } else {
      this.driverMarker.setLatLng([lat, lng]);
    }

    // Instant local snap of polyline origin to new rickshaw position
    if (this.routePolyline) {
      const latLngs = this.routePolyline.getLatLngs();
      if (latLngs && latLngs.length > 0) {
        latLngs[0] = L.latLng(lat, lng);
        this.routePolyline.setLatLngs(latLngs);
      }
    }
  }

  showRoute(pathCoordinates) {
    if (!this.map || !pathCoordinates || pathCoordinates.length === 0) return;

    // Support both [[lat, lng], ...] and [{lat, lng}, ...]
    const latLngs = pathCoordinates.map(pt => Array.isArray(pt) ? pt : [pt.lat, pt.lng]);
    if (this.routePolyline) {
      this.routePolyline.setLatLngs(latLngs);
    } else {
      this.routePolyline = L.polyline(latLngs, {
        color: '#1565C0',
        weight: 5,
        opacity: 0.85
      }).addTo(this.map);
    }

    if (!this.hasFittedRouteBounds) {
      this.fitBounds();
      this.hasFittedRouteBounds = true;
    }
  }

  driveCloser() {
    if (!this.routePolyline || !this.driverMarker) return;
    const latLngs = this.routePolyline.getLatLngs();
    if (latLngs.length < 2) return;

    const stepIdx = Math.min(Math.max(1, Math.floor(latLngs.length * 0.15)), latLngs.length - 1);
    const target = latLngs[stepIdx];
    this.updatePosition(target.lat, target.lng);
    if (typeof wsClient !== 'undefined' && wsClient.sendLocation) {
      wsClient.sendLocation(target.lat, target.lng, 0);
    }
  }

  showStops(stops) {
    if (!this.map) return;
    this.clearStops();

    stops.forEach((stop, index) => {
      const isPickup = stop.type === 'pickup';
      const bgColor = isPickup ? '#2E7D32' : '#D32F2F';
      const typeLabel = isPickup ? '🟢 Pickup' : '🔴 Dropoff';

      const stopIcon = L.divIcon({
        className: 'stop-marker-icon',
        html: `<div style="background:${bgColor};color:white;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:13px;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.3);">${stop.order || index + 1}</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14]
      });

      const marker = L.marker([stop.lat, stop.lng], { icon: stopIcon }).addTo(this.map);
      marker.bindPopup(`<b>Stop ${stop.order || index + 1}: ${typeLabel}</b><br>${stop.passenger_name || 'Passenger'}<br>${stop.name || ''}`);
      this.stopMarkers.push(marker);
    });

    if (!this.hasFittedRouteBounds) {
      this.fitBounds();
      this.hasFittedRouteBounds = true;
    }
  }

  showDetourPreview(path) {
    if (!this.map || !path) return;
    if (this.detourPolyline) this.map.removeLayer(this.detourPolyline);

    const latLngs = path.map(pt => Array.isArray(pt) ? pt : [pt.lat, pt.lng]);
    this.detourPolyline = L.polyline(latLngs, {
      color: '#FF9800',
      weight: 4,
      dashArray: '6, 6'
    }).addTo(this.map);
  }

  clearRoute() {
    if (this.routePolyline) {
      this.map.removeLayer(this.routePolyline);
      this.routePolyline = null;
    }
    if (this.detourPolyline) {
      this.map.removeLayer(this.detourPolyline);
      this.detourPolyline = null;
    }
    this.hasFittedRouteBounds = false;
  }

  clearStops() {
    this.stopMarkers.forEach(m => this.map.removeLayer(m));
    this.stopMarkers = [];
  }

  fitBounds() {
    if (!this.map) return;
    if (this.routePolyline && this.routePolyline.getLatLngs().length > 1) {
      const b = this.routePolyline.getBounds();
      if (b.isValid()) {
        this.map.fitBounds(b, { padding: [50, 50] });
        return;
      }
    }

    const points = [];
    if (this.driverMarker) points.push(this.driverMarker.getLatLng());
    this.stopMarkers.forEach(m => points.push(m.getLatLng()));

    if (points.length > 1) {
      this.map.fitBounds(L.latLngBounds(points), { padding: [50, 50] });
    } else if (points.length === 1) {
      this.map.panTo(points[0]);
    }
  }
}

const mapManager = new MapManager();
