import { describe, expect, it } from "vitest";
import { bestRouteDistanceKm, formatRouteDistance } from "@/lib/routeDistance";
import { RouteSummary } from "@/types/route";

function makeRoute(overrides: Partial<RouteSummary> = {}): RouteSummary {
  return {
    route_id: "R0001",
    route_name: "Test Route",
    short_name: null,
    vehicle_type: "bus",
    start_stop_id: "S0001",
    end_stop_id: "S0002",
    total_stops: 2,
    is_bidirectional: false,
    approx_distance_km: null,
    osrm_distance_km: null,
    status: "active",
    operator: null,
    ...overrides,
  };
}

describe("bestRouteDistanceKm", () => {
  it("prefers osrm_distance_km over approx_distance_km when both are present", () => {
    const route = makeRoute({ osrm_distance_km: 5.2, approx_distance_km: 4.8 });
    expect(bestRouteDistanceKm(route)).toBe(5.2);
  });

  it("falls back to approx_distance_km when osrm_distance_km is null", () => {
    const route = makeRoute({ osrm_distance_km: null, approx_distance_km: 4.8 });
    expect(bestRouteDistanceKm(route)).toBe(4.8);
  });

  it("returns null when neither distance is available", () => {
    const route = makeRoute({ osrm_distance_km: null, approx_distance_km: null });
    expect(bestRouteDistanceKm(route)).toBeNull();
  });

  it("prefers osrm_distance_km of 0 over a truthy approx_distance_km (nullish coalescing, not ||)", () => {
    // Regression guard: bestRouteDistanceKm uses `??`, which must treat a
    // real 0 km distance as present -- `||` would incorrectly fall through
    // to approx_distance_km here since 0 is falsy.
    const route = makeRoute({ osrm_distance_km: 0, approx_distance_km: 4.8 });
    expect(bestRouteDistanceKm(route)).toBe(0);
  });
});

describe("formatRouteDistance", () => {
  it("formats to one decimal place with a km suffix", () => {
    const route = makeRoute({ osrm_distance_km: 5.234 });
    expect(formatRouteDistance(route)).toBe("5.2 km");
  });

  it("returns null when no distance is available", () => {
    const route = makeRoute({ osrm_distance_km: null, approx_distance_km: null });
    expect(formatRouteDistance(route)).toBeNull();
  });
});
