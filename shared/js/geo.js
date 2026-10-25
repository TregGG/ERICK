// Browser Geolocation wrapper. Reports real readings and explicit error states;
// it never substitutes a fabricated position when GPS is unavailable.

const ERRORS = {
  1: { code: 'denied', message: 'Location permission is blocked. Allow location for this site in your browser settings.' },
  2: { code: 'unavailable', message: 'Your device cannot determine its location right now. Check that GPS/location services are on.' },
  3: { code: 'timeout', message: 'Waiting for a GPS fix… Move to an open area if this continues.' },
};

export function geolocationSupported() {
  return 'geolocation' in navigator && window.isSecureContext;
}

export function unsupportedReason() {
  if (!('geolocation' in navigator)) return 'This browser does not support location.';
  if (!window.isSecureContext) return 'Location needs a secure (https) connection. Open the app over https or on localhost.';
  return null;
}

export async function permissionState() {
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' });
    return status.state; // granted | prompt | denied
  } catch {
    return 'unknown';
  }
}

export function toReading(position) {
  const c = position.coords;
  return {
    lat: c.latitude, lng: c.longitude, accuracy_m: Math.max(1, Math.round(c.accuracy * 10) / 10),
    heading: Number.isFinite(c.heading) && c.heading !== null && c.heading >= 0 && c.heading < 360 ? c.heading : null,
    speed_mps: Number.isFinite(c.speed) && c.speed !== null && c.speed >= 0 ? c.speed : null,
    timestamp: new Date(position.timestamp || Date.now()).toISOString(),
    source: 'gps',
  };
}

export class GeoWatcher {
  constructor({ onReading, onError }) {
    this.onReading = onReading;
    this.onError = onError;
    this.watchId = null;
  }
  get active() { return this.watchId !== null; }
  start() {
    if (this.active) return;
    const reason = unsupportedReason();
    if (reason) { this.onError({ code: 'unsupported', message: reason }); return; }
    this.watchId = navigator.geolocation.watchPosition(
      (pos) => this.onReading(toReading(pos)),
      (err) => this.onError(ERRORS[err.code] || { code: 'error', message: err.message }),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 },
    );
  }
  stop() {
    if (this.watchId !== null) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = null;
  }
}

export function currentPosition() {
  return new Promise((resolve, reject) => {
    const reason = unsupportedReason();
    if (reason) { reject({ code: 'unsupported', message: reason }); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve(toReading(pos)),
      (err) => reject(ERRORS[err.code] || { code: 'error', message: err.message }),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 },
    );
  });
}
