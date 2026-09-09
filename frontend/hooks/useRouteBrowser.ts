import { useEffect, useRef, useState } from "react";
import { RouteDirection, RouteGeometry, RouteStopEntry, RouteSummary } from "@/types/route";
import { getRouteGeometry, getRouteStops, getRoutes } from "@/lib/api";

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

interface UseRouteBrowserResult {
  routes: RouteSummary[];
  total: number;
  loading: boolean;
  loadingMore: boolean;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  visibleRouteId: string | null;
  visibleRouteStops: RouteStopEntry[];
  visibleRouteStopsLoading: boolean;
  /** Road-following OSRM geometry for the visible route, for drawing it
   * along actual roads on the map. Null while loading/unavailable -- the
   * map falls back to straight lines between stops in that case, same as
   * it always has. */
  visibleRouteGeometry: RouteGeometry | null;
  visibleRouteGeometryLoading: boolean;
  /** Current travel direction for the visible route. Only meaningful when
   * `visibleRouteId` is non-null and the route is bidirectional. */
  direction: RouteDirection;
  /** Switches direction for the currently visible route and reloads its
   * stops/geometry for that direction (cache-aware, race-guarded). A
   * no-op if no route is currently visible. */
  setDirection: (d: RouteDirection) => void;
  toggleVisible: (route: RouteSummary) => Promise<void>;
  /** Show a specific route's stops by ID without requiring it to be in
   * the currently loaded/paged list -- used for deep links like
   * /routes/[routeId]'s "View on map" action. */
  showRouteById: (routeId: string) => Promise<void>;
  loadMore: () => void;
  hasMore: boolean;
}

/**
 * Paged/searchable list of routes, with one route at a time toggle-able
 * "visible" (its ordered stops shown both inline in the panel and drawn
 * on the map). Only one visible at a time keeps the map readable --
 * matches the congestion/walking overlays' single-layer approach.
 */
export function useRouteBrowser(): UseRouteBrowserResult {
  const [routes, setRoutes] = useState<RouteSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const [visibleRouteId, setVisibleRouteId] = useState<string | null>(null);
  const [direction, setDirectionState] = useState<RouteDirection>("forward");
  const [visibleRouteStops, setVisibleRouteStops] = useState<RouteStopEntry[]>([]);
  const [visibleRouteStopsLoading, setVisibleRouteStopsLoading] = useState(false);
  // Cache keyed by `${routeId}:${direction}` so forward/reverse don't collide
  const [routeStopsCache, setRouteStopsCache] = useState<Record<string, RouteStopEntry[]>>({});

  const [visibleRouteGeometry, setVisibleRouteGeometry] = useState<RouteGeometry | null>(null);
  const [visibleRouteGeometryLoading, setVisibleRouteGeometryLoading] = useState(false);
  const [routeGeometryCache, setRouteGeometryCache] = useState<Record<string, RouteGeometry | null>>(
    {}
  );

  // Guards against slow requests overwriting faster ones when the user
  // toggles visibility rapidly or deep-links to a different route.
  const geometryRequestIdRef = useRef(0);
  const stopsRequestIdRef = useRef(0);

  function makeCacheKey(routeId: string, dir: RouteDirection): string {
    return `${routeId}:${dir}`;
  }

  // Fetched alongside stops but kept as its own request -- a route with no
  // usable OSRM geometry (OSRM down, route has <2 stops) should still show
  // its stops; the map layer just falls back to straight lines for that
  // one route rather than the whole panel erroring out.
  async function loadGeometry(routeId: string, dir: RouteDirection) {
    const requestId = ++geometryRequestIdRef.current;
    const cacheKey = makeCacheKey(routeId, dir);

    const cached = routeGeometryCache[cacheKey];
    if (cached !== undefined) {
      if (geometryRequestIdRef.current === requestId) {
        setVisibleRouteGeometry(cached);
      }
      return;
    }

    if (geometryRequestIdRef.current === requestId) {
      setVisibleRouteGeometryLoading(true);
      setVisibleRouteGeometry(null);
    }
    try {
      const data = await getRouteGeometry(routeId, dir);
      if (geometryRequestIdRef.current !== requestId) return;
      setVisibleRouteGeometry(data);
      setRouteGeometryCache((prev) => ({ ...prev, [cacheKey]: data }));
    } catch {
      if (geometryRequestIdRef.current !== requestId) return;
      setVisibleRouteGeometry(null);
      setRouteGeometryCache((prev) => ({ ...prev, [cacheKey]: null }));
    } finally {
      if (geometryRequestIdRef.current === requestId) {
        setVisibleRouteGeometryLoading(false);
      }
    }
  }

  // Debounced search -- re-fetch page 1 whenever the query settles,
  // rather than on every keystroke.
  useEffect(() => {
    let cancelled = false;

    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const data = await getRoutes({
          limit: PAGE_SIZE,
          offset: 0,
          q: searchQuery.trim() || undefined,
        });
        if (!cancelled) {
          setRoutes(data.items);
          setTotal(data.total);
        }
      } catch {
        // Route browser is supplementary -- fail silently, panel just
        // shows "no routes" rather than an error banner.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [searchQuery]);

  async function loadMore() {
    setLoadingMore(true);
    try {
      const data = await getRoutes({
        limit: PAGE_SIZE,
        offset: routes.length,
        q: searchQuery.trim() || undefined,
      });
      setRoutes((prev) => [...prev, ...data.items]);
      setTotal(data.total);
    } catch {
      // supplementary feature -- ignore
    } finally {
      setLoadingMore(false);
    }
  }

  // Shared by toggleVisible/showRouteById/changeDirection so all three
  // paths that can put a (routeId, direction) pair on screen go through
  // the same cache + race-guard logic. On failure the caller decides
  // whether to also clear visibleRouteId (deep link vs. direction flip
  // want different fallback behavior), so this only clears the stop list.
  async function loadStops(routeId: string, dir: RouteDirection): Promise<{ ok: boolean }> {
    const requestId = ++stopsRequestIdRef.current;
    const cacheKey = makeCacheKey(routeId, dir);

    const cached = routeStopsCache[cacheKey];
    if (cached) {
      if (stopsRequestIdRef.current === requestId) {
        setVisibleRouteStops(cached);
      }
      return { ok: true };
    }

    if (stopsRequestIdRef.current === requestId) {
      setVisibleRouteStopsLoading(true);
      setVisibleRouteStops([]);
    }
    try {
      const data = await getRouteStops(routeId, dir);
      if (stopsRequestIdRef.current !== requestId) return { ok: true };
      setVisibleRouteStops(data);
      setRouteStopsCache((prev) => ({ ...prev, [cacheKey]: data }));
      return { ok: true };
    } catch {
      if (stopsRequestIdRef.current !== requestId) return { ok: true };
      return { ok: false };
    } finally {
      if (stopsRequestIdRef.current === requestId) {
        setVisibleRouteStopsLoading(false);
      }
    }
  }

  async function toggleVisible(route: RouteSummary) {
    if (visibleRouteId === route.route_id) {
      setVisibleRouteId(null);
      setVisibleRouteStops([]);
      setVisibleRouteGeometry(null);
      return;
    }

    setVisibleRouteId(route.route_id);
    // Reset direction to forward when switching routes
    setDirectionState("forward");
    loadGeometry(route.route_id, "forward");
    await loadStops(route.route_id, "forward");
    // supplementary feature -- a failed fetch here just leaves the stop
    // list empty rather than erroring, same as before this was extracted.
  }

  async function showRouteById(routeId: string) {
    if (visibleRouteId === routeId) return;

    setVisibleRouteId(routeId);
    // Use current direction for deep links (defaults to forward)
    loadGeometry(routeId, direction);
    const { ok } = await loadStops(routeId, direction);
    if (!ok) {
      // Bad/stale route id -- undo so the panel doesn't look "stuck"
      // showing a route that never loaded.
      setVisibleRouteId(null);
    }
  }

  // Direction is only meaningful together with whichever route is
  // currently visible, so switching it re-runs both fetches for the new
  // (routeId, direction) pair -- previously this only flipped the
  // Forward/Return UI state and left the map/timeline showing the other
  // direction's stops and geometry until something else (a re-toggle)
  // happened to refresh them.
  function changeDirection(dir: RouteDirection) {
    setDirectionState(dir);
    if (!visibleRouteId) return;
    loadGeometry(visibleRouteId, dir);
    loadStops(visibleRouteId, dir);
  }

  return {
    routes,
    total,
    loading,
    loadingMore,
    searchQuery,
    setSearchQuery,
    visibleRouteId,
    visibleRouteStops,
    visibleRouteStopsLoading,
    visibleRouteGeometry,
    visibleRouteGeometryLoading,
    direction,
    setDirection: changeDirection,
    toggleVisible,
    showRouteById,
    loadMore,
    hasMore: routes.length < total,
  };
}
