import { describe, expect, it, vi, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useRouteBrowser } from "@/hooks/useRouteBrowser";
import * as api from "@/lib/api";
import { RouteSummary, RouteStopEntry, RouteGeometry, RouteDirection } from "@/types/route";

function makeRoute(overrides: Partial<RouteSummary> = {}): RouteSummary {
  return {
    route_id: "R1",
    route_name: "Test Route",
    short_name: "T1",
    vehicle_type: "bus",
    start_stop_id: "S0001",
    end_stop_id: "S0002",
    total_stops: 10,
    is_bidirectional: true,
    approx_distance_km: 5.0,
    osrm_distance_km: 5.2,
    status: "active",
    operator: null,
    ...overrides,
  };
}

function makeStops(count: number, prefix = "S"): RouteStopEntry[] {
  return Array.from({ length: count }, (_, i) => ({
    sequence_no: i + 1,
    stop: {
      stop_id: `${prefix}${String(i + 1).padStart(4, "0")}`,
      stop_name: `Stop ${prefix}${i + 1}`,
      lat: 27.7 + i * 0.001,
      lng: 85.3 + i * 0.001,
      zone: null,
      district: null,
      is_major_stop: false,
      is_interchange: false,
      status: "active",
    },
  }));
}

function makeGeometry(overrides: Partial<RouteGeometry> = {}): RouteGeometry {
  return {
    geometry: {
      type: "LineString",
      coordinates: [
        [85.3, 27.7],
        [85.31, 27.71],
      ],
    },
    distance_m: 1000,
    duration_s: 300,
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useRouteBrowser", () => {
  it("toggleVisible fetches with direction 'forward' by default", async () => {
    const route = makeRoute({ route_id: "R1", is_bidirectional: true });
    const forwardStops = makeStops(5, "F");
    const forwardGeometry = makeGeometry({ distance_m: 1000 });

    vi.spyOn(api, "getRouteStops").mockResolvedValue(forwardStops);
    vi.spyOn(api, "getRouteGeometry").mockResolvedValue(forwardGeometry);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 1, limit: 50, offset: 0, items: [route] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.routes).toHaveLength(1));

    await act(async () => {
      await result.current.toggleVisible(route);
    });

    expect(result.current.visibleRouteId).toBe("R1");
    expect(result.current.direction).toBe("forward");
    expect(api.getRouteStops).toHaveBeenCalledWith("R1", "forward");
    expect(api.getRouteGeometry).toHaveBeenCalledWith("R1", "forward");
    expect(result.current.visibleRouteStops).toEqual(forwardStops);
    expect(result.current.visibleRouteGeometry).toEqual(forwardGeometry);
  });

  it("setDirection updates direction state but does not trigger fetches; fetch occurs on next showRouteById", async () => {
    const route = makeRoute({ route_id: "R1", is_bidirectional: true });
    const forwardStops = makeStops(5, "F");
    const reverseStops = makeStops(5, "R");
    const forwardGeometry = makeGeometry({ distance_m: 1000 });
    const reverseGeometry = makeGeometry({ distance_m: 1200 });

    vi.spyOn(api, "getRouteStops")
      .mockResolvedValueOnce(forwardStops)
      .mockResolvedValueOnce(reverseStops);
    vi.spyOn(api, "getRouteGeometry")
      .mockResolvedValueOnce(forwardGeometry)
      .mockResolvedValueOnce(reverseGeometry);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 1, limit: 50, offset: 0, items: [route] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.toggleVisible(route);
    });

    expect(result.current.direction).toBe("forward");
    expect(api.getRouteStops).toHaveBeenCalledTimes(1);
    expect(api.getRouteGeometry).toHaveBeenCalledTimes(1);

    // setDirection only updates state, no fetch triggered
    await act(async () => {
      result.current.setDirection("reverse");
    });

    expect(result.current.direction).toBe("reverse");
    expect(api.getRouteStops).toHaveBeenCalledTimes(1);
    expect(api.getRouteGeometry).toHaveBeenCalledTimes(1);

    // Toggle off first, then showRouteById respects current direction - triggers fetch with reverse
    await act(async () => {
      await result.current.toggleVisible(route); // toggle off
    });
    await act(async () => {
      await result.current.showRouteById("R1");
    });

    await waitFor(() => expect(result.current.visibleRouteStopsLoading).toBe(false));
    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    expect(api.getRouteStops).toHaveBeenCalledTimes(2);
    expect(api.getRouteStops).toHaveBeenNthCalledWith(2, "R1", "reverse");
    expect(api.getRouteGeometry).toHaveBeenCalledTimes(2);
    expect(api.getRouteGeometry).toHaveBeenNthCalledWith(2, "R1", "reverse");
    expect(result.current.visibleRouteStops).toEqual(reverseStops);
    expect(result.current.visibleRouteGeometry).toEqual(reverseGeometry);
  });

  it("switching back to 'forward' after caching both directions does NOT re-fetch (uses cache)", async () => {
    const route = makeRoute({ route_id: "R1", is_bidirectional: true });
    const forwardStops = makeStops(5, "F");
    const reverseStops = makeStops(5, "R");
    const forwardGeometry = makeGeometry({ distance_m: 1000 });
    const reverseGeometry = makeGeometry({ distance_m: 1200 });

    vi.spyOn(api, "getRouteStops")
      .mockResolvedValueOnce(forwardStops)
      .mockResolvedValueOnce(reverseStops);
    vi.spyOn(api, "getRouteGeometry")
      .mockResolvedValueOnce(forwardGeometry)
      .mockResolvedValueOnce(reverseGeometry);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 1, limit: 50, offset: 0, items: [route] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // First toggle - fetches forward
    await act(async () => {
      await result.current.toggleVisible(route);
    });

    // Change direction, toggle off, use showRouteById to fetch reverse
    await act(async () => {
      result.current.setDirection("reverse");
    });
    await act(async () => {
      await result.current.toggleVisible(route); // toggle off
    });
    await act(async () => {
      await result.current.showRouteById("R1");
    });

    await waitFor(() => expect(result.current.visibleRouteStopsLoading).toBe(false));
    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    const stopsCallCountAfterReverse = (api.getRouteStops as vi.Mock).mock.calls.length;
    const geometryCallCountAfterReverse = (api.getRouteGeometry as vi.Mock).mock.calls.length;

    // Switch back to forward, toggle off, use showRouteById - should use cache, no new fetch
    await act(async () => {
      result.current.setDirection("forward");
    });
    await act(async () => {
      await result.current.toggleVisible(route); // toggle off
    });
    await act(async () => {
      await result.current.showRouteById("R1");
    });

    await waitFor(() => expect(result.current.visibleRouteStopsLoading).toBe(false));
    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    expect(result.current.direction).toBe("forward");
    expect(result.current.visibleRouteStops).toEqual(forwardStops);
    expect(result.current.visibleRouteGeometry).toEqual(forwardGeometry);
    expect((api.getRouteStops as vi.Mock).mock.calls.length).toBe(stopsCallCountAfterReverse);
    expect((api.getRouteGeometry as vi.Mock).mock.calls.length).toBe(geometryCallCountAfterReverse);
  });

  it("showRouteById respects the current direction state, not always 'forward'", async () => {
    const route = makeRoute({ route_id: "R1", is_bidirectional: true });
    const forwardStops = makeStops(5, "F");
    const forwardGeometry = makeGeometry({ distance_m: 1000 });

    vi.spyOn(api, "getRouteStops").mockResolvedValue(forwardStops);
    vi.spyOn(api, "getRouteGeometry").mockResolvedValue(forwardGeometry);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 1, limit: 50, offset: 0, items: [route] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.toggleVisible(route);
    });

    // Change direction to reverse
    await act(async () => {
      result.current.setDirection("reverse");
    });

    const route2 = makeRoute({ route_id: "R2", is_bidirectional: true });
    const route2ReverseStops = makeStops(3, "R2");
    const route2ReverseGeometry = makeGeometry({ distance_m: 900 });

    vi.spyOn(api, "getRouteStops").mockResolvedValue(route2ReverseStops);
    vi.spyOn(api, "getRouteGeometry").mockResolvedValue(route2ReverseGeometry);

    await act(async () => {
      await result.current.showRouteById("R2");
    });

    await waitFor(() => expect(result.current.visibleRouteStopsLoading).toBe(false));
    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    expect(result.current.visibleRouteId).toBe("R2");
    expect(result.current.direction).toBe("reverse");
    expect(api.getRouteStops).toHaveBeenCalledWith("R2", "reverse");
    expect(api.getRouteGeometry).toHaveBeenCalledWith("R2", "reverse");
    expect(result.current.visibleRouteStops).toEqual(route2ReverseStops);
    expect(result.current.visibleRouteGeometry).toEqual(route2ReverseGeometry);
  });

  it("stopsRequestIdRef race guard: slower showRouteById for route A resolving after newer showRouteById for route B must not overwrite B's result", async () => {
    const routeA = makeRoute({ route_id: "R1", is_bidirectional: true });
    const routeB = makeRoute({ route_id: "R2", is_bidirectional: true });
    const stopsA = makeStops(5, "A");
    const stopsB = makeStops(5, "B");
    const geometryA = makeGeometry({ distance_m: 1000 });
    const geometryB = makeGeometry({ distance_m: 1200 });

    let resolveStopsA!: (v: RouteStopEntry[]) => void;
    let resolveStopsB!: (v: RouteStopEntry[]) => void;
    let resolveGeometryA!: (v: RouteGeometry) => void;
    let resolveGeometryB!: (v: RouteGeometry) => void;

    const stopsAPromise = new Promise<RouteStopEntry[]>((resolve) => {
      resolveStopsA = resolve;
    });
    const stopsBPromise = new Promise<RouteStopEntry[]>((resolve) => {
      resolveStopsB = resolve;
    });
    const geometryAPromise = new Promise<RouteGeometry>((resolve) => {
      resolveGeometryA = resolve;
    });
    const geometryBPromise = new Promise<RouteGeometry>((resolve) => {
      resolveGeometryB = resolve;
    });

    const getRouteStopsSpy = vi
      .spyOn(api, "getRouteStops")
      .mockImplementationOnce(() => stopsAPromise)
      .mockImplementationOnce(() => stopsBPromise);
    const getRouteGeometrySpy = vi
      .spyOn(api, "getRouteGeometry")
      .mockImplementationOnce(() => geometryAPromise)
      .mockImplementationOnce(() => geometryBPromise);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 2, limit: 50, offset: 0, items: [routeA, routeB] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // Fire showRouteById for route A (slow)
    let showAPromise: Promise<void>;
    act(() => {
      showAPromise = result.current.showRouteById("R1");
    });

    // Immediately fire showRouteById for route B (fast)
    let showBPromise: Promise<void>;
    await act(async () => {
      showBPromise = result.current.showRouteById("R2");
    });

    // Resolve slow A request
    await act(async () => {
      resolveStopsA(stopsA);
      resolveGeometryA(geometryA);
      await showAPromise;
    });

    // Resolve fast B request
    await act(async () => {
      resolveStopsB(stopsB);
      resolveGeometryB(geometryB);
      await showBPromise;
    });

    await waitFor(() => expect(result.current.visibleRouteStopsLoading).toBe(false));
    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    // Route B's result should win (newer requestId)
    expect(result.current.visibleRouteId).toBe("R2");
    expect(result.current.visibleRouteStops).toEqual(stopsB);
    expect(result.current.visibleRouteGeometry).toEqual(geometryB);
    expect(getRouteStopsSpy).toHaveBeenCalledTimes(2);
    expect(getRouteGeometrySpy).toHaveBeenCalledTimes(2);
  });

  it("geometryRequestIdRef race guard: slower showRouteById geometry for route A resolving after newer for route B must not overwrite B's result", async () => {
    const routeA = makeRoute({ route_id: "R1", is_bidirectional: true });
    const routeB = makeRoute({ route_id: "R2", is_bidirectional: true });
    const stopsA = makeStops(5, "A");
    const stopsB = makeStops(5, "B");
    const geometryA = makeGeometry({ distance_m: 1000 });
    const geometryB = makeGeometry({ distance_m: 1200 });

    let resolveGeometryA!: (v: RouteGeometry) => void;
    let resolveGeometryB!: (v: RouteGeometry) => void;

    const geometryAPromise = new Promise<RouteGeometry>((resolve) => {
      resolveGeometryA = resolve;
    });
    const geometryBPromise = new Promise<RouteGeometry>((resolve) => {
      resolveGeometryB = resolve;
    });

    vi.spyOn(api, "getRouteStops")
      .mockResolvedValueOnce(stopsA)
      .mockResolvedValueOnce(stopsB);
    const getRouteGeometrySpy = vi
      .spyOn(api, "getRouteGeometry")
      .mockImplementationOnce(() => geometryAPromise)
      .mockImplementationOnce(() => geometryBPromise);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 2, limit: 50, offset: 0, items: [routeA, routeB] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // Fire showRouteById for route A (slow geometry)
    let showAPromise: Promise<void>;
    act(() => {
      showAPromise = result.current.showRouteById("R1");
    });

    // Immediately fire showRouteById for route B (fast geometry)
    let showBPromise: Promise<void>;
    await act(async () => {
      showBPromise = result.current.showRouteById("R2");
    });

    // Resolve slow A geometry
    await act(async () => {
      resolveGeometryA(geometryA);
      await showAPromise;
    });

    // Resolve fast B geometry
    await act(async () => {
      resolveGeometryB(geometryB);
      await showBPromise;
    });

    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    // Route B's geometry should win
    expect(result.current.visibleRouteId).toBe("R2");
    expect(result.current.visibleRouteGeometry).toEqual(geometryB);
    expect(getRouteGeometrySpy).toHaveBeenCalledTimes(2);
  });

  it("stopsRequestIdRef race guard: toggleVisible then showRouteById - newer showRouteById wins", async () => {
    const routeA = makeRoute({ route_id: "R1", is_bidirectional: true });
    const routeB = makeRoute({ route_id: "R2", is_bidirectional: true });
    const stopsA = makeStops(5, "A");
    const stopsB = makeStops(5, "B");
    const geometryA = makeGeometry({ distance_m: 1000 });
    const geometryB = makeGeometry({ distance_m: 1200 });

    let resolveStopsA!: (v: RouteStopEntry[]) => void;
    let resolveStopsB!: (v: RouteStopEntry[]) => void;

    const stopsAPromise = new Promise<RouteStopEntry[]>((resolve) => {
      resolveStopsA = resolve;
    });
    const stopsBPromise = new Promise<RouteStopEntry[]>((resolve) => {
      resolveStopsB = resolve;
    });

    const getRouteStopsSpy = vi
      .spyOn(api, "getRouteStops")
      .mockImplementationOnce(() => stopsAPromise)
      .mockImplementationOnce(() => stopsBPromise);
    vi.spyOn(api, "getRouteGeometry")
      .mockResolvedValueOnce(geometryA)
      .mockResolvedValueOnce(geometryB);
    vi.spyOn(api, "getRoutes").mockResolvedValue({ total: 2, limit: 50, offset: 0, items: [routeA, routeB] });

    const { result } = renderHook(() => useRouteBrowser());

    await waitFor(() => expect(result.current.loading).toBe(false));

    // Fire toggleVisible for route A (slow)
    let togglePromise: Promise<void>;
    act(() => {
      togglePromise = result.current.toggleVisible(routeA);
    });

    // Immediately fire showRouteById for route B (fast)
    let showBPromise: Promise<void>;
    await act(async () => {
      showBPromise = result.current.showRouteById("R2");
    });

    // Resolve slow A request
    await act(async () => {
      resolveStopsA(stopsA);
      await togglePromise;
    });

    // Resolve fast B request
    await act(async () => {
      resolveStopsB(stopsB);
      await showBPromise;
    });

    await waitFor(() => expect(result.current.visibleRouteStopsLoading).toBe(false));
    await waitFor(() => expect(result.current.visibleRouteGeometryLoading).toBe(false));

    // Route B's result should win (newer requestId)
    expect(result.current.visibleRouteId).toBe("R2");
    expect(result.current.visibleRouteStops).toEqual(stopsB);
    expect(getRouteStopsSpy).toHaveBeenCalledTimes(2);
  });
});