class Auth {
  constructor() {
    this.token = localStorage.getItem('driver_token');
  }
  
  isAuthenticated() {
    return !!this.token;
  }

  async requestOTP(phone) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/request-otp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone })
      });
      return res.ok;
    } catch (e) {
      console.error(e);
      return false;
    }
  }

  async verifyOTP(phone, otp) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/verify-otp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, otp, role: 'driver' })
      });
      if (res.ok) {
        const data = await res.json();
        this.token = data.token;
        localStorage.setItem('driver_token', this.token);
        return true;
      }
      return false;
    } catch (e) {
      console.error(e);
      return false;
    }
  }

  async registerVehicle(vehicleNumber) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/register-rickshaw`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`
        },
        body: JSON.stringify({ vehicle_number: vehicleNumber })
      });
      return res.ok;
    } catch (e) {
      console.error(e);
      return false;
    }
  }

  logout() {
    this.token = null;
    localStorage.removeItem('driver_token');
    window.location.reload();
  }
}

const auth = new Auth();
