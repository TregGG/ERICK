document.addEventListener('DOMContentLoaded', () => {
  // Navigation
  const showScreen = (id) => {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
  };

  // Auth Logic
  if (!auth.isAuthenticated()) {
    showScreen('auth-screen');
  } else {
    // Check if vehicle registered (mock check, ideally from API)
    const hasVehicle = localStorage.getItem('vehicle_registered');
    if (!hasVehicle) {
      showScreen('setup-screen');
    } else {
      initMainScreen();
    }
  }

  // Auth UI events
  document.getElementById('auth-request-otp').addEventListener('click', async () => {
    const phone = document.getElementById('auth-phone').value;
    if (phone) {
      await auth.requestOTP(phone);
      document.getElementById('auth-phone-step').classList.add('hidden');
      document.getElementById('auth-otp-step').classList.remove('hidden');
    }
  });

  // OTP input auto-advance
  document.querySelectorAll('.otp-box').forEach((input, index, inputs) => {
    input.addEventListener('input', () => {
      if (input.value && index < inputs.length - 1) inputs[index + 1].focus();
    });
  });

  document.getElementById('auth-verify-otp').addEventListener('click', async () => {
    const phone = document.getElementById('auth-phone').value;
    const otp = Array.from(document.querySelectorAll('.otp-box')).map(i => i.value).join('');
    if (await auth.verifyOTP(phone, otp)) {
      showScreen('setup-screen');
    }
  });

  // Setup UI events
  document.getElementById('setup-submit').addEventListener('click', async () => {
    const vNum = document.getElementById('vehicle-number').value;
    if (vNum) {
      // API call to register
      await auth.registerVehicle(vNum);
      localStorage.setItem('vehicle_registered', 'true');
      initMainScreen();
    }
  });

  // Initialize Main Screen
  function initMainScreen() {
    showScreen('main-screen');
    
    // Load Maps script (mock API key)
    const script = document.createElement('script');
    script.src = `https://maps.googleapis.com/maps/api/js?key=${CONFIG.GOOGLE_MAPS_API_KEY}&callback=initMap`;
    script.async = true;
    script.defer = true;
    window.initMap = () => mapManager.init('map-container');
    document.head.appendChild(script);

    // Connect WS
    wsClient.connect();
    
    // Load ride state
    rideManager.getActiveRide();

    // Toggle Online/Offline
    const toggle = document.getElementById('online-toggle');
    toggle.addEventListener('change', (e) => {
      rideManager.setOnlineStatus(e.target.checked);
    });

    // Request Popup actions
    document.getElementById('req-accept').addEventListener('click', () => {
      if (rideManager.currentRequest) {
        rideManager.acceptPassenger(rideManager.currentRequest.request_id);
      }
    });

    document.getElementById('req-reject').addEventListener('click', () => {
      if (rideManager.currentRequest) {
        rideManager.rejectPassenger(rideManager.currentRequest.request_id);
      }
    });

    // Settings
    document.getElementById('settings-btn').addEventListener('click', () => {
      document.getElementById('settings-modal').classList.remove('hidden');
    });

    document.getElementById('settings-close').addEventListener('click', async () => {
      const val = document.getElementById('detour-slider').value;
      try {
        await fetch(`${CONFIG.API_BASE_URL}/api/driver/settings`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${auth.token}`
          },
          body: JSON.stringify({ max_detour_meters: parseInt(val) })
        });
      } catch (e) {
        console.error(e);
      }
      document.getElementById('settings-modal').classList.add('hidden');
    });

    document.getElementById('detour-slider').addEventListener('input', (e) => {
      document.getElementById('detour-val').textContent = e.target.value;
    });

    document.getElementById('logout-btn').addEventListener('click', () => {
      auth.logout();
    });

    // Page visibility (pause tracking in background if needed)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && rideManager.isOnline) {
        locationTracker.startTracking();
      }
    });
  }

  // Register SW
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(err => console.log('SW registration failed: ', err));
    });
  }
});
