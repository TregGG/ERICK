/**
 * Main application orchestrator
 */
document.addEventListener('DOMContentLoaded', async () => {
    // 1. Init Google Maps
    try {
        await mapManager.initMap();
    } catch(e) {
        console.error("Map initialization failed", e);
    }

    // 2. Auth Check & Profile
    if (!authManager.isLoggedIn()) {
        authManager.showAuthScreen();
    } else {
        wsManager.connect(authManager.getToken());
        checkUserProfile();
    }

    async function checkUserProfile() {
        const profile = await authManager.getProfile();
        if (!profile) {
            authManager.showAuthScreen();
            return;
        }
        const displayName = profile.name || profile.username;
        const badge = document.getElementById('user-display-name');
        if (badge) badge.textContent = `👤 ${displayName}`;

        if (!profile.name || profile.name.startsWith('user_')) {
            document.getElementById('profile-modal').classList.remove('hidden');
            document.getElementById('profile-name').value = profile.name || '';
        }
    }

    // Profile form listener
    document.getElementById('form-profile')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('profile-name').value.trim();
        if (name) {
            const updated = await authManager.updateName(name);
            if (updated) {
                document.getElementById('profile-modal').classList.add('hidden');
                const badge = document.getElementById('user-display-name');
                if (badge) badge.textContent = `👤 ${updated.name}`;
            }
        }
    });

    document.getElementById('user-display-name')?.addEventListener('click', () => {
        document.getElementById('profile-modal').classList.remove('hidden');
    });

    // Pin Picking Mode Controls
    document.getElementById('btn-mode-pickup')?.addEventListener('click', () => mapManager.setMode('pickup'));
    document.getElementById('btn-mode-dropoff')?.addEventListener('click', () => mapManager.setMode('dropoff'));
    document.getElementById('btn-clear-pins')?.addEventListener('click', () => mapManager.clearMarkers());

    // 3. Bind WebSocket Events
    wsManager.onMessage('ride_matched', (data) => rideManager.updateState('matched', data));
    wsManager.onMessage('queue_position', (data) => rideManager.updateState('queued', data));
    wsManager.onMessage('ride_status', (data) => rideManager.updateState(data.status));
    wsManager.onMessage('driver_location', (data) => {
        mapManager.updateDriverMarker(data.lat, data.lng, data.heading);
        rideManager.handleDriverLocation(data);
    });
    wsManager.onMessage('route_updated', (data) => {
        rideManager.handleRouteUpdated(data);
    });

    // 4. Setup Event Listeners

    // Auth Listeners
    document.getElementById('btn-show-register').addEventListener('click', (e) => {
        e.preventDefault();
        document.getElementById('login-view').classList.add('hidden');
        document.getElementById('register-view').classList.remove('hidden');
    });

    document.getElementById('btn-show-login').addEventListener('click', (e) => {
        e.preventDefault();
        document.getElementById('register-view').classList.add('hidden');
        document.getElementById('login-view').classList.remove('hidden');
    });

    let currentPhone = '';

    document.getElementById('form-register').addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('reg-username').value;
        const phone = document.getElementById('reg-phone').value;
        const success = await authManager.register(username, phone);
        if (success) {
            currentPhone = phone;
            await authManager.requestOTP(currentPhone);
            document.getElementById('register-view').classList.add('hidden');
            document.getElementById('otp-view').classList.remove('hidden');
            const firstInput = document.querySelector('.otp-input');
            if (firstInput) firstInput.focus();
        } else {
            alert('Registration failed. Username or phone may already exist.');
        }
    });

    document.getElementById('form-login').addEventListener('submit', async (e) => {
        e.preventDefault();
        currentPhone = document.getElementById('login-phone').value;
        const success = await authManager.requestOTP(currentPhone);
        if (success) {
            document.getElementById('login-view').classList.add('hidden');
            document.getElementById('otp-view').classList.remove('hidden');
            const firstInput = document.querySelector('.otp-input');
            if (firstInput) firstInput.focus();
        } else {
            alert('Failed to request OTP');
        }
    });

    document.getElementById('form-otp').addEventListener('submit', async (e) => {
        e.preventDefault();
        const otpInputs = document.querySelectorAll('.otp-input');
        let otp = '';
        otpInputs.forEach(input => otp += input.value);
        
        if (otp.length === 6) {
            const success = await authManager.verifyOTP(currentPhone, otp);
            if (success) {
                checkUserProfile();
            } else {
                alert('Invalid OTP');
            }
        }
    });

    // OTP auto-focus logic
    const otpInputs = document.querySelectorAll('.otp-input');
    otpInputs.forEach((input, index) => {
        input.addEventListener('keyup', (e) => {
            if (e.key >= 0 && e.key <= 9 && index < otpInputs.length - 1) {
                otpInputs[index + 1].focus();
            } else if (e.key === 'Backspace' && index > 0) {
                otpInputs[index - 1].focus();
            }
        });
    });

    // Menu Listener
    document.getElementById('btn-menu').addEventListener('click', () => {
        if (confirm('Do you want to logout?')) {
            authManager.logout();
        }
    });

    // Map Listeners
    document.getElementById('btn-my-location').addEventListener('click', async () => {
        try {
            const coords = await mapManager.getCurrentLocation();
            if (mapManager.map) {
                mapManager.map.panTo([coords.lat, coords.lng]);
            }
            mapManager.setPickupMarker(coords.lat, coords.lng, "My Location");
        } catch (e) {
            alert("Could not get location. Check permissions.");
        }
    });

    document.getElementById('btn-request-ride').addEventListener('click', () => {
        if (!mapManager.pickupMarker || !mapManager.dropoffMarker) {
            alert("Please select both Pickup (Start) and Dropoff (Stop) locations on the map!");
            return;
        }

        let pLat = mapManager.pickupMarker.getLatLng().lat;
        let pLng = mapManager.pickupMarker.getLatLng().lng;
        let dLat = mapManager.dropoffMarker.getLatLng().lat;
        let dLng = mapManager.dropoffMarker.getLatLng().lng;

        const pickup = { lat: pLat, lng: pLng, name: document.getElementById('input-pickup').value || 'Pickup' };
        const dropoff = { lat: dLat, lng: dLng, name: document.getElementById('input-dropoff').value || 'Dropoff' };
        
        rideManager.requestRide(pickup, dropoff);
    });

    const cancelHandlers = () => rideManager.cancelRide();
    document.querySelectorAll('.btn-cancel-ride').forEach(btn => btn.addEventListener('click', cancelHandlers));
    
    document.getElementById('btn-pay').addEventListener('click', () => {
        alert('Payment successful!');
        rideManager.resetRide();
    });

    // Bottom Sheet Swipe Logic
    const bottomSheet = document.getElementById('bottom-sheet');
    const sheetHandle = document.getElementById('sheet-handle');
    let startY, currentY, isDragging = false;
    
    const setSheetState = (state) => {
        bottomSheet.classList.remove('peek', 'half', 'full');
        bottomSheet.classList.add(state);
    };

    sheetHandle.addEventListener('touchstart', (e) => {
        startY = e.touches[0].clientY;
        isDragging = true;
        bottomSheet.style.transition = 'none';
    }, {passive: true});

    sheetHandle.addEventListener('touchmove', (e) => {
        if (!isDragging) return;
        currentY = e.touches[0].clientY;
        const delta = currentY - startY;
        // Only allow dragging down slightly or pulling up
        bottomSheet.style.transform = `translateY(${delta}px)`;
    }, {passive: true});

    sheetHandle.addEventListener('touchend', () => {
        if (!isDragging) return;
        isDragging = false;
        bottomSheet.style.transition = 'transform 0.3s ease-out';
        bottomSheet.style.transform = ''; 

        if (currentY - startY > 50) {
            setSheetState('peek');
        } else if (startY - currentY > 50) {
            setSheetState('full');
        } else {
            setSheetState('half');
        }
    });

    // Initialize UI state
    rideManager.updateState('idle');
});

// Load service worker
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').then(reg => {
            console.log('SW registered: ', reg.scope);
        }).catch(err => console.log('SW registration failed: ', err));
    });
}
