import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import RouteDetailPage from "@/app/routes/[routeId]/page";
import { ApiError, getFare, getRoute, getRouteGeometry, getRouteStops } from "@/lib/api";
import { FareOut, RouteSummary } from "@/types/route";

const navParams = vi.hoisted(() => ({ routeId: "R0001" }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ routeId: navParams.routeId }),
}));

vi.mock("next/dynamic", () => ({
  default: () => () => null,
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getRoute: vi.fn(),
    getRouteStops: vi.fn(),
    getRouteGeometry: vi.fn(),
    getFare: vi.fn(),
  };
});

vi.mock("@/components/user/SuggestionBox", () => ({
  default: () => null,
}));

const mockedGetRoute = vi.mocked(getRoute);
const mockedGetRouteStops = vi.mocked(getRouteStops);
const mockedGetRouteGeometry = vi.mocked(getRouteGeometry);
const mockedGetFare = vi.mocked(getFare);

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

function makeFare(overrides: Partial<FareOut> = {}): FareOut {
  return {
    fare_id: "F0001",
    min_distance_km: 0,
    max_distance_km: 10,
    fare_npr_min: 30,
    fare_npr_max: 35,
    student_discount_pct: null,
    verification_note: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  navParams.routeId = "R0001";
  document.title = "";
  mockedGetRouteStops.mockResolvedValue([]);
  mockedGetRouteGeometry.mockResolvedValue({
    geometry: { type: "LineString", coordinates: [] },
    distance_m: 0,
    duration_s: 0,
  });
  mockedGetFare.mockResolvedValue(makeFare());
});

afterEach(cleanup);

describe("RouteDetailPage distance disclosure", () => {
  it("shows a real OSRM road distance with no qualifier or fare caveat", async () => {
    mockedGetRoute.mockResolvedValue(makeRoute({ osrm_distance_km: 5.2, approx_distance_km: 4.8 }));

    render(<RouteDetailPage />);

    expect(await screen.findByText(/5\.2 km/)).toBeInTheDocument();
    expect(screen.queryByText(/approx\./)).not.toBeInTheDocument();
    expect(screen.queryByText(/approximate distance/)).not.toBeInTheDocument();
    // The fare lookup runs in a passive effect after the distance commits, so
    // wait for it rather than asserting immediately after findByText.
    await waitFor(() => expect(mockedGetFare).toHaveBeenCalledWith(5.2));
  });

  it("marks an approximate distance and discloses the approximate fare input", async () => {
    mockedGetRoute.mockResolvedValue(makeRoute({ osrm_distance_km: null, approx_distance_km: 4.8 }));

    render(<RouteDetailPage />);

    expect(await screen.findByText(/4\.8 km \(approx\.\)/)).toBeInTheDocument();
    expect(
      await screen.findByText(/estimated from the route's approximate distance/)
    ).toBeInTheDocument();
    await waitFor(() => expect(mockedGetFare).toHaveBeenCalledWith(4.8));
  });

  it("shows no distance and no fare when every value is invalid", async () => {
    mockedGetRoute.mockResolvedValue(makeRoute({ osrm_distance_km: 0, approx_distance_km: -2 }));

    render(<RouteDetailPage />);

    expect(await screen.findByText("Test Route")).toBeInTheDocument();
    expect(screen.queryByText(/\d+\.\d km/)).not.toBeInTheDocument();
    expect(screen.queryByText(/approximate distance/)).not.toBeInTheDocument();
    expect(mockedGetFare).not.toHaveBeenCalled();
  });
});

describe("RouteDetailPage document title", () => {
  it("identifies the requested route while loading", () => {
    mockedGetRoute.mockReturnValue(new Promise(() => {}));

    render(<RouteDetailPage />);

    expect(document.title).toBe("Route R0001 — Kathmandu Bus Route Finder");
  });

  it("uses the short and long name once the route has loaded", async () => {
    mockedGetRoute.mockResolvedValue(makeRoute({ short_name: "26", route_name: "Ring Road" }));

    render(<RouteDetailPage />);

    expect(await screen.findByText(/Ring Road/)).toBeInTheDocument();
    // findByText resolves on the DOM commit; the passive title effect may not
    // have flushed yet under load, so wait for it.
    await waitFor(() => expect(document.title).toBe("26 — Ring Road — Kathmandu Bus Route Finder"));
  });

  it("falls back to the long name when there is no short name", async () => {
    mockedGetRoute.mockResolvedValue(makeRoute({ short_name: null, route_name: "Test Route" }));

    render(<RouteDetailPage />);

    expect(await screen.findByText("Test Route")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Test Route — Kathmandu Bus Route Finder"));
  });

  it("marks a missing route as not found", async () => {
    mockedGetRoute.mockRejectedValue(new ApiError("not found", "http", 404));

    render(<RouteDetailPage />);

    expect(await screen.findByText(/doesn't exist/)).toBeInTheDocument();
    await waitFor(() =>
      expect(document.title).toBe("Route R0001 not found — Kathmandu Bus Route Finder")
    );
  });

  it("marks a failed load", async () => {
    mockedGetRoute.mockRejectedValue(new ApiError("network down", "network"));

    render(<RouteDetailPage />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await waitFor(() =>
      expect(document.title).toBe("Route R0001 — failed to load — Kathmandu Bus Route Finder")
    );
  });

  it("does not keep a previous route's title when the id changes", async () => {
    mockedGetRoute.mockResolvedValue(
      makeRoute({ route_id: "R0001", short_name: "26", route_name: "Ring Road" })
    );

    const { rerender } = render(<RouteDetailPage />);
    expect(await screen.findByText(/Ring Road/)).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("26 — Ring Road — Kathmandu Bus Route Finder"));

    mockedGetRoute.mockReturnValue(new Promise(() => {}));
    navParams.routeId = "R0002";
    rerender(<RouteDetailPage />);

    await waitFor(() => expect(document.title).toBe("Route R0002 — Kathmandu Bus Route Finder"));
  });
});
