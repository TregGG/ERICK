/**
 * WebSocket communication manager
 */
class WebSocketManager {
    constructor() {
        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectDelay = 30000;
        this.handlers = new Map();
        this.token = null;
    }

    /**
     * Connects to the WebSocket server
     * @param {string} token - The authentication token
     */
    connect(token) {
        this.token = token;
        const wsUrl = `${CONFIG.WS_BASE_URL}/ws/${token}`;
        
        try {
            this.socket = new WebSocket(wsUrl);

            this.socket.onopen = () => {
                console.log('WebSocket connected');
                this.reconnectAttempts = 0;
                this._updateStatusIndicator(true);
            };

            this.socket.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    this._handleMessage(message);
                } catch (error) {
                    console.error('Error parsing WS message:', error);
                }
            };

            this.socket.onclose = () => {
                console.log('WebSocket disconnected');
                this._updateStatusIndicator(false);
                this._scheduleReconnect();
            };

            this.socket.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (error) {
            console.error('Failed to establish WebSocket:', error);
            this._scheduleReconnect();
        }
    }

    /**
     * Registers a message handler for a specific message type
     * @param {string} type - Message type
     * @param {Function} handler - Callback function
     */
    onMessage(type, handler) {
        if (!this.handlers.has(type)) {
            this.handlers.set(type, []);
        }
        this.handlers.get(type).push(handler);
    }

    /**
     * Disconnects the WebSocket
     */
    disconnect() {
        if (this.socket) {
            this.socket.onclose = null;
            this.socket.close();
            this.socket = null;
            this._updateStatusIndicator(false);
        }
    }

    _handleMessage(message) {
        if (message && message.type && this.handlers.has(message.type)) {
            const handlers = this.handlers.get(message.type);
            handlers.forEach(handler => handler(message.data));
        }
    }

    _scheduleReconnect() {
        if (!this.token) return;
        
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), this.maxReconnectDelay);
        this.reconnectAttempts++;
        
        console.log(`Scheduling reconnect in ${delay}ms...`);
        setTimeout(() => this.connect(this.token), delay);
    }

    _updateStatusIndicator(isConnected) {
        const indicator = document.getElementById('ws-status-indicator');
        if (indicator) {
            indicator.className = isConnected ? 'status-connected' : 'status-disconnected';
        }
    }
}

const wsManager = new WebSocketManager();
