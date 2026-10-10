import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RouteTimeline from "@/components/route/RouteTimeline";
import { Stop, RouteLeg } from "@/types/route";

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

describe("RouteTimeline", () => {
  it("shows hop count (num_ride_segments), not physical stop count", () => {
    // 3 hops across 4 physical stops (board + 2 intermediates + alight).
    const leg: RouteLeg = {
      route_id: "R1",
      route_name: "Ratna Park-Budhanilkantha",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Ratna Park" }),
      alight_stop: makeStop({ stop_id: "S4", stop_name: "Machhapokhari" }),
      num_ride_segments: 3,
      stops: [
        makeStop({ stop_id: "S1", stop_name: "Ratna Park" }),
        makeStop({ stop_id: "S2", stop_name: "Bhadrakali" }),
        makeStop({ stop_id: "S3", stop_name: "New Bus Park" }),
        makeStop({ stop_id: "S4", stop_name: "Machhapokhari" }),
      ],
    };
    render(<RouteTimeline legs={[leg]} />);
    // "3 stops" is the hop/segment count -- NOT the 4 physical stops,
    // which overstates the ride and was the old behaviour.
    expect(screen.getByText("3 stops")).toBeTruthy();
    expect(screen.queryByText("4 stops")).toBeNull();
  });

  it("uses singular 'stop' for a single hop", () => {
    const leg: RouteLeg = {
      route_id: "R2",
      route_name: "Balaju-Tarkeshwor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Balaju" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Tarkeshwor" }),
      num_ride_segments: 1,
      stops: [
        makeStop({ stop_id: "S1", stop_name: "Balaju" }),
        makeStop({ stop_id: "S2", stop_name: "Tarkeshwor" }),
      ],
    };
    render(<RouteTimeline legs={[leg]} />);
    expect(screen.getByText("1 stop")).toBeTruthy();
  });

  it("shows direction towards terminus and the operating company", () => {
    const leg: RouteLeg = {
      route_id: "R3",
      route_name: "Kalanki - Koteshwor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Kalanki" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Koteshwor" }),
      num_ride_segments: 10,
      stops: [
        makeStop({ stop_id: "S1", stop_name: "Kalanki" }),
        makeStop({ stop_id: "S2", stop_name: "Koteshwor" }),
      ],
      operator_name: "Mahanagar Yatayat",
      direction_towards: "Koteshwor",
    };
    render(<RouteTimeline legs={[leg]} />);
    expect(screen.getByText("towards Koteshwor")).toBeTruthy();
    expect(screen.getByText(/Mahanagar Yatayat/)).toBeTruthy();
    expect(screen.getByText(/Kalanki → Koteshwor/)).toBeTruthy();
  });

  it("omits direction/operator lines when the backend didn't resolve them", () => {
    const leg: RouteLeg = {
      route_id: "R4",
      route_name: "Unknown Corridor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "A" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "B" }),
      num_ride_segments: 1,
      stops: [
        makeStop({ stop_id: "S1", stop_name: "A" }),
        makeStop({ stop_id: "S2", stop_name: "B" }),
      ],
      operator_name: null,
      direction_towards: null,
    };
    render(<RouteTimeline legs={[leg]} />);
    expect(screen.queryByText("towards ")).toBeNull();
    expect(screen.getByText("1 stop")).toBeTruthy();
  });

  it("shows the backend estimate, not the OSRM driving-profile duration", () => {
    // estimated_duration_s = 600s (10 min) at assumed bus speed; the OSRM
    // driving profile says 300s (5 min), which describes a private car at
    // free-flow speed, not a bus. The timeline must show 10, never 5.
    const leg: RouteLeg = {
      route_id: "R5",
      route_name: "Ratna Park-Budhanilkantha",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Ratna Park" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Machhapokhari" }),
      num_ride_segments: 5,
      stops: [
        makeStop({ stop_id: "S1", stop_name: "Ratna Park" }),
        makeStop({ stop_id: "S2", stop_name: "Machhapokhari" }),
      ],
      estimated_duration_s: 600,
      road_geometry: {
        geometry: { type: "LineString", coordinates: [[85.31, 27.7], [85.35, 27.75]] },
        distance_m: 5000,
        duration_s: 300,
      },
    };
    render(<RouteTimeline legs={[leg]} />);
    expect(screen.getByText(/10 min/)).toBeTruthy();
    expect(screen.queryByText(/5 min/)).toBeNull();
  });
});