import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

function mockBrowser(
  routes: RouteSummary[],
  overrides: Partial<ReturnType<typeof useRouteBrowser>> = {}
) {
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
    ...overrides,
  } as ReturnType<typeof useRouteBrowser>);
}

describe("RoutesPage search field labelling", () => {
  it("names the search field with real label markup, independent of its placeholder", () => {
    mockBrowser([]);

    render(<RoutesPage />);

    const field = screen.getByRole("textbox", { name: "Search by route name or number…" });
    expect(field).toBeInTheDocument();

    // The accessible name must survive without the placeholder attribute --
    // dom-accessibility-api (which getByRole name queries use) derives no name
    // from placeholder, so this fails if only a placeholder is present.
    field.removeAttribute("placeholder");
    expect(screen.getByRole("textbox", { name: "Search by route name or number…" })).toBe(field);
  });
});

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

describe("RoutesPage list status", () => {
  it("announces the matching route count", () => {
    mockBrowser([makeRoute()], { total: 1 });

    render(<RoutesPage />);

    expect(screen.getByRole("status")).toHaveTextContent("1 route found.");
  });

  it("announces the total and how many are shown while more pages remain", () => {
    mockBrowser([makeRoute(), makeRoute({ route_id: "R0002" })], { total: 120, hasMore: true });

    render(<RoutesPage />);

    expect(screen.getByRole("status")).toHaveTextContent("120 routes found, 2 shown.");
  });

  it("announces a no-match query result", () => {
    mockBrowser([], { searchQuery: "zzz", total: 0 });

    render(<RoutesPage />);

    expect(screen.getByRole("status")).toHaveTextContent('No routes match "zzz".');
  });

  it("announces that more routes are loading", () => {
    mockBrowser([makeRoute()], { loadingMore: true, total: 120, hasMore: true });

    render(<RoutesPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading more routes…");
  });

  it("stays silent while a query is still loading with no rows yet", () => {
    mockBrowser([], { loading: true, searchQuery: "zzz", total: 0 });

    render(<RoutesPage />);

    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("moves from no-match to a count when a later query matches", () => {
    mockBrowser([], { searchQuery: "zzz", total: 0 });
    const { rerender } = render(<RoutesPage />);
    expect(screen.getByRole("status")).toHaveTextContent('No routes match "zzz".');

    mockBrowser([makeRoute()], { searchQuery: "rat", total: 1 });
    rerender(<RoutesPage />);

    expect(screen.getByRole("status")).toHaveTextContent("1 route found.");
  });
});

describe("RoutesPage Load more focus", () => {
  it("keeps Load more focusable while a page is loading instead of dropping focus", () => {
    const loadMore = vi.fn();
    mockBrowser([makeRoute()], { loadingMore: true, total: 120, hasMore: true, loadMore });

    render(<RoutesPage />);

    const button = screen.getByRole("button", { name: /loading/i });
    // aria-disabled (focus preserved), not the disabled attribute.
    expect(button).not.toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
  });

  it("moves focus to the list when the final page loads and Load more unmounts", () => {
    const loadMore = vi.fn();
    mockBrowser([makeRoute()], { total: 1, hasMore: true, loadMore });
    const { rerender } = render(<RoutesPage />);

    const button = screen.getByRole("button", { name: /load more/i });
    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);
    expect(loadMore).toHaveBeenCalledTimes(1);

    // The last page arrives: hasMore flips false and the button unmounts.
    mockBrowser([makeRoute(), makeRoute({ route_id: "R0002" })], {
      total: 2,
      hasMore: false,
      loadMore,
    });
    rerender(<RoutesPage />);

    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
    expect(screen.getByRole("region", { name: "Routes" })).toHaveFocus();
  });

  it("does not steal focus from the search field when a new search resolves while pagination is in flight", () => {
    const loadMore = vi.fn();
    mockBrowser([makeRoute()], { total: 120, hasMore: true, loadingMore: false, loadMore });
    const { rerender } = render(<RoutesPage />);

    // 1. Activate Load more -- this arms the focus restore and starts a page
    //    request that has not come back yet.
    const button = screen.getByRole("button", { name: /load more/i });
    button.focus();
    fireEvent.click(button);
    expect(loadMore).toHaveBeenCalledTimes(1);

    mockBrowser([makeRoute()], { total: 120, hasMore: true, loadingMore: true, loadMore });
    rerender(<RoutesPage />);

    // 2. The user starts a new search; focus is in the search field.
    const field = screen.getByRole("textbox", { name: "Search by route name or number…" });
    field.focus();
    expect(field).toHaveFocus();

    // 3. The search resolves with no further pages, while the earlier
    //    pagination request is still in flight.
    mockBrowser([makeRoute()], {
      searchQuery: "rat",
      total: 1,
      hasMore: false,
      loadingMore: true,
      loadMore,
    });
    rerender(<RoutesPage />);

    // 4. Pagination completing must leave the search field focused rather than
    //    pulling focus to the list region.
    expect(screen.getByRole("region", { name: "Routes" })).not.toHaveFocus();
    expect(field).toHaveFocus();

    // The stale activation is consumed, so a later settle cannot steal it either.
    mockBrowser([makeRoute()], {
      searchQuery: "rat",
      total: 1,
      hasMore: false,
      loadingMore: false,
      loadMore,
    });
    rerender(<RoutesPage />);
    expect(field).toHaveFocus();
  });
});
