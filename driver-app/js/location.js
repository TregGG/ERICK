class LocationTracker {
  constructor() {
    this.watchId = null;
    this.lastPos = null;
  }

  startTracking() {
    if (!navigator.geolocation) {
      mapManager.updatePosition(CONFIG.CAMPUS_CENTER.lat, CONFIG.CAMPUS_CENTER.lng);
      wsClient.sendLocation(CONFIG.CAMPUS_CENTER.lat, CONFIG.CAMPUS_CENTER.lng, 0);
      return;
    }
    
    this.stopTracking(); // Ensure no duplicates
    
    this.watchId = navigator.geolocation.watchPosition(
      (position) => {
        const { latitude: lat, longitude: lng, heading } = position.coords;
        
        // Update map
        mapManager.updatePosition(lat, lng);
        
        // Send to server
        wsClient.sendLocation(lat, lng, heading || 0);
        
        this.lastPos = { lat, lng };
      },
      (error) => {
        console.warn('Geolocation unavailable/denied, defaulting to campus center:', error.message);
        if (!this.lastPos) {
          mapManager.updatePosition(CONFIG.CAMPUS_CENTER.lat, CONFIG.CAMPUS_CENTER.lng);
          wsClient.sendLocation(CONFIG.CAMPUS_CENTER.lat, CONFIG.CAMPUS_CENTER.lng, 0);
        }
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 0
      }
    );
  }

  stopTracking() {
    if (this.watchId !== null) {
      navigator.geolocation.clearWatch(this.watchId);
      this.watchId = null;
    }
  }
}

const locationTracker = new LocationTracker();
