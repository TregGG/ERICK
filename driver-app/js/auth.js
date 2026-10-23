class Auth {
  constructor() {
    this.token = localStorage.getItem('driver_token');
  }
  
  isTokenExpired() {
    if (!this.token) return true;
    try {
      const parts = this.token.split('.');
      if (parts.length !== 3) return true;
      const payload = JSON.parse(atob(parts[1]));
      if (payload.exp && payload.exp * 1000 < Date.now()) {
        return true;
      }
      return false;
    } catch (e) {
      return true;
    }
  }

  isAuthenticated() {
    if (!this.token) return false;
    if (this.isTokenExpired()) {
      this.token = null;
      localStorage.removeItem('driver_token');
      return false;
    }
    return true;
  }

  async requestOTP(phone) {
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/request-otp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, role: 'driver' })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.debug_otp) {
          alert(`Driver OTP is: ${data.debug_otp}`);
          console.log('Driver OTP:', data.debug_otp);
        }
        return true;
      }
      return false;
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
        this.token = data.token || data.access_token;
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
      if (res.status === 401) {
        this.token = null;
        localStorage.removeItem('driver_token');
        return false;
      }
      return res.ok;
    } catch (e) {
      console.error(e);
      return false;
    }
  }

  async getMyRickshaw() {
    if (!this.token) return null;
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/driver/my-rickshaw`, {
        headers: { 'Authorization': `Bearer ${this.token}` }
      });
      if (res.status === 401) {
        this.token = null;
        localStorage.removeItem('driver_token');
        return null;
      }
      if (res.ok) {
        return await res.json();
      }
      return null;
    } catch (e) {
      console.error(e);
      return null;
    }
  }

  async getProfile() {
    if (!this.token) return null;
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/me`, {
        headers: { 'Authorization': `Bearer ${this.token}` }
      });
      if (res.status === 401) {
        this.token = null;
        localStorage.removeItem('driver_token');
        return null;
      }
      if (res.ok) {
        return await res.json();
      }
      return null;
    } catch (e) {
      console.error(e);
      return null;
    }
  }

  async updateName(name) {
    if (!this.token) return null;
    try {
      const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/profile`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.token}`
        },
        body: JSON.stringify({ name })
      });
      if (res.status === 401) {
        this.token = null;
        localStorage.removeItem('driver_token');
        return null;
      }
      if (res.ok) {
        return await res.json();
      }
      return null;
    } catch (e) {
      console.error(e);
      return null;
    }
  }

  logout() {
    this.token = null;
    localStorage.removeItem('driver_token');
    window.location.reload();
  }
}

const auth = new Auth();
