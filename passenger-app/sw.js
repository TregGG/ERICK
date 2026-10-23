const CACHE_NAME = 'rick-passenger-v2';

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  // Always network-first in development so file updates apply immediately
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
