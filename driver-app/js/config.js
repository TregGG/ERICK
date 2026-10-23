const origin = window.location.origin;
const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsHost = window.location.host;

const CONFIG = {
  API_BASE_URL: origin,
  WS_BASE_URL: `${wsProto}//${wsHost}`,
  CAMPUS_CENTER: { lat: 28.6139, lng: 77.2090 },
  LOCATION_UPDATE_INTERVAL: 3000,
  REQUEST_TIMEOUT: 30000 // 30s
};
