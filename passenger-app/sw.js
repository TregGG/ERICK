// Minimal service worker: makes the passenger app installable (PWA / Android TWA) and shows a
// clear offline message instead of the browser's error page. It caches nothing, so every page
// and API call always comes fresh from the server.
const OFFLINE_HTML = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campus Rick</title><body style="font-family:system-ui,sans-serif;padding:2rem;text-align:center;color:#15201b">
<h1 style="font-size:1.3rem">You are offline</h1><p>Campus Rick needs an internet connection. Check your data or Wi-Fi and try again.</p>
<p><button onclick="location.reload()" style="padding:.6rem 1.2rem;font-size:1rem">Try again</button></p></body>`;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() =>
    new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
