/**
 * Authentication module
 */
class AuthManager {
    constructor() {
        this.tokenKey = 'campus_rick_token';
    }

    /**
     * Retrieves the stored JWT token
     * @returns {string|null} The token or null if not found
     */
    getToken() {
        return localStorage.getItem(this.tokenKey);
    }

    /**
     * Checks if the stored JWT is expired
     * @returns {boolean} True if expired or missing
     */
    isTokenExpired() {
        const token = this.getToken();
        if (!token) return true;
        try {
            const parts = token.split('.');
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

    /**
     * Checks if the user is logged in
     * @returns {boolean} True if logged in
     */
    isLoggedIn() {
        if (!this.getToken()) return false;
        if (this.isTokenExpired()) {
            this.logout();
            return false;
        }
        return true;
    }

    /**
     * Clears authentication and reloads/redirects
     */
    logout() {
        localStorage.removeItem(this.tokenKey);
        if (typeof wsManager !== 'undefined' && wsManager.disconnect) {
            wsManager.disconnect();
        }
    }

    /**
     * Shows the authentication UI
     */
    showAuthScreen() {
        document.getElementById('auth-modal').classList.remove('hidden');
        document.getElementById('register-view').classList.add('hidden');
        document.getElementById('login-view').classList.remove('hidden');
        document.getElementById('otp-view').classList.add('hidden');
    }

    /**
     * Registers a new user
     * @param {string} username 
     * @param {string} phone 
     * @returns {Promise<boolean>} Success status
     */
    async register(username, phone) {
        try {
            const response = await fetch(`${CONFIG.API_BASE_URL}/api/auth/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, phone, role: 'passenger' })
            });
            return response.ok;
        } catch (error) {
            console.error('Registration failed', error);
            return false;
        }
    }

    /**
     * Requests an OTP for login
     * @param {string} phone 
     * @returns {Promise<boolean>} Success status
     */
    async requestOTP(phone) {
        try {
            const response = await fetch(`${CONFIG.API_BASE_URL}/api/auth/request-otp`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ phone, role: 'passenger' })
            });
            if (response.ok) {
                const data = await response.json();
                if (data.debug_otp) {
                    alert(`OTP is: ${data.debug_otp}`);
                    console.log('OTP:', data.debug_otp);
                }
                return true;
            }
            return false;
        } catch (error) {
            console.error('OTP request failed', error);
            return false;
        }
    }

    /**
     * Verifies the OTP and stores the token
     * @param {string} phone 
     * @param {string} otp 
     * @returns {Promise<boolean>} Success status
     */
    async verifyOTP(phone, otp) {
        try {
            const response = await fetch(`${CONFIG.API_BASE_URL}/api/auth/verify-otp`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ phone, otp, role: 'passenger' })
            });
            if (response.ok) {
                const data = await response.json();
                const token = data.token || data.access_token;
                localStorage.setItem(this.tokenKey, token);
                document.getElementById('auth-modal').classList.add('hidden');
                wsManager.connect(token);
                return true;
            }
            return false;
        } catch (error) {
            console.error('OTP verification failed', error);
            return false;
        }
    }

    /**
     * Gets the current user profile
     */
    async getProfile() {
        const token = this.getToken();
        if (!token) return null;
        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (res.status === 401) {
                this.logout();
                return null;
            }
            if (res.ok) {
                return await res.json();
            }
            return null;
        } catch (e) {
            console.error('Failed to get profile', e);
            return null;
        }
    }

    /**
     * Updates user name
     * @param {string} name 
     */
    async updateName(name) {
        const token = this.getToken();
        if (!token) return null;
        try {
            const res = await fetch(`${CONFIG.API_BASE_URL}/api/auth/profile`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({ name })
            });
            if (res.status === 401) {
                this.logout();
                return null;
            }
            if (res.ok) {
                return await res.json();
            }
            return null;
        } catch (e) {
            console.error('Failed to update name', e);
            return null;
        }
    }
}

const authManager = new AuthManager();
