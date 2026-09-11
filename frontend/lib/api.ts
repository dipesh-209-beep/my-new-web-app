/**
 * lib/api.ts
 *
 * Single point of contact with the FastAPI backend. Every fetch() call in
 * the app should go through a function here instead of being inlined in a
 * component -- keeps request shapes, error handling, and base-URL/timeout
 * logic in one place.
 *
 * Endpoints wrapped (all discovered from backend/app/api/*.py, matched
 * exactly -- no invented routes):
 *   GET /stops
 *   GET /stops/nearby
 *   GET /routes
 *   GET /routes/{route_id}
 *   GET /routes/{route_id}/stops
 *   GET /routes/{route_id}/geometry
 *   GET /route-finder
 *   GET /walking-route
 *   GET /congestion
 *   GET /congestion/buckets
 */

import {
  CongestionResponse,
  RouteDirection,
  RouteFinderResult,
  RouteGeometry,
  RouteListResponse,
  RouteOut,
  RouteStopEntry,
  Stop,
  StopListOut,
  WalkingRoute,
} from "@/types/route";
import { apiBase } from "@/lib/apiBase";
import { DEFAULT_TIMEOUT_MS, ROUTING_TIMEOUT_MS } from "@/lib/constants";

const API_BASE = apiBase();

export type ApiErrorKind = "http" | "network" | "timeout" | "parse";

/**
 * Normalized error shape for every failure mode a fetch can produce, so
 * call sites can branch on `.kind` instead of sniffing error messages or
 * DOMException names.
 */
export class ApiError extends Error {
  kind: ApiErrorKind;
  status?: number;

  constructor(message: string, kind: ApiErrorKind, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

interface RequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);

  // Let an externally-supplied signal (e.g. a search superseded by a newer
  // one) abort this request too, alongside our own timeout.
  // Check if the signal is already aborted before we start -- otherwise
  // we'd miss it if it was aborted between the check and the listener.
  if (options.signal?.aborted) {
    clearTimeout(timeout);
    throw new ApiError("Request was cancelled.", "timeout");
  }
  const onExternalAbort = () => controller.abort();
  options.signal?.addEventListener("abort", onExternalAbort);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError("Request timed out or was cancelled.", "timeout");
    }
    throw new ApiError("Couldn't reach the server. Check your connection.", "network");
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", onExternalAbort);
  }

  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch {
      /* body wasn't JSON -- fine, we just won't have a detail message */
    }
    throw new ApiError(detail || `Request failed (${res.status}).`, "http", res.status);
  }

  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("Received an invalid response from the server.", "parse");
  }
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined) search.set(key, String(value));
  }
  const str = search.toString();
  return str ? `?${str}` : "";
}

// ---------------------------------------------------------------------------
// Stops
// ---------------------------------------------------------------------------

export function getStops(
  params: { limit?: number; offset?: number; district?: string } = {},
  options?: RequestOptions
): Promise<StopListOut> {
  return request<StopListOut>(`/stops${qs(params)}`, options);
}

/**
 * Pages through GET /stops until every stop is loaded (the server caps
 * `limit` at settings.MAX_PAGE_SIZE), firing all remaining pages in
 * parallel once the first page reveals the total. Used by the
 * autocomplete/"use my location" affordances, which need the full list.
 */
export async function getAllStops(options?: RequestOptions): Promise<Stop[]> {
  const pageSize = 100; // stays under MAX_PAGE_SIZE regardless of its exact value
  const first = await getStops({ limit: pageSize, offset: 0 }, options);
  const all: Stop[] = [...first.items];

  const remainingOffsets: number[] = [];
  for (let offset = pageSize; offset < first.total; offset += pageSize) {
    remainingOffsets.push(offset);
  }

  if (remainingOffsets.length > 0) {
    const pages = await Promise.all(
      remainingOffsets.map((offset) => getStops({ limit: pageSize, offset }, options))
    );
    for (const page of pages) all.push(...page.items);
  }

  return all;
}

export function getNearbyStops(
  params: { lat: number; lng: number; radius_m?: number; limit?: number },
  options?: RequestOptions
): Promise<Stop[]> {
  return request<Stop[]>(`/stops/nearby${qs(params)}`, options);
}

export function getStop(stopId: string, options?: RequestOptions): Promise<Stop> {
  return request<Stop>(`/stops/${encodeURIComponent(stopId)}`, options);
}

export function getStopRoutes(stopId: string, options?: RequestOptions): Promise<RouteOut[]> {
  return request<RouteOut[]>(`/stops/${encodeURIComponent(stopId)}/routes`, options);
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

export function getRoutes(
  params: { q?: string; limit?: number; offset?: number } = {},
  options?: RequestOptions
): Promise<RouteListResponse> {
  return request<RouteListResponse>(`/routes${qs(params)}`, options);
}

export function getRoute(routeId: string, options?: RequestOptions): Promise<RouteOut> {
  return request<RouteOut>(`/routes/${encodeURIComponent(routeId)}`, options);
}

export function getRouteStops(
  routeId: string,
  direction: RouteDirection = "forward",
  options?: RequestOptions
): Promise<RouteStopEntry[]> {
  return request<RouteStopEntry[]>(
    `/routes/${encodeURIComponent(routeId)}/stops${qs({ direction })}`,
    options
  );
}

/**
 * Road-following geometry through a route's full stop sequence (OSRM
 * driving directions), for drawing the route-browser overlay along actual
 * roads instead of straight lines between stops. Calls out to OSRM same as
 * route-finder, so gets the longer timeout.
 */
export function getRouteGeometry(
  routeId: string,
  direction: RouteDirection = "forward",
  options?: RequestOptions
): Promise<RouteGeometry> {
  return request<RouteGeometry>(
    `/routes/${encodeURIComponent(routeId)}/geometry${qs({ direction })}`,
    { timeoutMs: ROUTING_TIMEOUT_MS, ...options }
  );
}

// ---------------------------------------------------------------------------
// Route finder / walking directions
// ---------------------------------------------------------------------------

/**
 * Distinct from the other calls: a "no route found" (HTTP 404) is a
 * normal, expected outcome here, not a failure -- callers should treat it
 * as `{ found: false }`, not catch it as an ApiError. Everything else
 * (network/timeout/5xx) still throws.
 */
export async function findRoute(
  params: {
    origin: string;
    destination: string;
    include_alternatives?: boolean;
    /** Intermediate stop_ids the trip must pass through, in order. The
     * backend ignores include_alternatives when this is non-empty (see
     * /route-finder's `via` docstring), so callers shouldn't rely on
     * `alternatives` coming back populated for a via search. */
    via?: string[];
  },
  options?: RequestOptions
): Promise<{ found: true; result: RouteFinderResult } | { found: false }> {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options?.timeoutMs ?? ROUTING_TIMEOUT_MS
  );
  const onExternalAbort = () => controller.abort();
  
  // Check if the signal is already aborted before we start
  if (options?.signal?.aborted) {
    clearTimeout(timeout);
    throw new ApiError("Request was cancelled.", "timeout");
  }
  options?.signal?.addEventListener("abort", onExternalAbort);

  const { via, ...scalarParams } = params;
  // qs() only handles scalar values -- `via` is a repeated query param
  // (?via=A&via=B), so it's appended separately rather than folded into
  // the URLSearchParams qs() builds.
  const search = new URLSearchParams(qs(scalarParams).replace(/^\?/, ""));
  for (const stopId of via ?? []) {
    search.append("via", stopId);
  }
  const queryString = search.toString();

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/route-finder${queryString ? `?${queryString}` : ""}`, {
      signal: controller.signal,
    });
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError("Request timed out or was cancelled.", "timeout");
    }
    throw new ApiError("Couldn't reach the server. Check your connection.", "network");
  } finally {
    clearTimeout(timeout);
    options?.signal?.removeEventListener("abort", onExternalAbort);
  }

  if (res.status === 404) return { found: false };

  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : "";
    } catch {
      /* ignore */
    }
    throw new ApiError(detail || `Request failed (${res.status}).`, "http", res.status);
  }

  try {
    const result = (await res.json()) as RouteFinderResult;
    return { found: true, result };
  } catch {
    throw new ApiError("Received an invalid response from the server.", "parse");
  }
}

export function getWalkingRoute(
  params: { from_lat: number; from_lng: number; to_lat: number; to_lng: number },
  options?: RequestOptions
): Promise<WalkingRoute> {
  return request<WalkingRoute>(`/walking-route${qs(params)}`, {
    timeoutMs: ROUTING_TIMEOUT_MS,
    ...options,
  });
}

// ---------------------------------------------------------------------------
// Congestion
// ---------------------------------------------------------------------------

export function getCongestion(
  params: { day_of_week?: number; hour?: number } = {},
  options?: RequestOptions
): Promise<CongestionResponse> {
  return request<CongestionResponse>(`/congestion${qs(params)}`, options);
}

export function getCongestionBuckets(
  options?: RequestOptions
): Promise<{ hour_buckets: number[] }> {
  return request<{ hour_buckets: number[] }>(`/congestion/buckets`, options);
}
