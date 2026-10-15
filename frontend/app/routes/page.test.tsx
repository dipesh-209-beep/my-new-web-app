import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import RoutesPage from "@/app/routes/page";
import { useRouteBrowser } from "@/hooks/useRouteBrowser";
import { RouteSummary } from "@/types/route";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/hooks/useRouteBrowser", () => ({
  useRouteBrowser: vi.fn(),
}));

const mockedUseRouteBrowser = vi.mocked(useRouteBrowser);

afterEach(cleanup);

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

function mockBrowser(routes: RouteSummary[]) {
  mockedUseRouteBrowser.mockReturnValue({
    routes,
    total: routes.length,
    loading: false,
    loadingMore: false,
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
  } as ReturnType<typeof useRouteBrowser>);
}

describe("RoutesPage distance disclosure", () => {
  it("shows a real OSRM road distance without any qualifier", () => {
    mockBrowser([makeRoute({ osrm_distance_km: 5.234, approx_distance_km: 4.8 })]);

    render(<RoutesPage />);

    expect(screen.getByText(/5\.2 km/)).toBeInTheDocument();
    expect(screen.queryByText(/approx\./)).not.toBeInTheDocument();
  });

  it("marks an approximate fallback so it can't read as a real road distance", () => {
    mockBrowser([makeRoute({ osrm_distance_km: null, approx_distance_km: 4.76 })]);

    render(<RoutesPage />);

    expect(screen.getByText(/4\.8 km \(approx\.\)/)).toBeInTheDocument();
  });

  it("omits the distance entirely when neither value is valid", () => {
    mockBrowser([makeRoute({ osrm_distance_km: 0, approx_distance_km: -2 })]);

    render(<RoutesPage />);

    expect(screen.getByText("Test Route")).toBeInTheDocument();
    expect(screen.queryByText(/\d+\.\d km/)).not.toBeInTheDocument();
  });
});
