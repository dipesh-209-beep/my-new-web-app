import { afterEach, describe, expect, it, vi } from "vitest";
import React, { type ReactNode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { RouteSearchResult, Stop } from "@/types/route";

/**
 * Regression guard for the route-overlay refit bug.
 *
 * app/page.tsx derives the object it hands to BusMap (`mapResult`) by merging
 * the primary result with the selected alternative. useRouteResultLayer tears
 * down and re-fits the map whenever its `result` prop changes identity, so a
 * mapResult rebuilt on every render would refit the map on *unrelated* state
 * changes -- dragging the mobile sheet, the congestion poll, a geolocation
 * update -- discarding the user's pan/zoom and any per-leg visibility toggles.
 *
 * This test captures the `result` prop on every BusMap render and asserts its
 * identity is stable across an unrelated re-render while an alternative is
 * selected, but does change when the selected alternative changes.
 */

const captured = vi.hoisted(() => ({
  results: [] as (RouteSearchResult | null)[],
}));

const canned = vi.hoisted(() => {
  const stop = (id: string, name: string): Stop => ({
    stop_id: id,
    stop_name: name,
    lat: 27.7,
    lng: 85.3,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
  });
  const leg = (routeId: string) => ({
    route_id: routeId,
    route_name: routeId,
    board_stop: stop("S1", "A"),
    alight_stop: stop("S2", "B"),
    num_ride_segments: 1,
    stops: [stop("S1", "A"), stop("S2", "B")],
    estimated_duration_s: 600,
    road_geometry: null,
  });
  const result: RouteSearchResult = {
    found: true,
    origin_stop_id: "S1",
    destination_stop_id: "S2",
    total_cost: 20,
    transfer_count: 0,
    fare: null,
    total_estimated_duration_s: 600,
    alternatives: [
      {
        label: "shortest_distance",
        total_cost: 25,
        transfer_count: 1,
        legs: [leg("R2")],
        fare: null,
        total_estimated_duration_s: 900,
      },
    ],
    legs: [leg("R1")],
  };
  return { result };
});

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

// The real next/dynamic would pull in Leaflet; the stub records the props it
// is handed instead, so the test can observe mapResult's identity.
vi.mock("next/dynamic", () => ({
  default: () =>
    function StubMap(props: { result?: RouteSearchResult | null }) {
      captured.results.push(props.result ?? null);
      return null;
    },
}));

vi.mock("@/lib/api", () => ({
  getStop: vi.fn(),
  getAllStops: vi.fn().mockResolvedValue([]),
  getNearbyStops: vi.fn(),
  getWalkingRoute: vi.fn(),
}));

vi.mock("@/hooks/useStops", () => ({
  useStops: () => ({ stops: [], loading: false, error: null, reload: vi.fn() }),
}));

vi.mock("@/hooks/useRouteSearch", () => ({
  useRouteSearch: () => ({
    result: canned.result,
    loading: false,
    loadingStage: "done",
    error: null,
    search: vi.fn(),
    retry: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock("@/hooks/useCongestion", () => ({
  useCongestion: () => ({
    enabled: false,
    toggle: vi.fn(),
    dayOfWeek: null,
    hourBucket: null,
    setDayOfWeek: vi.fn(),
    setHourBucket: vi.fn(),
    segments: [],
    loading: false,
    error: false,
    provenance: null,
    hasSeededOnly: false,
  }),
}));

vi.mock("@/hooks/useRouteBrowser", () => ({
  useRouteBrowser: () => ({
    routes: [],
    total: 0,
    loading: false,
    loadingMore: false,
    error: null,
    retry: vi.fn(),
    searchQuery: "",
    setSearchQuery: vi.fn(),
    visibleRouteId: null,
    visibleRouteStops: [],
    visibleRouteStopsLoading: false,
    visibleRouteGeometry: null,
    visibleRouteGeometryLoading: false,
    direction: "forward",
    setDirection: vi.fn(),
    toggleVisible: vi.fn(),
    showRouteById: vi.fn(),
    loadMore: vi.fn(),
    hasMore: false,
  }),
}));

const { default: Home } = await import("@/app/page");

afterEach(cleanup);

describe("Home route-overlay identity", () => {
  it("keeps the map result referentially stable across an unrelated re-render while an alternative is selected", async () => {
    await act(async () => {
      render(<Home />);
    });

    // Nothing selected: the map gets the primary result itself.
    const primary = captured.results.at(-1);
    expect(primary).toBe(canned.result);

    // Selecting the alternative must produce a (new) merged object...
    await act(async () => {
      screen.getByRole("button", { name: /^Least distance/ }).click();
    });
    const selectedAlt = captured.results.at(-1);
    expect(selectedAlt).not.toBe(primary);
    if (!selectedAlt || !selectedAlt.found) throw new Error("expected a found result");
    expect(selectedAlt.legs[0].route_name).toBe("R2");

    // ...but an unrelated re-render (toggling the Stops map layer) must reuse
    // that same object, so the overlay is not torn down and the map not re-fit.
    await act(async () => {
      screen.getByRole("button", { name: "Stops" }).click();
    });
    const afterUnrelated = captured.results.at(-1);
    expect(afterUnrelated).toBe(selectedAlt);
  });
});
