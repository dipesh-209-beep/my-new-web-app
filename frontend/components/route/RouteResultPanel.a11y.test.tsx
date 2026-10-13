import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import RouteResultPanel from "./RouteResultPanel";
import { RouteFinderResult, RouteLeg } from "@/types/route";

function makeLeg(overrides: Partial<RouteLeg> = {}): RouteLeg {
  return {
    route_id: "R1",
    route_name: "Route 1",
    board_stop: { stop_id: "S1", stop_name: "A", lat: 0, lng: 0, is_major_stop: false, is_interchange: false, status: "active" },
    alight_stop: { stop_id: "S2", stop_name: "B", lat: 0, lng: 0, is_major_stop: false, is_interchange: false, status: "active" },
    num_ride_segments: 1,
    stops: [],
    estimated_duration_s: 600,
    ...overrides,
  };
}

function makeResult(overrides: Partial<RouteFinderResult> = {}): RouteFinderResult {
  return {
    origin_stop_id: "S1",
    destination_stop_id: "S2",
    total_cost: 1000,
    transfer_count: 0,
    legs: [makeLeg()],
    fare: null,
    alternatives: [],
    total_estimated_duration_s: 600,
    ...overrides,
  };
}

describe("RouteResultPanel a11y", () => {
  it("has no axe violations in found state", async () => {
    const { container } = render(
      <RouteResultPanel
        result={{ found: true, ...makeResult() }}
        loading={false}
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />,
    );
    const results = await axe(container);
    expect(results.violations).toHaveLength(0);
  });
});
