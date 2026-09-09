/**
 * lib/adminApi.ts
 *
 * Authenticated client for the backend's admin data-entry endpoints
 * (backend/app/api/admin.py + admin_auth.py). Distinct from lib/api.ts
 * (read-only public API): every call here sends `Authorization: Bearer
 * <token>` obtained from POST /admin/login, and the token lives in
 * localStorage so a logged-in session survives page reloads.
 *
 * Endpoints wrapped (matched exactly -- no invented routes):
 *   POST /admin/login
 *   POST /stops
 *   POST /routes
 *   POST /routes/{route_id}/stops
 *   DELETE /routes/{route_id}/stops/{sequence_no}
 *   PATCH /routes/{route_id}/stops/order
 *   PATCH /routes/{route_id}/status
 *   POST /graph/reload
 */

import { ApiError } from "@/lib/api";
import {
  AdminTokenResponse,
  RouteOut,
  RouteStatus,
  RouteStopRef,
  Stop,
  StopCreatePayload,
  RouteCreatePayload,
} from "@/types/route";

const ADMIN_TOKEN_KEY = "ktm-transit:admin-token";

export function getAdminToken(): string | null {
  try {
    return window.localStorage.getItem(ADMIN_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAdminToken(token: string): void {
  try {
    window.localStorage.setItem(ADMIN_TOKEN_KEY, token);
  } catch {
    // Storage unavailable (private browsing / quota) -- the session just
    // won't survive a reload, which is acceptable for an admin tool.
  }
}

export function clearAdminToken(): void {
  try {
    window.localStorage.removeItem(ADMIN_TOKEN_KEY);
  } catch {
    /* nothing to clear */
  }
}

const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * JSON request with an optional bearer token. Returns parsed JSON, or
 * undefined for 204 responses (which have no body). Reuses ApiError so
 * callers can branch on `.status` (a 401 means "log out and re-login").
 */
async function adminRequest<T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  body: unknown,
  token: string | null,
  expectsBody = true
): Promise<T | undefined> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    if (controller.signal.aborted) {
      throw new ApiError("Request timed out.", "timeout");
    }
    throw new ApiError("Couldn't reach the server. Check your connection.", "network");
  } finally {
    clearTimeout(timeout);
  }

  if (res.status === 204) return undefined;

  if (!res.ok) {
    let detail = "";
    try {
      const bodyJson = await res.json();
      detail = typeof bodyJson?.detail === "string" ? bodyJson.detail : "";
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError(detail || `Request failed (${res.status}).`, "http", res.status);
  }

  if (!expectsBody) return undefined;
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("Received an invalid response from the server.", "parse");
  }
}

/** Same base-URL resolution as lib/api.ts (env override, else current host). */
function apiBase(): string {
  const envBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (envBase) return envBase;
  if (typeof window !== "undefined") return `http://${window.location.hostname}:8000`;
  return "http://localhost:8000";
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export function adminLogin(username: string, password: string): Promise<AdminTokenResponse> {
  return adminRequest<AdminTokenResponse>("POST", "/admin/login", { username, password }, null) as Promise<AdminTokenResponse>;
}

// ---------------------------------------------------------------------------
// Stops / routes
// ---------------------------------------------------------------------------

export function adminCreateStop(payload: StopCreatePayload, token: string): Promise<Stop> {
  return adminRequest<Stop>("POST", "/stops", payload, token) as Promise<Stop>;
}

export function adminCreateRoute(payload: RouteCreatePayload, token: string): Promise<RouteOut> {
  return adminRequest<RouteOut>("POST", "/routes", payload, token) as Promise<RouteOut>;
}

// ---------------------------------------------------------------------------
// Route stops
// ---------------------------------------------------------------------------

export function adminAddRouteStop(
  routeId: string,
  stopId: string,
  sequenceNo: number,
  token: string
): Promise<RouteStopRef> {
  return adminRequest<RouteStopRef>(
    "POST",
    `/routes/${encodeURIComponent(routeId)}/stops`,
    { stop_id: stopId, sequence_no: sequenceNo },
    token
  ) as Promise<RouteStopRef>;
}

export function adminRemoveRouteStop(
  routeId: string,
  sequenceNo: number,
  token: string
): Promise<void> {
  return adminRequest<void>(
    "DELETE",
    `/routes/${encodeURIComponent(routeId)}/stops/${sequenceNo}`,
    undefined,
    token
  ) as Promise<void>;
}

/** Reorder a route's stops: `sequence` is the route's current sequence_no
 * values arranged in the desired new order (see RouteStopReorder in
 * backend/app/schemas.py for the rationale). */
export function adminReorderRouteStops(
  routeId: string,
  sequence: number[],
  token: string
): Promise<RouteStopRef[]> {
  return adminRequest<RouteStopRef[]>(
    "PATCH",
    `/routes/${encodeURIComponent(routeId)}/stops/order`,
    { sequence },
    token
  ) as Promise<RouteStopRef[]>;
}

// ---------------------------------------------------------------------------
// Status / graph
// ---------------------------------------------------------------------------

export function adminUpdateRouteStatus(
  routeId: string,
  status: RouteStatus,
  token: string
): Promise<RouteOut> {
  return adminRequest<RouteOut>(
    "PATCH",
    `/routes/${encodeURIComponent(routeId)}/status`,
    { status },
    token
  ) as Promise<RouteOut>;
}

export function adminReloadGraph(token: string): Promise<{ nodes: number; edges: number }> {
  return adminRequest<{ nodes: number; edges: number }>(
    "POST",
    "/graph/reload",
    undefined,
    token
  ) as Promise<{ nodes: number; edges: number }>;
}