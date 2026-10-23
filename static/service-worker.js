const CACHE = "ihki-shell-v22";
const SHELL = [
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png?v=2",
  "/static/icons/icon-512.png?v=2",
  "/static/icons/icon-maskable-512.png?v=2",
  "/static/logo-intro.svg",
  "/static/logo-math.svg",
  "/static/assets/arabic-calligraphy-path.webp"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/static/")) return;
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {
      if (response.ok) {
        const copy = response.clone();   // cloned now: the page reads the body before the cache opens
        caches.open(CACHE).then(cache => cache.put(event.request, copy));
      }
      return response;
    }))
  );
});

// the daily video's morning reminder (app/push.py)
self.addEventListener("push", event => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch { msg = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(msg.title || "אהלא וסהלא", {
    body: msg.body || "", tag: msg.tag || "ihki", lang: "he", dir: "rtl",
    icon: "/static/icons/icon-192.png?v=2", badge: "/static/icons/icon-192.png?v=2",
    data: { url: msg.url || "/" }
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(wins => {
    const open = wins.find(w => new URL(w.url).origin === self.location.origin);
    return open ? open.navigate(url).then(w => (w || open).focus()) : self.clients.openWindow(url);
  }));
});
