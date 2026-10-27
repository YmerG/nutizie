// Nutizie hors ligne : toujours le réseau d'abord (actualité fraîche), la dernière copie si le réseau manque.
var CACHE = 'nutizie-1';

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return c.addAll(['./', 'style.css', 'app.js', 'manifest.webmanifest', 'icones/icone-192.png']);
  }));
  self.skipWaiting();
});

self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('fetch', function (e) {
  // images des médias et autres sites : jamais copiées, le navigateur les demande directement
  if (e.request.method !== 'GET' || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(function (r) {
    if (r.ok) {
      var copie = r.clone();
      caches.open(CACHE).then(function (c) { c.put(e.request, copie); });
    }
    return r;
  }).catch(function () {
    return caches.match(e.request, { ignoreSearch: true }).then(function (r) {
      return r || (e.request.mode === 'navigate' ? caches.match('./') : Response.error());
    });
  }));
});
