class RideManager {
  constructor() {
    this.passengers = [];
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
        this.isOnline = true; // Assume online if we have active ride data
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

  handleNewRequest(data) {
    if (data.is_auto_accepted) {
      // Just update list and show quick notification
      this.getActiveRide();
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
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
    
    statusText.textContent = this.isOnline ? 'Online' : 'Offline';
    
    if (this.isOnline) {
      seatCounter.classList.remove('hidden');
      
      const activePassengers = this.passengers.filter(p => p.status !== 'dropped_off');
      seatCounter.textContent = `${activePassengers.length}/4 Seats`;
      
      if (activePassengers.length > 0) {
        noRide.classList.add('hidden');
        activeRide.classList.remove('hidden');
        this.renderPassengerList(activePassengers);
      } else {
        noRide.classList.remove('hidden');
        noRide.innerHTML = '<p>Waiting for passengers...</p>';
        activeRide.classList.add('hidden');
        mapManager.clearRoute();
      }
    } else {
      seatCounter.classList.add('hidden');
      noRide.classList.remove('hidden');
      noRide.innerHTML = '<p>Go online to start receiving requests</p>';
      activeRide.classList.add('hidden');
      mapManager.clearRoute();
    }
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
