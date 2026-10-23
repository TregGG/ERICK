document.addEventListener('DOMContentLoaded', () => {
  // Navigation helper
  const showScreen = (id) => {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    if (id !== 'main-screen') {
      document.getElementById('seat-counter')?.classList.add('hidden');
      document.getElementById('request-popup')?.classList.add('hidden');
    }
  };

  // Auth & Profile Logic
  if (!auth.isAuthenticated()) {
    showScreen('auth-screen');
  } else {
    checkDriverProfile();
  }

  async function checkDriverProfile() {
    const profile = await auth.getProfile();
    if (!profile) {
      auth.logout();
      showScreen('auth-screen');
      return;
    }
    const rickshawData = await auth.getMyRickshaw();

    const hasName = profile && profile.name && !profile.name.startsWith('user_') && !profile.name.startsWith('driver_');
    const hasVehicle = rickshawData && rickshawData.registered;

    const nameEl = document.getElementById('driver-display-name');
    if (nameEl) nameEl.textContent = profile.name || profile.username;

    if (!hasName || !hasVehicle) {
      showScreen('setup-screen');
      // Pre-fill existing data if any
      const nameInput = document.getElementById('driver-name-input');
      const vInput = document.getElementById('vehicle-number');
      if (nameInput && profile?.name) nameInput.value = profile.name;
      if (vInput && rickshawData?.rickshaw?.vehicle_number) vInput.value = rickshawData.rickshaw.vehicle_number;
    } else {
      initMainScreen(profile, rickshawData.rickshaw);
    }
  }

  // Auth UI events
  const handleRequestOtp = async () => {
    const phone = document.getElementById('auth-phone').value.trim();
    if (!phone) {
      alert('Please enter your phone number first.');
      return;
    }
    const ok = await auth.requestOTP(phone);
    if (ok) {
      document.getElementById('auth-phone-step').classList.add('hidden');
      document.getElementById('auth-otp-step').classList.remove('hidden');
      const first = document.querySelector('.otp-box');
      if (first) first.focus();
    } else {
      alert('Failed to request OTP. Check phone number and backend connection.');
    }
  };

  document.getElementById('auth-request-otp').addEventListener('click', handleRequestOtp);
  document.getElementById('auth-phone').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleRequestOtp();
  });

  // OTP input auto-advance
  document.querySelectorAll('.otp-box').forEach((input, index, inputs) => {
    input.addEventListener('input', () => {
      if (input.value && index < inputs.length - 1) inputs[index + 1].focus();
    });
  });

  document.getElementById('auth-verify-otp').addEventListener('click', async () => {
    const phone = document.getElementById('auth-phone').value.trim();
    const otp = Array.from(document.querySelectorAll('.otp-box')).map(i => i.value).join('');
    if (await auth.verifyOTP(phone, otp)) {
      await checkDriverProfile();
    } else {
      alert('Invalid or expired OTP. Please try again.');
    }
  });

  // Unified Setup Submit
  document.getElementById('setup-submit').addEventListener('click', async () => {
    const name = document.getElementById('driver-name-input').value.trim();
    const vNum = document.getElementById('vehicle-number').value.trim();

    if (!name) {
      alert('Please enter your full name.');
      return;
    }
    if (!vNum) {
      alert('Please enter your vehicle number.');
      return;
    }

    if (!auth.isAuthenticated()) {
      alert('Session expired. Please log in again.');
      showScreen('auth-screen');
      return;
    }

    const nameOk = await auth.updateName(name);
    const vehicleOk = await auth.registerVehicle(vNum);

    if (nameOk && vehicleOk) {
      localStorage.setItem('vehicle_registered', 'true');
      await checkDriverProfile();
    } else {
      if (!auth.isAuthenticated()) {
        alert('Session expired. Please log in again.');
        showScreen('auth-screen');
      } else {
        alert('Error saving profile or vehicle. Please try again.');
      }
    }
  });

  document.getElementById('setup-logout')?.addEventListener('click', () => {
    auth.logout();
  });

  // Initialize Main Screen
  function initMainScreen(profile, rickshaw) {
    showScreen('main-screen');
    
    // Initialize Leaflet map
    mapManager.init('map-container');

    // Connect WebSocket
    wsClient.connect();
    
    // Load active ride state
    rideManager.getActiveRide();

    // Toggle Online/Offline initialization
    const toggle = document.getElementById('online-toggle');
    const isInitiallyOnline = !!(rickshaw && rickshaw.is_active);
    toggle.checked = isInitiallyOnline;
    rideManager.isOnline = isInitiallyOnline;
    rideManager.updateUI();
    if (isInitiallyOnline) {
      locationTracker.startTracking();
    }

    toggle.addEventListener('change', (e) => {
      rideManager.setOnlineStatus(e.target.checked);
    });

    // Request Popup actions
    document.getElementById('btn-drive-step')?.addEventListener('click', () => {
      mapManager.driveCloser();
    });

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

    // Settings Modal
    document.getElementById('settings-btn').addEventListener('click', () => {
      const nameInput = document.getElementById('settings-name-input');
      const vInput = document.getElementById('settings-vehicle-input');
      const detourInput = document.getElementById('detour-slider');
      const detourVal = document.getElementById('detour-val');

      if (nameInput && profile?.name) nameInput.value = profile.name;
      if (vInput && rickshaw?.vehicle_number) vInput.value = rickshaw.vehicle_number;
      if (detourInput && rickshaw?.max_detour_meters) {
        detourInput.value = rickshaw.max_detour_meters;
        if (detourVal) detourVal.textContent = rickshaw.max_detour_meters;
      }

      document.getElementById('settings-modal').classList.remove('hidden');
    });

    document.getElementById('settings-close').addEventListener('click', async () => {
      const newName = document.getElementById('settings-name-input')?.value.trim();
      const newVNum = document.getElementById('settings-vehicle-input')?.value.trim();
      const detourMeters = parseInt(document.getElementById('detour-slider')?.value || '500');

      if (newName) {
        await auth.updateName(newName);
        profile.name = newName;
        const nameEl = document.getElementById('driver-display-name');
        if (nameEl) nameEl.textContent = newName;
      }

      if (newVNum) {
        await auth.registerVehicle(newVNum);
        if (rickshaw) rickshaw.vehicle_number = newVNum;
      }

      try {
        await fetch(`${CONFIG.API_BASE_URL}/api/driver/settings`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${auth.token}`
          },
          body: JSON.stringify({ max_detour_meters: detourMeters })
        });
        if (rickshaw) rickshaw.max_detour_meters = detourMeters;
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

    // Page visibility (resume tracking if active)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && rideManager.isOnline) {
        locationTracker.startTracking();
      }
    });
  }

  // Register Service Worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(err => console.log('SW registration failed: ', err));
    });
  }
});
