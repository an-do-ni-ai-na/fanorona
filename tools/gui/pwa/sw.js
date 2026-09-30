// Service worker de l'interface Fanorona (PWA).
// Coquille (page, textes, leçons, puzzles, icônes) : réseau d'abord, cache en secours — une mise à jour déployée
// est vue au prochain chargement, et l'application s'ouvre encore si le serveur est injoignable.
// API (/api/…) : jamais mise en cache (le moteur est côté serveur). Polices Google : cache d'abord.
const CACHE = "fanorona-v1";
const SHELL = ["/", "/i18n.json", "/lessons.json", "/puzzles.json", "/manifest.webmanifest",
  "/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png", "/icons/favicon-32.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (url.origin === location.origin && url.pathname.startsWith("/api/")) return;
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    e.respondWith(caches.open(CACHE).then(async c => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok || res.type === "opaque") c.put(req, res.clone());
      return res;
    }));
    return;
  }
  if (url.origin !== location.origin) return;
  // Navigation : toujours la page d'accueil de l'application (paramètres et ancre #g= gérés par la page).
  const key = req.mode === "navigate" ? "/" : url.pathname;
  e.respondWith(fetch(req).then(res => {
    if (res.ok) caches.open(CACHE).then(c => c.put(key, res.clone()));
    return res;
  }).catch(() => caches.match(key).then(hit => hit || Response.error())));
});
