/**
 * Map management module using Leaflet.js
 */
class MapManager {
    constructor() {
        this.map = null;
        this.pickupMarker = null;
        this.dropoffMarker = null;
        this.driverMarker = null;
        this.routeLine = null;
        this.landmarks = [];
        this.activeMode = 'pickup'; // 'pickup' or 'dropoff'
    }

    /**
     * Initializes the Leaflet Map
     */
    async initMap() {
        if (typeof L === 'undefined') {
            console.error("Leaflet library not loaded");
            return;
        }

        const center = [CONFIG.DEFAULT_CENTER.lat, CONFIG.DEFAULT_CENTER.lng];
        this.map = L.map('map-container', {
            zoomControl: false,
            attributionControl: true
        }).setView(center, CONFIG.DEFAULT_ZOOM || 16);

        // OpenStreetMap tile layer (reliable & free)
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors'
        }).addTo(this.map);

        // Zoom control in top right
        L.control.zoom({ position: 'topright' }).addTo(this.map);

        // Custom pin icons
        this.pickupIcon = L.divIcon({
            className: 'custom-pin-pickup',
            html: '<div style="background:#2E7D32;color:white;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;box-shadow:0 3px 8px rgba(0,0,0,0.4);border:2px solid white;font-weight:bold;font-size:16px;">🟢</div>',
            iconSize: [32, 32],
            iconAnchor: [16, 16]
        });

        this.dropoffIcon = L.divIcon({
            className: 'custom-pin-dropoff',
            html: '<div style="background:#D32F2F;color:white;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;box-shadow:0 3px 8px rgba(0,0,0,0.4);border:2px solid white;font-weight:bold;font-size:16px;">🔴</div>',
            iconSize: [32, 32],
            iconAnchor: [16, 16]
        });

        this.driverIcon = L.divIcon({
            className: 'custom-pin-driver',
            html: '<div style="background:#1565C0;color:white;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;box-shadow:0 3px 8px rgba(0,0,0,0.4);border:2px solid white;font-size:18px;">🛺</div>',
            iconSize: [36, 36],
            iconAnchor: [18, 18]
        });

        // Click on map to place pin
        this.map.on('click', (e) => {
            const { lat, lng } = e.latlng;
            if (this.activeMode === 'pickup') {
                this.setPickupMarker(lat, lng, `Lat: ${lat.toFixed(4)}, Lng: ${lng.toFixed(4)}`);
                // Auto advance mode to dropoff if not set yet
                if (!this.dropoffMarker) {
                    this.setMode('dropoff');
                }
            } else {
                this.setDropoffMarker(lat, lng, `Lat: ${lat.toFixed(4)}, Lng: ${lng.toFixed(4)}`);
            }
        });

        // Load campus landmarks
        await this.loadLandmarks();
    }

    setMode(mode) {
        this.activeMode = mode;
        const btnPickup = document.getElementById('btn-mode-pickup');
        const btnDropoff = document.getElementById('btn-mode-dropoff');
        const instruction = document.getElementById('pin-instruction');

        if (mode === 'pickup') {
            btnPickup?.classList.add('active');
            btnDropoff?.classList.remove('active');
            if (instruction) instruction.innerHTML = '👉 Tap map to set <b>Start (Pickup)</b>';
        } else {
            btnDropoff?.classList.add('active');
            btnPickup?.classList.remove('active');
            if (instruction) instruction.innerHTML = '👉 Tap map to set <b>Stop (Dropoff)</b>';
        }
    }

    async loadLandmarks() {
        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/api/locations/landmarks`);
            if (res.ok) {
                this.landmarks = await res.json();
                this.renderLandmarkChips();
            }
        } catch (e) {
            console.warn("Could not load landmarks", e);
        }
    }

    renderLandmarkChips() {
        const container = document.getElementById('landmark-chips');
        if (!container) return;
        container.innerHTML = '';

        this.landmarks.forEach(lm => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'chip';
            chip.textContent = lm.name;
            chip.addEventListener('click', () => {
                if (this.activeMode === 'pickup') {
                    this.setPickupMarker(lm.lat, lm.lng, lm.name);
                    if (!this.dropoffMarker) this.setMode('dropoff');
                } else {
                    this.setDropoffMarker(lm.lat, lm.lng, lm.name);
                }
            });
            container.appendChild(chip);
        });
    }

    setPickupMarker(lat, lng, name) {
        if (!this.map) return;
        if (this.pickupMarker) {
            this.pickupMarker.setLatLng([lat, lng]);
        } else {
            this.pickupMarker = L.marker([lat, lng], {
                icon: this.pickupIcon,
                draggable: true
            }).addTo(this.map);

            this.pickupMarker.on('dragend', (e) => {
                const pos = e.target.getLatLng();
                this.setPickupMarker(pos.lat, pos.lng, `Lat: ${pos.lat.toFixed(4)}, Lng: ${pos.lng.toFixed(4)}`);
            });
        }

        const label = name || `Lat: ${lat.toFixed(4)}, Lng: ${lng.toFixed(4)}`;
        this.pickupMarker.bindPopup(`<b>🟢 Start (Pickup)</b><br>${label}`).openPopup();
        const input = document.getElementById('input-pickup');
        if (input) input.value = label;

        this.updateRouteLine();
    }

    setDropoffMarker(lat, lng, name) {
        if (!this.map) return;
        if (this.dropoffMarker) {
            this.dropoffMarker.setLatLng([lat, lng]);
        } else {
            this.dropoffMarker = L.marker([lat, lng], {
                icon: this.dropoffIcon,
                draggable: true
            }).addTo(this.map);

            this.dropoffMarker.on('dragend', (e) => {
                const pos = e.target.getLatLng();
                this.setDropoffMarker(pos.lat, pos.lng, `Lat: ${pos.lat.toFixed(4)}, Lng: ${pos.lng.toFixed(4)}`);
            });
        }

        const label = name || `Lat: ${lat.toFixed(4)}, Lng: ${lng.toFixed(4)}`;
        this.dropoffMarker.bindPopup(`<b>🔴 Stop (Dropoff)</b><br>${label}`).openPopup();
        const input = document.getElementById('input-dropoff');
        if (input) input.value = label;

        this.updateRouteLine();
    }

    updateRouteLine() {
        if (this.pickupMarker && this.dropoffMarker) {
            const p = this.pickupMarker.getLatLng();
            const d = this.dropoffMarker.getLatLng();

            if (this.routeLine) {
                this.routeLine.setLatLngs([[p.lat, p.lng], [d.lat, d.lng]]);
            } else {
                this.routeLine = L.polyline([[p.lat, p.lng], [d.lat, d.lng]], {
                    color: '#2E7D32',
                    weight: 4,
                    dashArray: '8, 8'
                }).addTo(this.map);
            }

            this.map.fitBounds(L.latLngBounds([p, d]), { padding: [50, 50] });
        }
    }

    async drawRoute(start, end) {
        if (!this.map || !start || !end) return;

        let points = [[start.lat, start.lng], [end.lat, end.lng]];

        // Instant local update
        if (this.routeLine) {
            this.routeLine.setLatLngs(points);
        } else {
            this.routeLine = L.polyline(points, {
                color: '#2E7D32',
                weight: 5,
                opacity: 0.85
            }).addTo(this.map);
        }

        if (!this.hasFittedRouteBounds && this.routeLine.getLatLngs().length > 1) {
            this.map.fitBounds(this.routeLine.getBounds(), { padding: [50, 50] });
            this.hasFittedRouteBounds = true;
        }

        // Throttled OSRM road geometry fetch (max once every 2s)
        const now = Date.now();
        if (!this._lastOsrmTime || now - this._lastOsrmTime > 2000) {
            this._lastOsrmTime = now;
            try {
                const url = `https://router.project-osrm.org/route/v1/driving/${start.lng},${start.lat};${end.lng},${end.lat}?overview=full&geometries=geojson`;
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    if (data.routes && data.routes.length > 0) {
                        const roadCoords = data.routes[0].geometry.coordinates.map(pt => [pt[1], pt[0]]);
                        if (this.routeLine) {
                            this.routeLine.setLatLngs(roadCoords);
                        }
                    }
                }
            } catch (e) {
                console.warn("OSRM passenger route fallback:", e);
            }
        }
    }

    setRoadPolyline(pathCoordinates) {
        if (!this.map || !pathCoordinates || pathCoordinates.length === 0) return;
        const latLngs = pathCoordinates.map(pt => Array.isArray(pt) ? pt : [pt.lat, pt.lng]);
        if (this.routeLine) {
            this.routeLine.setLatLngs(latLngs);
        } else {
            this.routeLine = L.polyline(latLngs, {
                color: '#2E7D32',
                weight: 5,
                opacity: 0.85
            }).addTo(this.map);
        }
        if (!this.hasFittedRouteBounds && this.routeLine.getLatLngs().length > 1) {
            this.map.fitBounds(this.routeLine.getBounds(), { padding: [50, 50] });
            this.hasFittedRouteBounds = true;
        }
    }

    calculateETA(p1, p2) {
        if (!p1 || !p2) return 1;
        const R = 6371000;
        const dLat = (p2.lat - p1.lat) * Math.PI / 180;
        const dLng = (p2.lng - p1.lng) * Math.PI / 180;
        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                  Math.cos(p1.lat * Math.PI / 180) * Math.cos(p2.lat * Math.PI / 180) *
                  Math.sin(dLng / 2) * Math.sin(dLng / 2);
        const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        const meters = R * c;
        return Math.max(1, Math.round(meters / 250)); // ~15 km/h
    }

    clearMarkers() {
        if (this.pickupMarker) {
            this.map.removeLayer(this.pickupMarker);
            this.pickupMarker = null;
        }
        if (this.dropoffMarker) {
            this.map.removeLayer(this.dropoffMarker);
            this.dropoffMarker = null;
        }
        if (this.routeLine) {
            this.map.removeLayer(this.routeLine);
            this.routeLine = null;
        }
        if (this.driverMarker) {
            this.map.removeLayer(this.driverMarker);
            this.driverMarker = null;
        }
        this.hasFittedRouteBounds = false;
        const pInput = document.getElementById('input-pickup');
        const dInput = document.getElementById('input-dropoff');
        if (pInput) pInput.value = '';
        if (dInput) dInput.value = '';
        this.setMode('pickup');
    }

    updateDriverMarker(lat, lng, heading) {
        if (!this.map) return;
        if (!this.driverMarker) {
            this.driverMarker = L.marker([lat, lng], {
                icon: this.driverIcon
            }).addTo(this.map);
            this.driverMarker.bindPopup('<b>🛺 Campus Rick</b>');
        } else {
            this.driverMarker.setLatLng([lat, lng]);
        }
    }

    async getCurrentLocation() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) {
                reject(new Error("Geolocation not supported"));
                return;
            }
            navigator.geolocation.getCurrentPosition(
                (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
                (err) => reject(err),
                { enableHighAccuracy: true, timeout: 5000 }
            );
        });
    }
}

const mapManager = new MapManager();
