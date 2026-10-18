"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

/**
 * lib-free hook that reports whether the service worker is currently serving
 * list data (/stops, /routes, /congestion) from a stale cache, plus live
 * offline state. public/sw.js posts a `ktm-bus-cache-status` message whenever
 * it answers one of those requests, so the page can show a subtle "showing
 * saved data" note instead of presenting stale rows as fresh (AUD-014).
 *
 * The message type is duplicated here rather than imported: sw.js is a plain
 * public asset served verbatim, not part of the bundler's module graph.
 */
const CACHE_STATUS_MESSAGE = "ktm-bus-cache-status";

// navigator.onLine is an external store, so subscribe to it directly rather
// than mirroring it into state from an effect (which would cascade a render).
// The server snapshot is "online": a fresh render is the right default, and
// the client corrects it before paint if the device is actually offline.
function subscribeOnline(callback: () => void) {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

function getOnlineSnapshot() {
  return navigator.onLine;
}

function getServerOnlineSnapshot() {
  return true;
}

export interface CacheFreshness {
  /** The last cacheable list response came from an expired cache copy, or
   *  from cache because the network was unavailable. */
  stale: boolean;
  /** When the served copy was cached (epoch ms), or null if unknown. */
  cachedAt: number | null;
  /** navigator.onLine === false, tracked live. */
  offline: boolean;
}

export function useCacheFreshness(): CacheFreshness {
  const [stale, setStale] = useState(false);
  const [cachedAt, setCachedAt] = useState<number | null>(null);
  const online = useSyncExternalStore(
    subscribeOnline,
    getOnlineSnapshot,
    getServerOnlineSnapshot
  );

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;

    function onMessage(event: MessageEvent) {
      const data = event.data as
        | { type?: unknown; stale?: unknown; cachedAt?: unknown }
        | null;
      if (!data || data.type !== CACHE_STATUS_MESSAGE) return;
      setStale(Boolean(data.stale));
      setCachedAt(typeof data.cachedAt === "number" ? data.cachedAt : null);
    }

    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, []);

  return { stale, cachedAt, offline: !online };
}
