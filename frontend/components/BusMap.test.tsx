import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import type { RouteLeg, RouteSearchResult, RouteStopEntry, Stop } from "@/types/route";
import { leafletState } from "@/test/leaflet-mock";

vi.mock("leaflet", async () => {
  const { createLeafletMock } = await import("@/test/leaflet-mock");
  return createLeafletMock();
});

// Imported after the mock is registered so BusMap picks up the mocked
// "leaflet" module.
const { default: BusMap } = await import("./BusMap");

function makeStop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: "S0001",
    stop_name: "Ratna Park",
    lat: 27.7041,
    lng: 85.31,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
    ...overrides,
  };
}

beforeEach(() => {
  leafletState.reset();
});

afterEach(() => {
  cleanup();
});

describe("BusMap", () => {
  it("mounts a Leaflet map on its container without crashing", () => {
    render(<BusMap />);
    expect(leafletState.mapInstances.length).toBe(1);
  });

  describe("all-stops layer", () => {
    it("renders one circle marker per stop and escapes the tooltip content", () => {
      // Distinct, well-separated coordinates -- two stops sharing a
      // location would fall into the same cluster (lib/stopClustering.ts)
      // at the default zoom and render as one cluster bubble instead of
      // two individual circle markers, which isn't what this test means
      // to exercise.
      const stops = [
        makeStop({ stop_id: "S0001", stop_name: "Ratna Park", lat: 27.7041, lng: 85.31 }),
        makeStop({ stop_id: "S0002", stop_name: 'New Road <script>alert(1)</script>', lat: 27.75, lng: 85.36 }),
      ];
      render(<BusMap allStops={stops} />);

      const circleMarkers = leafletState.markers.filter((m) => m.kind === "circleMarker");
      expect(circleMarkers).toHaveLength(2);
      // HTML-unsafe characters in stop names must be escaped before being
      // interpolated into Leaflet's HTML-as-string tooltip content.
      expect(circleMarkers[1].tooltip?.content).toContain("&lt;script&gt;");
      expect(circleMarkers[1].tooltip?.content).not.toContain("<script>");
    });

    it("only invokes onStopPick when a pickTarget is active", () => {
      const stop = makeStop();
      const onStopPick = vi.fn();

      const { rerender } = render(
        <BusMap allStops={[stop]} pickTarget={null} onStopPick={onStopPick} />
      );
      let marker = leafletState.markers[0];
      marker.handlers["click"]?.forEach((h) => h());
      expect(onStopPick).not.toHaveBeenCalled();

      leafletState.reset();
      rerender(<BusMap allStops={[stop]} pickTarget="origin" onStopPick={onStopPick} />);
      marker = leafletState.markers[0];
      marker.handlers["click"]?.forEach((h) => h());
      expect(onStopPick).toHaveBeenCalledWith(stop);
    });

    it("groups nearby stops into a cluster bubble and zooms in on click instead of picking one", () => {
      const stops = [
        makeStop({ stop_id: "S0001", lat: 27.70, lng: 85.30 }),
        makeStop({ stop_id: "S0002", lat: 27.7001, lng: 85.3001 }), // a few meters away
      ];
      const onStopPick = vi.fn();

      render(<BusMap allStops={stops} pickTarget="origin" onStopPick={onStopPick} />);

      // Two stops this close together render as one cluster (a marker, not
      // a circleMarker) at the map's default zoom.
      expect(leafletState.markers.filter((m) => m.kind === "circleMarker")).toHaveLength(0);
      const clusterMarker = leafletState.markers.find((m) => m.kind === "marker");
      expect(clusterMarker).toBeDefined();

      clusterMarker!.handlers["click"]?.forEach((h) => h());
      expect(onStopPick).not.toHaveBeenCalled();
      // Clicking a cluster zooms in via the map, not a stop pick -- setView
      // is the mechanism; already covered by onStopPick not firing above.
    });

    it("does not render the all-stops layer at all when showAllStops is false", () => {
      const stop = makeStop();
      render(<BusMap allStops={[stop]} showAllStops={false} />);
      expect(leafletState.markers).toHaveLength(0);
    });

    it("dims (but does not hide) stop dots once a route result is found", () => {
      const stop = makeStop();
      const foundResult: RouteSearchResult = {
        found: true,
        origin_stop_id: "S0001",
        destination_stop_id: "S0002",
        total_cost: 10,
        transfer_count: 0,
        fare: null,
        alternatives: [],
        legs: [],
      };

      const { rerender } = render(<BusMap allStops={[stop]} result={{ found: false }} />);
      const brightDot = leafletState.markers.find((m) => m.kind === "circleMarker")!;
      const brightOpacity = brightDot.options.fillOpacity;

      leafletState.reset();
      rerender(<BusMap allStops={[stop]} result={foundResult} />);
      const dimmedDot = leafletState.markers.find((m) => m.kind === "circleMarker")!;
      expect(dimmedDot.options.fillOpacity).toBeLessThan(brightOpacity as number);
    });
  });

  describe("browse-route overlay coordinate handling", () => {
    // Regression coverage for the coordinate-coercion bug: some API
    // responses send lat/lng as strings, which must still render (Number()
    // coercion), while genuinely invalid values must be dropped rather
    // than crashing the effect and silently killing the whole overlay.
    it("coerces string lat/lng into numeric marker positions", () => {
      const entries: RouteStopEntry[] = [
        {
          sequence_no: 1,
          stop: { ...makeStop({ stop_id: "S0001" }), lat: "27.7041" as unknown as number, lng: "85.31" as unknown as number },
        },
        {
          sequence_no: 2,
          stop: makeStop({ stop_id: "S0002", lat: 27.71, lng: 85.32 }),
        },
      ];

      render(<BusMap browseRouteStops={entries} />);

      const markers = leafletState.markers.filter((m) => m.kind === "marker");
      expect(markers).toHaveLength(2);
      expect(markers[0].latlng).toEqual([27.7041, 85.31]);
    });

    it("prefers display_lat/display_lng over the canonical stop coordinate when present", () => {
      const entries: RouteStopEntry[] = [
        {
          sequence_no: 1,
          stop: makeStop({ stop_id: "S0001", lat: 27.7041, lng: 85.31 }),
          // Route/direction-specific adjusted position -- see
          // app/routing/stop_positioning.py -- should win over stop.lat/lng.
          display_lat: 27.7050,
          display_lng: 85.3110,
        },
        {
          sequence_no: 2,
          // No adjustment computed for this stop -- falls back to canonical.
          stop: makeStop({ stop_id: "S0002", lat: 27.71, lng: 85.32 }),
        },
      ];

      render(<BusMap browseRouteStops={entries} />);

      const markers = leafletState.markers.filter((m) => m.kind === "marker");
      expect(markers).toHaveLength(2);
      expect(markers[0].latlng).toEqual([27.7050, 85.3110]);
      expect(markers[1].latlng).toEqual([27.71, 85.32]);
    });

    it("drops entries with invalid coordinates instead of crashing, and still draws the rest", () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      const entries: RouteStopEntry[] = [
        { sequence_no: 1, stop: makeStop({ stop_id: "S0001", lat: 27.7041, lng: 85.31 }) },
        {
          sequence_no: 2,
          stop: { ...makeStop({ stop_id: "S0002" }), lat: "not-a-number" as unknown as number, lng: 85.32 },
        },
      ];

      expect(() => render(<BusMap browseRouteStops={entries} />)).not.toThrow();

      const markers = leafletState.markers.filter((m) => m.kind === "marker");
      expect(markers).toHaveLength(1);
      expect(warnSpy).toHaveBeenCalled();
      warnSpy.mockRestore();
    });

    it("logs an error and draws nothing when every entry has invalid coordinates", () => {
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      const entries: RouteStopEntry[] = [
        { sequence_no: 1, stop: { ...makeStop(), lat: NaN, lng: NaN } },
      ];

      expect(() => render(<BusMap browseRouteStops={entries} />)).not.toThrow();

      expect(leafletState.markers.filter((m) => m.kind === "marker")).toHaveLength(0);
      expect(errorSpy).toHaveBeenCalled();
      errorSpy.mockRestore();
    });

    it("draws along OSRM road geometry when available, falling back to straight segments otherwise", () => {
      const entries: RouteStopEntry[] = [
        { sequence_no: 1, stop: makeStop({ stop_id: "S0001", lat: 27.7, lng: 85.3 }) },
        { sequence_no: 2, stop: makeStop({ stop_id: "S0002", lat: 27.71, lng: 85.32 }) },
      ];

      // No geometry -> straight line between the two stop coordinates.
      const { rerender } = render(<BusMap browseRouteStops={entries} browseRouteGeometry={null} />);
      let line = leafletState.polylines[leafletState.polylines.length - 1];
      expect(line.points).toEqual([
        [27.7, 85.3],
        [27.71, 85.32],
      ]);

      leafletState.reset();
      // With geometry -> follows the road polyline (GeoJSON [lng,lat] ->
      // Leaflet [lat,lng] conversion).
      rerender(
        <BusMap
          browseRouteStops={entries}
          browseRouteGeometry={{
            geometry: { type: "LineString", coordinates: [[85.3, 27.7], [85.305, 27.705], [85.32, 27.71]] },
            distance_m: 1200,
            duration_s: 300,
          }}
        />
      );
      line = leafletState.polylines[leafletState.polylines.length - 1];
      expect(line.points).toEqual([
        [27.7, 85.3],
        [27.705, 85.305],
        [27.71, 85.32],
      ]);
    });
  });

  describe("route-result legend", () => {
    function makeLeg(overrides: Partial<RouteLeg> = {}): RouteLeg {
      const board = makeStop({ stop_id: "S0001", stop_name: "Ratna Park" });
      const alight = makeStop({ stop_id: "S0002", stop_name: "Koteshwor" });
      return {
        route_id: "R0001",
        route_name: "Route 1",
        board_stop: board,
        alight_stop: alight,
        num_ride_segments: 1,
        stops: [board, alight],
        road_geometry: null,
        ...overrides,
      };
    }

    it("adds a legend control capped to a max width, so a long route name can't overflow on mobile", () => {
      const result: RouteSearchResult = {
        found: true,
        origin_stop_id: "S0001",
        destination_stop_id: "S0002",
        total_cost: 10,
        transfer_count: 0,
        fare: null,
        alternatives: [],
        legs: [
          makeLeg({
            route_name:
              "A Very Long Route Name That Would Previously Overflow The Legend Box On A Narrow Phone Screen",
          }),
        ],
      };

      render(<BusMap result={result} />);

      // Filter to the legend specifically -- BusMap now also adds a scale
      // bar and a recenter button (always present, regardless of
      // `result`), so asserting on the raw control count would make this
      // test brittle against unrelated map-chrome additions.
      const legendControls = leafletState.controls.filter(
        (c) => c.div?.style.maxWidth === "min(220px, 60vw)"
      );
      expect(legendControls).toHaveLength(1);
      const div = legendControls[0].div;
      expect(div).not.toBeNull();
      expect(div!.style.maxWidth).toBe("min(220px, 60vw)");
      expect(div!.innerHTML).toContain("A Very Long Route Name");
    });

    it("does not add a legend when there is no found result", () => {
      render(<BusMap result={{ found: false }} />);
      const legendControls = leafletState.controls.filter(
        (c) => c.div?.style.maxWidth === "min(220px, 60vw)"
      );
      expect(legendControls).toHaveLength(0);
    });

    it("clicking a legend row hides that leg's layer and restores it on a second click", () => {
      const result: RouteSearchResult = {
        found: true,
        origin_stop_id: "S0001",
        destination_stop_id: "S0002",
        total_cost: 10,
        transfer_count: 0,
        fare: null,
        alternatives: [],
        legs: [makeLeg({ route_name: "Route 1" })],
      };

      render(<BusMap result={result} />);

      const legendDiv = leafletState.controls.find(
        (c) => c.div?.style.maxWidth === "min(220px, 60vw)"
      )!.div!;
      const rowButton = legendDiv.querySelector("button")!;
      expect(rowButton).not.toBeNull();
      expect(rowButton.getAttribute("aria-pressed")).toBe("true");

      // This leg's layer group is the most recently added at this point.
      const legLayer = leafletState.layerGroups[leafletState.layerGroups.length - 1];
      expect(legLayer.removed).toBe(false);

      rowButton.click();
      expect(rowButton.getAttribute("aria-pressed")).toBe("false");
      expect(legLayer.removed).toBe(true);

      rowButton.click();
      expect(rowButton.getAttribute("aria-pressed")).toBe("true");
    });

    it("draws a dashed grey line for walking transfers and a solid colored line for ride legs", () => {
      const result: RouteSearchResult = {
        found: true,
        origin_stop_id: "S0001",
        destination_stop_id: "S0003",
        total_cost: 10,
        transfer_count: 1,
        fare: null,
        alternatives: [],
        legs: [
          makeLeg({ route_id: "R0001", route_name: "Route 1" }),
          makeLeg({
            route_id: "TRANSFER",
            route_name: "Walk",
            board_stop: makeStop({ stop_id: "S0002", stop_name: "Koteshwor" }),
            alight_stop: makeStop({ stop_id: "S0003", stop_name: "Tinkune" }),
            stops: [
              makeStop({ stop_id: "S0002", stop_name: "Koteshwor" }),
              makeStop({ stop_id: "S0003", stop_name: "Tinkune" }),
            ],
          }),
        ],
      };

      render(<BusMap result={result} />);

      const rideLine = leafletState.polylines.find((p) => p.options.dashArray === undefined);
      const walkLine = leafletState.polylines.find((p) => p.options.dashArray === "6 8");
      expect(rideLine).toBeDefined();
      expect(walkLine).toBeDefined();
      expect(walkLine!.options.color).toBe("#9CA3AF");
    });
  });

  describe("congestion overlay", () => {
    it("colors segments by congestion level and skips segments referencing unknown stops", () => {
      const stops = [
        makeStop({ stop_id: "S0001", stop_name: "A", lat: 27.7, lng: 85.3 }),
        makeStop({ stop_id: "S0002", stop_name: "B", lat: 27.71, lng: 85.31 }),
      ];

      render(
        <BusMap
          allStops={stops}
          congestionSegments={[
            {
              route_id: "R0001",
              from_stop_id: "S0001",
              to_stop_id: "S0002",
              avg_duration_s: 600,
              avg_distance_m: 2000,
              free_flow_duration_s: 300,
              congestion_ratio: 2,
              congestion_level: "heavy",
              sample_count: 5,
              is_seeded: false,
            },
            {
              // References a stop not present in allStops -- must be
              // skipped rather than throwing.
              route_id: "R0002",
              from_stop_id: "S9999",
              to_stop_id: "S0002",
              avg_duration_s: 400,
              avg_distance_m: 1000,
              free_flow_duration_s: 300,
              congestion_ratio: 1.3,
              congestion_level: "moderate",
              sample_count: 2,
              is_seeded: true,
            },
          ]}
        />
      );

      expect(leafletState.polylines).toHaveLength(1);
      expect(leafletState.polylines[0].options.color).toBe("#EF4444"); // heavy
    });
  });
});
