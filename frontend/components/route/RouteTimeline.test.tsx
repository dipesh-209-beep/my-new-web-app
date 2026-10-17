import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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
    expect(screen.getByText("Board at Kalanki")).toBeTruthy();
    expect(screen.getByText("Get off at Koteshwor")).toBeTruthy();
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

  it("gives a direct journey explicit board, ride and alight instructions", () => {
    const leg: RouteLeg = {
      route_id: "R1",
      route_name: "Ratna Park-Budhanilkantha",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Ratna Park" }),
      alight_stop: makeStop({ stop_id: "S4", stop_name: "Machhapokhari" }),
      num_ride_segments: 3,
      stops: [],
      operator_name: "Sajha Yatayat",
      direction_towards: "Budhanilkantha",
      estimated_duration_s: 600,
    };
    render(<RouteTimeline legs={[leg]} />);

    expect(screen.getByText("Ratna Park-Budhanilkantha")).toBeTruthy();
    expect(screen.getByText("Board at Ratna Park")).toBeTruthy();
    expect(screen.getByText("towards Budhanilkantha")).toBeTruthy();
    expect(screen.getByText("Get off at Machhapokhari")).toBeTruthy();
    expect(screen.getByText(/Sajha Yatayat/)).toBeTruthy();
  });

  it("lays out a two-bus journey as ride, transfer, ride with the transfer point named", () => {
    const firstRide: RouteLeg = {
      route_id: "R1",
      route_name: "Kalimati-Koteshwor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Kalimati" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      num_ride_segments: 4,
      stops: [],
    };
    const walk: RouteLeg = {
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      board_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      alight_stop: makeStop({ stop_id: "S3", stop_name: "Ratna Park" }),
      num_ride_segments: 1,
      stops: [],
      estimated_duration_s: 180,
      road_geometry: {
        geometry: { type: "LineString", coordinates: [[85.31, 27.7], [85.32, 27.71]] },
        distance_m: 450,
        duration_s: 200,
      },
    };
    const secondRide: RouteLeg = {
      route_id: "R2",
      route_name: "Ratna Park-Budhanilkantha",
      board_stop: makeStop({ stop_id: "S3", stop_name: "Ratna Park" }),
      alight_stop: makeStop({ stop_id: "S4", stop_name: "Budhanilkantha" }),
      num_ride_segments: 8,
      stops: [],
    };
    render(<RouteTimeline legs={[firstRide, walk, secondRide]} />);

    // Both rides name where to board and where to get off.
    expect(screen.getByText("Board at Kalimati")).toBeTruthy();
    expect(screen.getByText("Get off at Tripureshwor")).toBeTruthy();
    expect(screen.getByText("Board at Ratna Park")).toBeTruthy();
    expect(screen.getByText("Get off at Budhanilkantha")).toBeTruthy();
    // The transfer is its own step naming the point it connects.
    expect(screen.getByText("Transfer")).toBeTruthy();
    expect(screen.getByText("Walk from Tripureshwor to Ratna Park")).toBeTruthy();
  });

  it("names a same-stop transfer and invents no walk distance/duration when none was supplied", () => {
    const walk: RouteLeg = {
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      board_stop: makeStop({ stop_id: "S2", stop_name: "Koteshwor" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Koteshwor" }),
      num_ride_segments: 1,
      stops: [],
    };
    render(<RouteTimeline legs={[walk]} />);
    expect(screen.getByText("Transfer at Koteshwor")).toBeTruthy();
    expect(screen.queryByText(/km|min/)).toBeNull();
  });

  it("omits a missing operator and direction without hiding board or alight", () => {
    const leg: RouteLeg = {
      route_id: "R4",
      route_name: "Unknown Corridor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "A" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "B" }),
      num_ride_segments: 1,
      stops: [],
      operator_name: null,
      direction_towards: null,
    };
    render(<RouteTimeline legs={[leg]} />);
    expect(screen.queryByText(/^towards /)).toBeNull();
    expect(screen.getByText("Board at A")).toBeTruthy();
    expect(screen.getByText("Get off at B")).toBeTruthy();
    expect(screen.getByText("1 stop")).toBeTruthy();
  });
});

describe("RouteTimeline step selection", () => {
  function makeRide(overrides: Partial<RouteLeg> = {}): RouteLeg {
    return {
      route_id: "R1",
      route_name: "Kalimati-Koteshwor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Kalimati" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      num_ride_segments: 4,
      stops: [],
      ...overrides,
    };
  }

  it("turns each leg into a selectable button only when a handler is supplied", () => {
    const { rerender } = render(<RouteTimeline legs={[makeRide()]} />);
    // No handler: steps stay plain content, never dead buttons.
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.getByText("Board at Kalimati")).toBeTruthy();

    rerender(<RouteTimeline legs={[makeRide()]} onLegSelect={vi.fn()} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("reports the index of the leg that was clicked", () => {
    const onLegSelect = vi.fn();
    const walk: RouteLeg = makeRide({
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      board_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      alight_stop: makeStop({ stop_id: "S3", stop_name: "Ratna Park" }),
      num_ride_segments: 1,
    });
    render(<RouteTimeline legs={[makeRide(), walk]} onLegSelect={onLegSelect} />);

    fireEvent.click(screen.getByRole("button", { name: /Board at Kalimati/ }));
    expect(onLegSelect).toHaveBeenCalledWith(0);

    fireEvent.click(screen.getByRole("button", { name: /Walk from Tripureshwor to Ratna Park/ }));
    expect(onLegSelect).toHaveBeenLastCalledWith(1);
    expect(onLegSelect).toHaveBeenCalledTimes(2);
  });

  it("marks only the given leg as pressed", () => {
    render(
      <RouteTimeline legs={[makeRide(), makeRide({ route_id: "R2" })]} selectedLegIndex={1} onLegSelect={vi.fn()} />
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveAttribute("aria-pressed", "false");
    expect(buttons[1]).toHaveAttribute("aria-pressed", "true");
  });

  it("deselects the current step when it is clicked again", () => {
    const onLegSelect = vi.fn();
    render(
      <RouteTimeline legs={[makeRide(), makeRide({ route_id: "R2" })]} selectedLegIndex={1} onLegSelect={onLegSelect} />
    );
    fireEvent.click(screen.getAllByRole("button")[1]);
    expect(onLegSelect).toHaveBeenCalledWith(null);
  });

  it("makes a same-stop transfer selectable and names it without inventing a walk", () => {
    const onLegSelect = vi.fn();
    const sameStopWalk: RouteLeg = makeRide({
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      board_stop: makeStop({ stop_id: "S9", stop_name: "Koteshwor" }),
      alight_stop: makeStop({ stop_id: "S9", stop_name: "Koteshwor" }),
      num_ride_segments: 1,
    });
    render(<RouteTimeline legs={[sameStopWalk]} onLegSelect={onLegSelect} />);

    const button = screen.getByRole("button", { name: /Transfer at Koteshwor/ });
    fireEvent.click(button);
    expect(onLegSelect).toHaveBeenCalledWith(0);
    // No fabricated walk distance/duration text for a same-stop interchange.
    expect(screen.queryByText(/km|min/)).toBeNull();
  });
});
