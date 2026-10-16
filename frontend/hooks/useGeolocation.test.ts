import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useGeolocation } from "@/hooks/useGeolocation";
import * as api from "@/lib/api";
import { Stop } from "@/types/route";

function makeStop(id: string, name: string): Stop {
  return {
    stop_id: id,
    stop_name: name,
    lat: 27.7,
    lng: 85.3,
    zone: null,
    district: null,
    is_major_stop: false,
    is_interchange: false,
    status: "active",
  };
}

type SuccessCb = (pos: GeolocationPosition) => void;
type ErrorCb = (err: GeolocationPositionError) => void;

// jsdom has no geolocation; capture the callbacks so a test can decide when
// and how the browser "responds" (needed to observe the in-flight state and
// to overtake one request with another).
const pendingCalls: { success: SuccessCb; error: ErrorCb }[] = [];

function installGeolocation() {
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: {
      getCurrentPosition: vi.fn((success: SuccessCb, error: ErrorCb) => {
        pendingCalls.push({ success, error });
      }),
    },
  });
}

function position(lat: number, lng: number): GeolocationPosition {
  return { coords: { latitude: lat, longitude: lng } } as GeolocationPosition;
}

// The hook reads the PERMISSION_DENIED / TIMEOUT / POSITION_UNAVAILABLE
// constants off the error object itself, mirroring the browser API.
function geoError(code: number): GeolocationPositionError {
  return {
    code,
    PERMISSION_DENIED: 1,
    TIMEOUT: 2,
    POSITION_UNAVAILABLE: 3,
  } as unknown as GeolocationPositionError;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const walkingRoute = {
  geometry: { type: "LineString" as const, coordinates: [] as [number, number][] },
  distance_m: 300,
  duration_s: 240,
};

beforeEach(() => {
  delete (navigator as unknown as { geolocation?: unknown }).geolocation;
  pendingCalls.length = 0;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useGeolocation", () => {
  it("announces the nearest stop once it is found", async () => {
    installGeolocation();
    const stop = makeStop("S0001", "Ratnapark");
    vi.spyOn(api, "getNearbyStops").mockResolvedValue([stop]);
    vi.spyOn(api, "getWalkingRoute").mockResolvedValue(walkingRoute);
    const onStopFound = vi.fn();

    const { result } = renderHook(() => useGeolocation({ stops: [stop], onStopFound }));
    act(() => result.current.useMyLocation());
    expect(result.current.locateStatus).toBeNull();

    act(() => {
      pendingCalls[0].success(position(27.7, 85.3));
    });

    await waitFor(() => expect(result.current.locateStatus).toBe("Nearest stop found: Ratnapark."));
    expect(result.current.nearestStop).toEqual(stop);
    expect(result.current.locating).toBe(false);
    expect(onStopFound).toHaveBeenCalledWith("Ratnapark");
  });

  it("still announces the stop when the optional walking route fails", async () => {
    installGeolocation();
    const stop = makeStop("S0001", "Ratnapark");
    vi.spyOn(api, "getNearbyStops").mockResolvedValue([stop]);
    vi.spyOn(api, "getWalkingRoute").mockRejectedValue(new api.ApiError("foot down", "network"));

    const { result } = renderHook(() => useGeolocation({ stops: [stop] }));
    act(() => result.current.useMyLocation());
    act(() => {
      pendingCalls[0].success(position(27.7, 85.3));
    });

    await waitFor(() => expect(result.current.locateStatus).toBe("Nearest stop found: Ratnapark."));
    expect(result.current.walkingRoute).toBeNull();
  });

  it("clears the previous success when a repeat request starts, so it announces again", async () => {
    installGeolocation();
    const stop = makeStop("S0001", "Ratnapark");
    vi.spyOn(api, "getNearbyStops").mockResolvedValue([stop]);
    vi.spyOn(api, "getWalkingRoute").mockResolvedValue(walkingRoute);

    const { result } = renderHook(() => useGeolocation({ stops: [stop] }));
    act(() => result.current.useMyLocation());
    act(() => {
      pendingCalls[0].success(position(27.7, 85.3));
    });
    await waitFor(() => expect(result.current.locateStatus).toBe("Nearest stop found: Ratnapark."));

    act(() => result.current.useMyLocation());
    expect(result.current.locateStatus).toBeNull();

    act(() => {
      pendingCalls[1].success(position(27.7, 85.3));
    });
    await waitFor(() => expect(result.current.locateStatus).toBe("Nearest stop found: Ratnapark."));
  });

  it("does not announce success when no stop is near", async () => {
    installGeolocation();
    vi.spyOn(api, "getNearbyStops").mockResolvedValue([]);

    const { result } = renderHook(() => useGeolocation({ stops: [] }));
    act(() => result.current.useMyLocation());
    act(() => {
      pendingCalls[0].success(position(27.7, 85.3));
    });

    await waitFor(() => expect(result.current.locateError).toMatch(/no bus stops/i));
    expect(result.current.locateStatus).toBeNull();
  });

  it("announces the failure, never a success, when permission is denied", () => {
    installGeolocation();
    const getNearby = vi.spyOn(api, "getNearbyStops");

    const { result } = renderHook(() => useGeolocation({ stops: [] }));
    act(() => result.current.useMyLocation());
    act(() => pendingCalls[0].error(geoError(1)));

    expect(result.current.locateError).toMatch(/permission denied/i);
    expect(result.current.locateStatus).toBeNull();
    expect(result.current.locating).toBe(false);
    expect(getNearby).not.toHaveBeenCalled();
  });

  it("explains an unsupported browser and does not announce success", () => {
    const { result } = renderHook(() => useGeolocation({ stops: [] }));
    act(() => result.current.useMyLocation());

    expect(result.current.locateError).toMatch(/isn't available in this browser/i);
    expect(result.current.locateStatus).toBeNull();
    expect(result.current.locating).toBe(false);
  });

  it("announces only the latest nearest stop when a slow request is overtaken", async () => {
    installGeolocation();
    const slowStop = makeStop("S0001", "Ratnapark");
    const fastStop = makeStop("S0002", "Koteshwor");
    const slow = deferred<Stop[]>();
    const fast = deferred<Stop[]>();
    vi.spyOn(api, "getNearbyStops").mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);
    vi.spyOn(api, "getWalkingRoute").mockResolvedValue(walkingRoute);

    const { result } = renderHook(() => useGeolocation({ stops: [slowStop, fastStop] }));

    act(() => result.current.useMyLocation());
    act(() => {
      pendingCalls[0].success(position(27.7, 85.3));
    });
    act(() => result.current.useMyLocation());
    act(() => {
      pendingCalls[1].success(position(27.71, 85.31));
    });

    act(() => fast.resolve([fastStop]));
    await waitFor(() => expect(result.current.locateStatus).toBe("Nearest stop found: Koteshwor."));

    act(() => slow.resolve([slowStop]));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(result.current.locateStatus).toBe("Nearest stop found: Koteshwor.");
    expect(result.current.nearestStop).toEqual(fastStop);
  });
});
