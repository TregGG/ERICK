/**
 * Application Configuration
 */
const origin = window.location.origin;
const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsHost = window.location.host;

const CONFIG = {
    API_BASE_URL: origin,
    WS_BASE_URL: `${wsProto}//${wsHost}`,
    DEFAULT_CENTER: { lat: 28.6139, lng: 77.2090 },
    DEFAULT_ZOOM: 16
};
