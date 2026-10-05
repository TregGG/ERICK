/**
 * Map management module
 */
class MapManager {
    constructor() {
        this.map = null;
        this.pickupMarker = null;
        this.dropoffMarker = null;
        this.driverMarker = null;
        this.landmarkMarkers = [];
        this.directionsService = null;
        this.directionsRenderer = null;
        this.geocoder = null;
    }

    /**
     * Initializes the Google Map
     */
    async initMap() {
        if (typeof google === 'undefined') {
            console.warn("Google Maps not loaded");
            return;
        }

        this.map = new google.maps.Map(document.getElementById('map-container'), {
            center: CONFIG.DEFAULT_CENTER,
            zoom: CONFIG.DEFAULT_ZOOM,
            disableDefaultUI: true,
            zoomControl: false,
            styles: [
                {
                    "featureType": "poi",
                    "stylers": [{ "visibility": "off" }]
                }
            ]
        });

        this.directionsService = new google.maps.DirectionsService();
        this.directionsRenderer = new google.maps.DirectionsRenderer({
            map: this.map,
            suppressMarkers: true,
            polylineOptions: {
                strokeColor: '#2E7D32',
                strokeWeight: 4
            }
        });
        this.geocoder = new google.maps.Geocoder();
        
        // Tap handler
        this.map.addListener('click', (e) => {
            if (rideManager.state === 'idle') {
                if (!this.pickupMarker) {
                    this.setPickupMarker(e.latLng.lat(), e.latLng.lng());
                    document.getElementById('input-pickup').value = "Map Selection";
                } else if (!this.dropoffMarker) {
                    this.setDropoffMarker(e.latLng.lat(), e.latLng.lng());
                    document.getElementById('input-dropoff').value = "Map Selection";
                }
            }
        });
    }

    /**
     * Sets the pickup marker on the map
     * @param {number} lat 
     * @param {number} lng 
     */
    setPickupMarker(lat, lng) {
        if (!this.map) return;
        if (this.pickupMarker) {
            this.pickupMarker.setPosition({ lat, lng });
        } else {
            this.pickupMarker = new google.maps.Marker({
                position: { lat, lng },
                map: this.map,
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    fillColor: '#2E7D32',
                    fillOpacity: 1,
                    strokeColor: '#ffffff',
                    strokeWeight: 2,
                    scale: 8
                },
                title: 'Pickup'
            });
        }
        this.map.panTo({ lat, lng });
    }

    /**
     * Sets the dropoff marker on the map
     * @param {number} lat 
     * @param {number} lng 
     */
    setDropoffMarker(lat, lng) {
        if (!this.map) return;
        if (this.dropoffMarker) {
            this.dropoffMarker.setPosition({ lat, lng });
        } else {
            this.dropoffMarker = new google.maps.Marker({
                position: { lat, lng },
                map: this.map,
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    fillColor: '#D32F2F',
                    fillOpacity: 1,
                    strokeColor: '#ffffff',
                    strokeWeight: 2,
                    scale: 8
                },
                title: 'Dropoff'
            });
        }
    }

    /**
     * Updates the driver's location marker
     * @param {number} lat 
     * @param {number} lng 
     * @param {number} heading 
     */
    updateDriverMarker(lat, lng, heading) {
        if (!this.map) return;
        if (this.driverMarker) {
            this.driverMarker.setPosition({ lat, lng });
            const icon = this.driverMarker.getIcon();
            icon.rotation = heading || 0;
            this.driverMarker.setIcon(icon);
        } else {
            this.driverMarker = new google.maps.Marker({
                position: { lat, lng },
                map: this.map,
                icon: {
                    path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW,
                    fillColor: '#F57C00',
                    fillOpacity: 1,
                    strokeColor: '#ffffff',
                    strokeWeight: 2,
                    scale: 6,
                    rotation: heading || 0
                },
                title: 'Rickshaw'
            });
        }
    }

    /**
     * Shows landmark markers on the map
     * @param {Array} landmarks Array of {name, lat, lng}
     */
    showLandmarks(landmarks) {
        if (!this.map) return;
        this.landmarkMarkers.forEach(m => m.setMap(null));
        this.landmarkMarkers = landmarks.map(landmark => {
            return new google.maps.Marker({
                position: { lat: landmark.lat, lng: landmark.lng },
                map: this.map,
                label: {
                    text: landmark.name,
                    fontSize: '12px',
                    fontWeight: 'bold',
                    color: '#333'
                },
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    scale: 4,
                    fillColor: '#9E9E9E',
                    fillOpacity: 1,
                    strokeWeight: 0
                }
            });
        });
    }

    /**
     * Clears routing and specific markers
     */
    clearMarkers() {
        if (this.pickupMarker) this.pickupMarker.setMap(null);
        if (this.dropoffMarker) this.dropoffMarker.setMap(null);
        if (this.driverMarker) this.driverMarker.setMap(null);
        this.pickupMarker = null;
        this.dropoffMarker = null;
        this.driverMarker = null;
        if (this.directionsRenderer) this.directionsRenderer.setDirections({ routes: [] });
    }

    /**
     * Draws route between two points
     * @param {Object} pickup {lat, lng}
     * @param {Object} dropoff {lat, lng}
     */
    drawRoute(pickup, dropoff) {
        if (!this.directionsService) return;
        const request = {
            origin: pickup,
            destination: dropoff,
            travelMode: google.maps.TravelMode.DRIVING
        };
        this.directionsService.route(request, (result, status) => {
            if (status == 'OK') {
                this.directionsRenderer.setDirections(result);
            }
        });
    }

    /**
     * Gets current user location
     * @returns {Promise<{lat: number, lng: number}>}
     */
    getCurrentLocation() {
        return new Promise((resolve, reject) => {
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    (position) => resolve({ lat: position.coords.latitude, lng: position.coords.longitude }),
                    (error) => reject(error),
                    { enableHighAccuracy: true }
                );
            } else {
                reject(new Error('Geolocation not supported'));
            }
        });
    }

    /**
     * Geocodes coordinates to a place name
     * @param {number} lat 
     * @param {number} lng 
     * @returns {Promise<string>}
     */
    async geocodeLocation(lat, lng) {
        if (!this.geocoder) return `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
        try {
            const response = await this.geocoder.geocode({ location: { lat, lng } });
            if (response.results[0]) {
                return response.results[0].formatted_address;
            }
            return 'Unknown Location';
        } catch (e) {
            console.error('Geocode error:', e);
            return `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
        }
    }
}

const mapManager = new MapManager();
