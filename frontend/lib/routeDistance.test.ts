import { describe, expect, it } from "vitest";
import {
  bestRouteDistanceKm,
  formatRouteDistance,
  formatRouteDistanceKm,
  isValidDistanceKm,
  routeDistanceInfo,
} from "@/lib/routeDistance";
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
    estimated_duration_min: null,
    status: "active",
    operator: null,
    ...overrides,
  };
}

describe("isValidDistanceKm", () => {
  it("accepts finite positive numbers", () => {
    expect(isValidDistanceKm(5.2)).toBe(true);
    expect(isValidDistanceKm(0.1)).toBe(true);
  });

  it("rejects null, undefined, zero, negative, NaN and Infinity", () => {
    expect(isValidDistanceKm(null)).toBe(false);
    expect(isValidDistanceKm(undefined)).toBe(false);
    expect(isValidDistanceKm(0)).toBe(false);
    expect(isValidDistanceKm(-1)).toBe(false);
    expect(isValidDistanceKm(Number.NaN)).toBe(false);
    expect(isValidDistanceKm(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isValidDistanceKm(Number.NEGATIVE_INFINITY)).toBe(false);
  });

  it("rejects non-number values", () => {
    expect(isValidDistanceKm("5.2")).toBe(false);
    expect(isValidDistanceKm({})).toBe(false);
  });
});

describe("routeDistanceInfo", () => {
  it("prefers osrm_distance_km and labels the source osrm", () => {
    const route = makeRoute({ osrm_distance_km: 5.2, approx_distance_km: 4.8 });
    expect(routeDistanceInfo(route)).toEqual({ km: 5.2, source: "osrm" });
  });

  it("falls back to approx_distance_km and labels the source approximate", () => {
    const route = makeRoute({ osrm_distance_km: null, approx_distance_km: 4.8 });
    expect(routeDistanceInfo(route)).toEqual({ km: 4.8, source: "approximate" });
  });

  it("returns null when neither distance is a valid value", () => {
    expect(routeDistanceInfo(makeRoute())).toBeNull();
  });

  it("treats an invalid osrm_distance_km (0/negative/NaN/Infinity) as absent", () => {
    for (const bad of [0, -3, Number.NaN, Number.POSITIVE_INFINITY]) {
      const route = makeRoute({ osrm_distance_km: bad, approx_distance_km: 4.8 });
      expect(routeDistanceInfo(route)).toEqual({ km: 4.8, source: "approximate" });
    }
  });

  it("returns null when both values are invalid", () => {
    const route = makeRoute({ osrm_distance_km: 0, approx_distance_km: -1 });
    expect(routeDistanceInfo(route)).toBeNull();
  });

  it("ignores an invalid approx_distance_km and keeps a valid osrm one", () => {
    const route = makeRoute({ osrm_distance_km: 5.2, approx_distance_km: Number.NaN });
    expect(routeDistanceInfo(route)).toEqual({ km: 5.2, source: "osrm" });
  });
});

describe("bestRouteDistanceKm", () => {
  it("prefers osrm_distance_km over approx_distance_km", () => {
    const route = makeRoute({ osrm_distance_km: 5.2, approx_distance_km: 4.8 });
    expect(bestRouteDistanceKm(route)).toBe(5.2);
  });

  it("falls back to approx_distance_km when osrm is absent", () => {
    const route = makeRoute({ osrm_distance_km: null, approx_distance_km: 4.8 });
    expect(bestRouteDistanceKm(route)).toBe(4.8);
  });

  it("returns null when no valid distance is available", () => {
    expect(bestRouteDistanceKm(makeRoute())).toBeNull();
    expect(bestRouteDistanceKm(makeRoute({ osrm_distance_km: 0, approx_distance_km: 0 }))).toBeNull();
  });
});

describe("formatRouteDistanceKm", () => {
  it("formats an osrm distance with no qualifier", () => {
    expect(formatRouteDistanceKm({ km: 5.234, source: "osrm" })).toBe("5.2 km");
  });

  it("marks an approximate distance with (approx.) so it can't read as real", () => {
    expect(formatRouteDistanceKm({ km: 4.76, source: "approximate" })).toBe("4.8 km (approx.)");
  });
});

describe("formatRouteDistance", () => {
  it("formats a real osrm distance without a qualifier", () => {
    expect(formatRouteDistance(makeRoute({ osrm_distance_km: 5.234 }))).toBe("5.2 km");
  });

  it("flags an approximate fallback so it can't read as a real road distance", () => {
    expect(formatRouteDistance(makeRoute({ approx_distance_km: 4.76 }))).toBe("4.8 km (approx.)");
  });

  it("returns null when no valid distance is available", () => {
    expect(formatRouteDistance(makeRoute())).toBeNull();
    expect(formatRouteDistance(makeRoute({ osrm_distance_km: 0, approx_distance_km: 0 }))).toBeNull();
  });
});
