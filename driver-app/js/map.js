class MapManager {
  constructor() {
    this.map = null;
    this.driverMarker = null;
    this.routePolyline = null;
    this.stopMarkers = [];
    this.detourPolyline = null;
  }

  init(containerId) {
    // Mock init if Maps API not loaded
    if (!window.google) {
      console.warn('Google Maps not loaded');
      return;
    }
    
    this.map = new google.maps.Map(document.getElementById(containerId), {
      center: CONFIG.CAMPUS_CENTER,
      zoom: 15,
      disableDefaultUI: true,
      zoomControl: true
    });
  }

  updatePosition(lat, lng) {
    if (!this.map) return;
    const pos = { lat, lng };
    if (!this.driverMarker) {
      this.driverMarker = new google.maps.Marker({
        position: pos,
        map: this.map,
        icon: {
          path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW,
          scale: 6,
          fillColor: '#1565C0',
          fillOpacity: 1,
          strokeWeight: 2,
          strokeColor: '#ffffff'
        }
      });
      this.map.panTo(pos);
    } else {
      this.driverMarker.setPosition(pos);
    }
  }

  showRoute(pathCoordinates) {
    if (!this.map) return;
    if (this.routePolyline) this.routePolyline.setMap(null);
    
    this.routePolyline = new google.maps.Polyline({
      path: pathCoordinates,
      geodesic: true,
      strokeColor: '#1565C0',
      strokeOpacity: 0.8,
      strokeWeight: 5
    });
    this.routePolyline.setMap(this.map);
  }

  showStops(stops) {
    if (!this.map) return;
    this.clearStops();
    
    stops.forEach((stop, index) => {
      const isPickup = stop.type === 'pickup';
      const marker = new google.maps.Marker({
        position: { lat: stop.lat, lng: stop.lng },
        map: this.map,
        label: {
          text: (index + 1).toString(),
          color: 'white',
          fontWeight: 'bold'
        },
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 12,
          fillColor: isPickup ? '#2e7d32' : '#c62828',
          fillOpacity: 1,
          strokeWeight: 2,
          strokeColor: 'white'
        },
        title: stop.passenger_name
      });
      this.stopMarkers.push(marker);
    });
  }

  showDetourPreview(path) {
    if (!this.map) return;
    if (this.detourPolyline) this.detourPolyline.setMap(null);
    
    this.detourPolyline = new google.maps.Polyline({
      path: path,
      geodesic: true,
      strokeColor: '#ff9800',
      strokeOpacity: 0,
      strokeWeight: 4,
      icons: [{
        icon: { path: 'M 0,-1 0,1', strokeOpacity: 1, scale: 4 },
        offset: '0',
        repeat: '20px'
      }]
    });
    this.detourPolyline.setMap(this.map);
  }

  clearRoute() {
    if (this.routePolyline) this.routePolyline.setMap(null);
    if (this.detourPolyline) this.detourPolyline.setMap(null);
    this.clearStops();
  }

  clearStops() {
    this.stopMarkers.forEach(m => m.setMap(null));
    this.stopMarkers = [];
  }

  fitBounds() {
    if (!this.map || this.stopMarkers.length === 0) return;
    const bounds = new google.maps.LatLngBounds();
    this.stopMarkers.forEach(m => bounds.extend(m.getPosition()));
    if (this.driverMarker) bounds.extend(this.driverMarker.getPosition());
    this.map.fitBounds(bounds, 50); // 50px padding
  }
}

const mapManager = new MapManager();
