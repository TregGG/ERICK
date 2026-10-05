class WSClient {
  constructor() {
    this.ws = null;
    this.reconnectTimeout = null;
  }

  connect() {
    if (!auth.token) return;
    
    this.ws = new WebSocket(`${CONFIG.WS_BASE_URL}/ws/${auth.token}`);
    
    this.ws.onopen = () => {
      console.log('WS Connected');
      clearTimeout(this.reconnectTimeout);
    };
    
    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this.handleMessage(msg);
      } catch (e) {
        console.error('Invalid WS message', e);
      }
    };
    
    this.ws.onclose = () => {
      console.log('WS Disconnected');
      this.reconnect();
    };
  }

  reconnect() {
    clearTimeout(this.reconnectTimeout);
    this.reconnectTimeout = setTimeout(() => this.connect(), 5000);
  }

  sendLocation(lat, lng, heading) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        type: 'location_update',
        data: { lat, lng, heading }
      }));
    }
  }

  handleMessage(msg) {
    switch (msg.type) {
      case 'new_passenger_request':
        rideManager.handleNewRequest(msg.data);
        break;
      case 'passenger_cancelled':
        rideManager.getActiveRide(); // Refresh state
        break;
      case 'route_updated':
        if (msg.data.route_polyline) {
          // Note: In a real app, decode polyline using google.maps.geometry.encoding.decodePath
          // mapManager.showRoute(path);
        }
        if (msg.data.stops) {
          mapManager.showStops(msg.data.stops);
          mapManager.fitBounds();
        }
        break;
    }
  }
}

const wsClient = new WSClient();
