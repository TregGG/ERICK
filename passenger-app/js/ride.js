/**
 * Ride management module
 */
class RideManager {
    constructor() {
        this.state = 'idle'; // idle, requesting, queued, matched, pickup_enroute, picked_up, completed
        this.currentRideId = null;
        this.pickup = null;
        this.dropoff = null;
    }

    /**
     * Requests a new ride
     * @param {Object} pickup {lat, lng, name}
     * @param {Object} dropoff {lat, lng, name}
     */
    async requestRide(pickup, dropoff) {
        this.pickup = pickup;
        this.dropoff = dropoff;
        this.updateState('requesting');

        try {
            const response = await fetch(`${CONFIG.API_BASE_URL}/api/passenger/ride-request`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authManager.getToken()}`
                },
                body: JSON.stringify({
                    pickup_lat: pickup.lat,
                    pickup_lng: pickup.lng,
                    pickup_name: pickup.name,
                    dropoff_lat: dropoff.lat,
                    dropoff_lng: dropoff.lng,
                    dropoff_name: dropoff.name
                })
            });

            if (response.ok) {
                const data = await response.json();
                this.currentRideId = data.request_id;
                this.updateState('queued');
            } else {
                throw new Error('Failed to request ride');
            }
        } catch (error) {
            console.error(error);
            this.updateState('idle');
            alert('Failed to request ride. Please try again.');
        }
    }

    /**
     * Cancels the current ride
     */
    async cancelRide() {
        if (!this.currentRideId) {
            this.resetRide();
            return;
        }

        try {
            await fetch(`${CONFIG.API_BASE_URL}/api/passenger/cancel/${this.currentRideId}`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authManager.getToken()}` }
            });
            this.resetRide();
        } catch (error) {
            console.error('Error canceling ride', error);
            this.resetRide();
        }
    }

    /**
     * Fetches ride history
     */
    async getRideHistory() {
        try {
            const response = await fetch(`${CONFIG.API_BASE_URL}/api/passenger/ride-history`, {
                headers: { 'Authorization': `Bearer ${authManager.getToken()}` }
            });
            if (response.ok) {
                return await response.json();
            }
            return [];
        } catch (error) {
            console.error('Error fetching history', error);
            return [];
        }
    }

    /**
     * Updates internal state and triggers UI updates
     * @param {string} newState 
     * @param {Object} data Additional context
     */
    updateState(newState, data = {}) {
        this.state = newState;
        
        // Hide all bottom sheet states
        document.querySelectorAll('.sheet-state').forEach(el => el.classList.add('hidden'));

        // Reset bottom sheet to half view on state change
        const sheet = document.getElementById('bottom-sheet');
        sheet.classList.remove('peek', 'full');
        sheet.classList.add('half');

        switch(newState) {
            case 'idle':
                document.getElementById('state-idle').classList.remove('hidden');
                mapManager.clearMarkers();
                break;
            case 'requesting':
                document.getElementById('state-loading').classList.remove('hidden');
                document.getElementById('loading-text').innerText = 'Finding you a rick...';
                break;
            case 'queued':
                document.getElementById('state-queued').classList.remove('hidden');
                if (data.position !== undefined) {
                    document.getElementById('queue-position').innerText = `#${data.position}`;
                }
                break;
            case 'matched':
                document.getElementById('state-matched').classList.remove('hidden');
                document.getElementById('driver-name').innerText = data.driver_name || 'Driver';
                document.getElementById('vehicle-number').innerText = data.vehicle_number || 'Vehicle';
                document.getElementById('eta').innerText = `ETA: ${data.eta_minutes || '?'} min`;
                if (data.ride_id) this.currentRideId = data.ride_id;
                break;
            case 'pickup_enroute':
                document.getElementById('state-enroute').classList.remove('hidden');
                document.getElementById('enroute-msg').innerText = 'Driver is on the way to your pickup.';
                break;
            case 'picked_up':
                document.getElementById('state-enroute').classList.remove('hidden');
                document.getElementById('enroute-msg').innerText = "You're on your way!";
                if (this.pickup && this.dropoff) {
                     mapManager.drawRoute(
                         {lat: this.pickup.lat, lng: this.pickup.lng}, 
                         {lat: this.dropoff.lat, lng: this.dropoff.lng}
                     );
                }
                break;
            case 'completed':
                document.getElementById('state-completed').classList.remove('hidden');
                break;
        }
    }

    resetRide() {
        this.currentRideId = null;
        this.pickup = null;
        this.dropoff = null;
        this.updateState('idle');
    }
}

const rideManager = new RideManager();
