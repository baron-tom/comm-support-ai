const CACHE = "comm-ai-v2";
const STATIC = ["/", "/index.html"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

const API_PATHS = ["/summarize", "/chat", "/ask", "/reset", "/extract_todos", "/config"];

self.addEventListener("fetch", e => {
  if (API_PATHS.some(p => e.request.url.includes(p))) return;
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
