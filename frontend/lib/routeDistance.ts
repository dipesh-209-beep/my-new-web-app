import { RouteSummary } from "@/types/route";

/**
 * Where a route's displayed distance came from.
 *
 * - "osrm": the real road distance (`routes.osrm_distance_km`), computed by
 *   an OSRM pass over the route's stop sequence.
 * - "approximate": the source dataset's own figure
 *   (`routes.approx_distance_km`). It is not reliably accurate and can
 *   disagree with the real road distance by a wide margin, so it is shown
 *   with an explicit "(approx.)" marker rather than as a plain distance.
 */
export type RouteDistanceSource = "osrm" | "approximate";

export interface RouteDistance {
  km: number;
  source: RouteDistanceSource;
}

/**
 * A distance is only usable if it is a finite number greater than zero.
 * Null, undefined, zero, negative, NaN and Infinity are all treated as
 * "no distance", so an invalid value is never rendered as if it were a real
 * distance (nor handed to the fare lookup as one).
 */
export function isValidDistanceKm(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

/**
 * The best usable distance for a route, together with the source it came
 * from -- or null when neither source holds a valid value.
 *
 * The OSRM road distance is always preferred over the source-data
 * approximation (see isValidDistanceKm for what "valid" means).
 */
export function routeDistanceInfo(route: RouteSummary): RouteDistance | null {
  if (isValidDistanceKm(route.osrm_distance_km)) {
    return { km: route.osrm_distance_km, source: "osrm" };
  }
  if (isValidDistanceKm(route.approx_distance_km)) {
    return { km: route.approx_distance_km, source: "approximate" };
  }
  return null;
}

/**
 * Best usable distance in kilometres, or null when neither source holds a
 * valid value. Kept for callers that only need the number (e.g. the fare
 * lookup); use routeDistanceInfo when the source has to be known too.
 */
export function bestRouteDistanceKm(route: RouteSummary): number | null {
  return routeDistanceInfo(route)?.km ?? null;
}

/**
 * Formats an already-resolved distance: "5.2 km" for a real OSRM road
 * distance, or "4.8 km (approx.)" when it is only the source-data
 * approximation, so the two can never read as the same kind of value.
 */
export function formatRouteDistanceKm(info: RouteDistance): string {
  const value = `${info.km.toFixed(1)} km`;
  return info.source === "osrm" ? value : `${value} (approx.)`;
}

/**
 * Best usable distance for a route as a display string, or null when neither
 * source holds a valid value -- so callers can omit the segment entirely
 * instead of printing an invalid distance. Convenience wrapper over
 * routeDistanceInfo/formatRouteDistanceKm for simple call sites.
 */
export function formatRouteDistance(route: RouteSummary): string | null {
  const info = routeDistanceInfo(route);
  return info === null ? null : formatRouteDistanceKm(info);
}
