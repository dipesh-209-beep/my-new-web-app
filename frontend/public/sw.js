// Service worker for the Kathmandu Bus Route Finder PWA.
//
// Scope, deliberately narrow:
//   1. App shell (the pages/JS/CSS Next.js builds) -- network-first, falling
//      back to the cache (and, for navigations, a cached /offline page) when
//      the network is unavailable, so the app still opens with no network.
//   2. Three read-only, slow-changing backend GETs -- /stops, /routes,
//      /congestion -- stale-while-revalidate, but bounded. A cached copy
//      younger than API_MAX_AGE_MS is served immediately while a refresh runs
//      in the background; an older one is revalidated first; the cache is
//      capped at MAX_API_CACHE_ENTRIES. These are exactly the three endpoints
//      the backend itself already puts a TTL cache in front of, so treating
//      them as cacheable here mirrors a decision already made about this
//      data's freshness requirements. When a stale copy is actually served,
//      the page is told via postMessage so it can say so.
//
// Deliberately NOT cached: /route-finder, /walking-route, and
// /routes/{id}/geometry. All three do live OSRM-dependent work (or, for
// route-finder, encode a live pathfinding result) -- serving a stale
// cached response for any of them would show the user a wrong route or
// wrong walking directions with no indication it's stale. Network-only.

const CACHE_VERSION = "v1";
const SHELL_CACHE = `ktm-bus-shell-${CACHE_VERSION}`;
const API_CACHE = `ktm-bus-api-${CACHE_VERSION}`;

const CACHEABLE_API_PATHS = ["/stops", "/routes", "/congestion"];

// Freshness and size bounds for the API cache. 30 minutes is far longer than
// the backend's own TTLs (this data changes on the order of days), so the
// point is not to match them but to put a ceiling on how stale a served list
// can be before the worker insists on the network first. The entry cap stops
// paged /stops and per-bucket /congestion query strings from growing the
// cache without limit; the oldest entries are dropped first.
const API_MAX_AGE_MS = 30 * 60 * 1000;
const MAX_API_CACHE_ENTRIES = 50;

// X-SW-* headers are internal cache metadata, not values from the API.
// Access-Control-Expose-Headers is re-declared on a synthesized response so
// the page can read them even though the API is a different origin.
const CACHED_AT_HEADER = "x-sw-cached-at";
const STALE_HEADER = "x-sw-stale";
const CACHE_STATUS_MESSAGE = "ktm-bus-cache-status";

const OFFLINE_URL = "/offline";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll([OFFLINE_URL])).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== SHELL_CACHE && key !== API_CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

function isCacheableApiRequest(url) {
  // /stops/nearby and /stops/{id}/routes etc. are prefix matches on
  // "/stops" but are per-location or per-id lookups, not the kind of
  // broad, repeatedly-fetched list data worth caching -- only cache exact
  // path matches (ignoring query string), same set the backend caches.
  return CACHEABLE_API_PATHS.includes(url.pathname);
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(API_CACHE);
  const cached = await cache.match(request);
  const cachedAt = cached ? Number(cached.headers.get(CACHED_AT_HEADER) || 0) : 0;
  const isStale = cached ? Date.now() - cachedAt > API_MAX_AGE_MS : true;

  const networkFetch = fetch(request)
    .then(async (response) => {
      if (response.ok) {
        await putWithTimestamp(cache, request, response.clone());
        await trimApiCache(cache);
      }
      return response;
    })
    .catch(() => null);

  if (cached && !isStale) {
    // Fresh enough: return the cached copy immediately and revalidate in the
    // background. Tell the page it is *not* stale so a notice from an earlier
    // request clears.
    void networkFetch;
    void notifyCacheStatus(false, cachedAt);
    return markCachedResponse(cached, cachedAt, false);
  }

  // No cached copy, or one older than the TTL: prefer the network.
  const fresh = await networkFetch;
  if (fresh) {
    void notifyCacheStatus(false, Date.now());
    return fresh;
  }

  if (cached) {
    // Offline and only a stale copy exists: serve it, but say so.
    void notifyCacheStatus(true, cachedAt);
    return markCachedResponse(cached, cachedAt, true);
  }

  throw new Error("No cached response and network fetch failed");
}

/**
 * Store a response with the time it was cached, so its age is knowable on a
 * later match. A received Response's own headers are immutable, so the copy
 * that goes into the cache is rebuilt with an extra header.
 */
async function putWithTimestamp(cache, request, response) {
  const headers = new Headers(response.headers);
  headers.set(CACHED_AT_HEADER, String(Date.now()));
  const body = await response.blob();
  await cache.put(
    request,
    new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    })
  );
}

/**
 * Rebuild a cached response for delivery, stamping it with its cached-at time
 * and whether it is being served stale. `cached` is consumed to read its
 * body; the cache entry itself is untouched, so later matches still work.
 */
async function markCachedResponse(cached, cachedAt, stale) {
  const headers = new Headers(cached.headers);
  headers.set(CACHED_AT_HEADER, String(cachedAt));
  headers.set(STALE_HEADER, stale ? "1" : "0");
  const alreadyExposed = headers.get("Access-Control-Expose-Headers");
  headers.set(
    "Access-Control-Expose-Headers",
    alreadyExposed
      ? `${alreadyExposed}, ${CACHED_AT_HEADER}, ${STALE_HEADER}`
      : `${CACHED_AT_HEADER}, ${STALE_HEADER}`
  );
  const body = await cached.blob();
  return new Response(body, {
    status: cached.status,
    statusText: cached.statusText,
    headers,
  });
}

/**
 * Keep the API cache under MAX_API_CACHE_ENTRIES, evicting the oldest
 * (by cached-at) first. Full LRU is unnecessary for this data set; bounding
 * the unbounded query-string growth (`/congestion?day=..&hour=..`, paged
 * `/stops`) is the goal.
 */
async function trimApiCache(cache) {
  const keys = await cache.keys();
  if (keys.length <= MAX_API_CACHE_ENTRIES) return;

  const stamped = await Promise.all(
    keys.map(async (key) => {
      const stored = await cache.match(key);
      const at = Number(stored?.headers.get(CACHED_AT_HEADER) || 0);
      return { key, at };
    })
  );
  stamped.sort((a, b) => a.at - b.at);
  const excess = stamped.slice(0, stamped.length - MAX_API_CACHE_ENTRIES);
  await Promise.all(excess.map(({ key }) => cache.delete(key)));
}

/**
 * Tell every page whether the list data it is about to receive came from a
 * stale cache (or from cache because the network is down). Best-effort: the
 * freshness notice is an enhancement, so a failure here must not affect the
 * response that is being returned.
 */
async function notifyCacheStatus(stale, cachedAt) {
  if (!self.clients?.matchAll) return;
  try {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clients) {
      client.postMessage({ type: CACHE_STATUS_MESSAGE, stale, cachedAt });
    }
  } catch {
    /* ignore -- see docstring */
  }
}

async function networkFirstShell(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cache = await caches.open(SHELL_CACHE);
    const cached = await cache.match(request);
    if (cached) return cached;
    if (request.mode === "navigate") {
      const offline = await cache.match(OFFLINE_URL);
      if (offline) return offline;
    }
    throw new Error("Offline and nothing cached for this request");
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    // App shell: pages, JS/CSS chunks, etc.
    event.respondWith(networkFirstShell(request));
    return;
  }

  // Cross-origin: only the backend's three cacheable list endpoints.
  if (isCacheableApiRequest(url)) {
    event.respondWith(staleWhileRevalidate(request));
  }
  // Everything else (route-finder, walking-route, geometry, congestion
  // buckets, stop/route detail lookups, and any other origin) falls
  // through to the browser's normal network fetch, untouched.
});
