class RideManager {
  constructor() {
    this.passengers = [];
    this.stops = [];
    this.routePolyline = [];
    this.nextStop = null;
    this.routeDistance = 0;
    this.isOnline = false;
    this.currentRequest = null;
    this.requestTimer = null;
  }

  async setOnlineStatus(online) {
    try {
      const endpoint = online ? 'go-online' : 'go-offline';
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/${endpoint}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${auth.token}` }
      });
      if (res.ok) {
        this.isOnline = online;
        this.updateUI();
        if (online) {
          locationTracker.startTracking();
        } else {
          locationTracker.stopTracking();
        }
        return true;
      }
      return false;
    } catch (e) {
      console.error(e);
      return false;
    }
  }

  async getActiveRide() {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/active-ride`, {
        headers: { 'Authorization': `Bearer ${auth.token}` }
      });
      if (res.ok) {
        const data = await res.json();
        this.passengers = data.passengers || [];
        this.stops = data.stops || [];
        this.nextStop = data.next_stop || (this.stops.length > 0 ? this.stops[0] : null);
        this.routePolyline = (data.active_ride && data.active_ride.route_polyline) || [];
        this.isOnline = true;
        document.getElementById('online-toggle').checked = true;
        this.updateUI();
        if (this.passengers.length > 0) {
          locationTracker.startTracking();
        }
      }
    } catch (e) {
      console.error('Failed to get active ride', e);
    }
  }

  handleRouteUpdated(data) {
    if (data.stops) this.stops = data.stops;
    if (data.route_polyline) this.routePolyline = data.route_polyline;
    if (data.next_stop) this.nextStop = data.next_stop;
    else if (this.stops.length > 0) this.nextStop = this.stops[0];
    else this.nextStop = null;
    this.routeDistance = data.distance_meters || 0;
    this.updateUI();
  }

  handleNewRequest(data) {
    if (data.is_auto_accepted) {
      this.getActiveRide();
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      const toast = document.createElement('div');
      toast.className = 'auto-pool-toast';
      toast.style.cssText = 'position:fixed;top:70px;left:50%;transform:translateX(-50%);background:#2E7D32;color:white;padding:10px 18px;border-radius:20px;font-size:13px;font-weight:600;z-index:99999;box-shadow:0 4px 12px rgba(0,0,0,0.3);';
      toast.innerHTML = `🟢 Auto-pooled: ${data.passenger_name} (${data.detour_meters}m on-route)`;
      document.body.appendChild(toast);
      setTimeout(() => toast.remove(), 4000);
      return;
    }

    this.currentRequest = data;
    this.showRequestPopup();
  }

  showRequestPopup() {
    const popup = document.getElementById('request-popup');
    document.getElementById('req-passenger-name').textContent = this.currentRequest.passenger_name;
    document.getElementById('req-pickup').textContent = this.currentRequest.pickup.name;
    document.getElementById('req-dropoff').textContent = this.currentRequest.dropoff.name;
    document.getElementById('req-detour').textContent = this.currentRequest.detour_meters;
    
    popup.classList.remove('hidden');
    if (navigator.vibrate) navigator.vibrate([500, 200, 500]);

    // Timer animation
    const fill = document.getElementById('req-timer-fill');
    fill.style.transition = 'none';
    fill.style.width = '100%';
    
    setTimeout(() => {
      fill.style.transition = `width ${CONFIG.REQUEST_TIMEOUT}ms linear`;
      fill.style.width = '0%';
    }, 50);

    clearTimeout(this.requestTimer);
    this.requestTimer = setTimeout(() => {
      this.rejectPassenger(this.currentRequest.request_id);
    }, CONFIG.REQUEST_TIMEOUT);
  }

  hideRequestPopup() {
    document.getElementById('request-popup').classList.add('hidden');
    clearTimeout(this.requestTimer);
    this.currentRequest = null;
  }

  async acceptPassenger(requestId) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/accept-passenger/${requestId}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${auth.token}` }
      });
      if (res.ok) {
        this.hideRequestPopup();
        this.getActiveRide();
      }
    } catch (e) {
      console.error(e);
    }
  }

  async rejectPassenger(requestId) {
    try {
      await fetch(`${CONFIG.API_BASE_URL}/api/driver/reject-passenger/${requestId}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${auth.token}` }
      });
    } catch (e) {
      console.error(e);
    } finally {
      this.hideRequestPopup();
    }
  }

  async pickupPassenger(requestId) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/pickup/${requestId}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${auth.token}` }
      });
      if (res.ok) this.getActiveRide();
    } catch (e) {
      console.error(e);
    }
  }

  async dropoffPassenger(requestId) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/dropoff/${requestId}`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${auth.token}` }
      });
      if (res.ok) this.getActiveRide();
    } catch (e) {
      console.error(e);
    }
  }

  updateUI() {
    const statusText = document.getElementById('status-text');
    const noRide = document.getElementById('no-ride-state');
    const activeRide = document.getElementById('active-ride-state');
    const seatCounter = document.getElementById('seat-counter');
    const nextBanner = document.getElementById('next-stop-banner');
    
    statusText.textContent = this.isOnline ? 'Online' : 'Offline';
    
    if (this.isOnline) {
      seatCounter.classList.remove('hidden');
      
      const activePassengers = this.passengers.filter(p => p.status !== 'dropped_off');
      seatCounter.textContent = `${activePassengers.length}/4 Seats`;
      
      if (activePassengers.length > 0) {
        noRide.classList.add('hidden');
        activeRide.classList.remove('hidden');

        // Render Next Stop Banner
        if (this.nextStop && nextBanner) {
          nextBanner.classList.remove('hidden');
          const isPickup = this.nextStop.type === 'pickup';
          if (isPickup) {
            nextBanner.classList.remove('dropoff-mode');
            document.getElementById('next-stop-icon').textContent = '🟢';
            document.getElementById('next-stop-target').textContent = `Stop ${this.nextStop.order || 1}: Pick up ${this.nextStop.passenger_name}`;
            const btn = document.getElementById('next-stop-action-btn');
            btn.textContent = 'Pick Up';
            btn.className = 'btn btn-sm btn-next-action';
            btn.onclick = () => this.pickupPassenger(this.nextStop.request_id);
          } else {
            nextBanner.classList.add('dropoff-mode');
            document.getElementById('next-stop-icon').textContent = '🔴';
            document.getElementById('next-stop-target').textContent = `Stop ${this.nextStop.order || 1}: Drop off ${this.nextStop.passenger_name}`;
            const btn = document.getElementById('next-stop-action-btn');
            btn.textContent = 'Drop Off';
            btn.className = 'btn btn-sm btn-next-action btn-dropoff';
            btn.onclick = () => this.dropoffPassenger(this.nextStop.request_id);
          }
          document.getElementById('next-stop-location').textContent = this.nextStop.name || '';
        } else if (nextBanner) {
          nextBanner.classList.add('hidden');
        }

        // Render ordered route stops timeline
        this.renderStopsTimeline(this.stops);

        // Render passenger list
        this.renderPassengerList(activePassengers);

        // Map updates: stops markers & route polyline
        if (this.stops && this.stops.length > 0) {
          mapManager.showStops(this.stops);
        } else {
          mapManager.clearStops();
        }

        if (this.routePolyline && this.routePolyline.length > 0) {
          mapManager.showRoute(this.routePolyline);
        }

      } else {
        noRide.classList.remove('hidden');
        noRide.innerHTML = '<p>Waiting for passengers...</p>';
        activeRide.classList.add('hidden');
        nextBanner?.classList.add('hidden');
        mapManager.clearRoute();
        mapManager.clearStops();
      }
    } else {
      seatCounter.classList.add('hidden');
      noRide.classList.remove('hidden');
      noRide.innerHTML = '<p>Go online to start receiving requests</p>';
      activeRide.classList.add('hidden');
      nextBanner?.classList.add('hidden');
      mapManager.clearRoute();
      mapManager.clearStops();
    }
  }

  renderStopsTimeline(stops) {
    const container = document.getElementById('stops-timeline');
    if (!container) return;
    container.innerHTML = '';

    const distBadge = document.getElementById('route-distance-badge');
    if (distBadge) {
      distBadge.textContent = this.routeDistance ? `${this.routeDistance}m total` : `${stops.length} stops`;
    }

    stops.forEach((s, idx) => {
      const isPickup = s.type === 'pickup';
      const isCurrentNext = this.nextStop && this.nextStop.request_id === s.request_id && this.nextStop.type === s.type;

      const item = document.createElement('div');
      item.className = `timeline-item ${isCurrentNext ? 'active-stop' : ''}`;

      item.innerHTML = `
        <div class="timeline-badge ${isPickup ? 'pickup' : 'dropoff'}">${s.order || idx + 1}</div>
        <div class="timeline-content">
          <div class="timeline-title">${isPickup ? '🟢 Pick up' : '🔴 Drop off'} ${s.passenger_name}</div>
          <div class="timeline-sub">${s.name || ''}</div>
        </div>
      `;
      container.appendChild(item);
    });
  }

  renderPassengerList(passengers) {
    const list = document.getElementById('passenger-list');
    list.innerHTML = '';
    
    passengers.forEach(p => {
      const card = document.createElement('div');
      card.className = 'passenger-card';
      
      let statusClass = 'status-waiting';
      let statusText = 'Waiting Pickup';
      let actionBtn = `<button class="btn btn-primary" onclick="rideManager.pickupPassenger('${p.request_id}')">Pick Up</button>`;
      
      if (p.status === 'in_vehicle') {
        statusClass = 'status-in-vehicle';
        statusText = 'In Vehicle';
        actionBtn = `<button class="btn btn-success" onclick="rideManager.dropoffPassenger('${p.request_id}')">Drop Off</button>`;
      }
      
      card.innerHTML = `
        <div class="passenger-info">
          <div class="passenger-name">${p.name}</div>
          <div class="status-badge ${statusClass}">${statusText}</div>
        </div>
        <div class="passenger-route">
          <strong>From:</strong> ${p.pickup_name}<br>
          <strong>To:</strong> ${p.dropoff_name}
        </div>
        ${actionBtn}
      `;
      list.appendChild(card);
    });
  }
}

const rideManager = new RideManager();

