/**
 * Flowrex Algo — minimal service worker (PWA install support).
 *
 * Scope is intentionally narrow: we want the "Install app" prompt + standalone
 * window launch. We do NOT cache authenticated app HTML / API responses —
 * trading data must be live. Only the public shell (manifest, icons) is
 * network-first with a tiny cache fallback.
 */
const CACHE_NAME = "flowrex-shell-v1";
const SHELL_ASSETS = [
  "/logo-icon.png",
  "/logo-full.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  // Never intercept API calls — trading data must always hit the network.
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return;

  // Network-first for everything else; fall back to shell cache offline.
  event.respondWith(
    fetch(request).catch(() => caches.match(request))
  );
});
