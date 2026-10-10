import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RouteResultPanel from "@/components/route/RouteResultPanel";
import {
  FareOut,
  RouteAlternative,
  RouteFinderResult,
  RouteLeg,
  Stop,
} from "@/types/route";

function makeStop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: "S0001",
    stop_name: "Ratna Park",
    lat: 27.7,
    lng: 85.31,
    zone: null,
    district: null,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
    ...overrides,
  };
}

function makeLeg(overrides: Partial<RouteLeg> = {}): RouteLeg {
  return {
    route_id: "R1",
    route_name: "R1",
    board_stop: makeStop({ stop_id: "S1", stop_name: "A" }),
    alight_stop: makeStop({ stop_id: "S2", stop_name: "B" }),
    num_ride_segments: 2,
    stops: [
      makeStop({ stop_id: "S1", stop_name: "A" }),
      makeStop({ stop_id: "S2", stop_name: "B" }),
    ],
    estimated_duration_s: 600,
    ...overrides,
  };
}

function makeFare(overrides: Partial<FareOut> = {}): FareOut {
  return {
    fare_id: "F1",
    min_distance_km: 0,
    max_distance_km: 5,
    fare_npr_min: 20,
    fare_npr_max: 25,
    student_discount_pct: null,
    verification_note: null,
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

function renderPanel(
  result: RouteFinderResult,
  selectedIndex = -1
) {
  return render(
    <RouteResultPanel
      result={{ found: true, ...result }}
      loading={false}
      error={null}
      selectedIndex={selectedIndex}
      onSelectedIndexChange={() => {}}
    />
  );
}

describe("RouteResultPanel time and fare honesty", () => {
  it("shows the backend estimate, not the OSRM driving-profile duration, always labeled (est.)", () => {
    // estimated_duration_s = 600s (10 min) at assumed bus speed; the OSRM
    // driving profile says 300s (5 min) -- a private car at free-flow
    // speed. The panel must show 10 and never the 5.
    const leg = makeLeg({
      estimated_duration_s: 600,
      road_geometry: {
        geometry: { type: "LineString", coordinates: [[85.31, 27.7], [85.32, 27.71]] },
        distance_m: 5000,
        duration_s: 300,
      },
    });
    renderPanel(makeResult({ legs: [leg], total_estimated_duration_s: 600 }));
    expect(screen.getByText("10")).toBeTruthy();
    expect(screen.getByText("(est.)")).toBeTruthy();
    expect(screen.queryByText("5")).toBeNull();
  });

  it("shows the selected alternative's own fare, not the primary result's", () => {
    const alt: RouteAlternative = {
      label: "shortest_distance",
      total_cost: 1200,
      transfer_count: 1,
      legs: [makeLeg()],
      fare: makeFare({ fare_npr_min: 40, fare_npr_max: 45 }),
      total_estimated_duration_s: 900,
    };
    renderPanel(
      makeResult({
        fare: makeFare({ fare_npr_min: 20, fare_npr_max: 25 }),
        alternatives: [alt],
      }),
      0
    );
    expect(screen.getByText(/Rs\. 40–45/)).toBeTruthy();
    expect(screen.queryByText(/Rs\. 20–25/)).toBeNull();
  });

  it("marks an unverified fare as pending confirmation", () => {
    renderPanel(
      makeResult({
        fare: makeFare({
          verification_note: "PENDING confirmation against the official gazette notice.",
        }),
      })
    );
    expect(
      screen.getByText(/Estimated fare — pending confirmation/)
    ).toBeTruthy();
  });

  it("says fare is unavailable when no fare rule matched", () => {
    renderPanel(makeResult({ fare: null }));
    expect(screen.getByText("Fare unavailable")).toBeTruthy();
  });
});
