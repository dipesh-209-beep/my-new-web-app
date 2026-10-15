import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import RouteDetailPage from "@/app/routes/[routeId]/page";
import { getFare, getRoute, getRouteGeometry, getRouteStops } from "@/lib/api";
import { FareOut, RouteSummary } from "@/types/route";

vi.mock("next/navigation", () => ({
  useParams: () => ({ routeId: "R0001" }),
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
    expect(mockedGetFare).toHaveBeenCalledWith(5.2);
  });

  it("marks an approximate distance and discloses the approximate fare input", async () => {
    mockedGetRoute.mockResolvedValue(makeRoute({ osrm_distance_km: null, approx_distance_km: 4.8 }));

    render(<RouteDetailPage />);

    expect(await screen.findByText(/4\.8 km \(approx\.\)/)).toBeInTheDocument();
    expect(
      await screen.findByText(/estimated from the route's approximate distance/)
    ).toBeInTheDocument();
    expect(mockedGetFare).toHaveBeenCalledWith(4.8);
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
