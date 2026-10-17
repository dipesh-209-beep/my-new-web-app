import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import StopDetailPage from "@/app/stops/[stopId]/page";
import { ApiError, getStop, getStopRoutes } from "@/lib/api";
import { RouteOut, Stop } from "@/types/route";

const navParams = vi.hoisted(() => ({ stopId: "S0001" }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ stopId: navParams.stopId }),
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
    getStop: vi.fn(),
    getStopRoutes: vi.fn(),
  };
});

vi.mock("@/components/user/SuggestionBox", () => ({
  default: () => null,
}));

const mockedGetStop = vi.mocked(getStop);
const mockedGetStopRoutes = vi.mocked(getStopRoutes);

function makeStop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: "S0001",
    stop_name: "Ratna Park",
    lat: 27.7,
    lng: 85.3,
    zone: null,
    district: "Kathmandu",
    aliases: null,
    ward: null,
    landmark: null,
    is_major_stop: true,
    is_interchange: false,
    status: "active",
    ...overrides,
  };
}

function makeRouteOut(overrides: Partial<RouteOut> = {}): RouteOut {
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

beforeEach(() => {
  vi.clearAllMocks();
  navParams.stopId = "S0001";
  document.title = "";
  mockedGetStopRoutes.mockResolvedValue([]);
});

afterEach(cleanup);

describe("StopDetailPage document title", () => {
  it("identifies the requested stop while loading", () => {
    mockedGetStop.mockReturnValue(new Promise(() => {}));

    render(<StopDetailPage />);

    expect(document.title).toBe("Stop S0001 — Kathmandu Bus Route Finder");
  });

  it("uses the stop name once the stop has loaded", async () => {
    mockedGetStop.mockResolvedValue(makeStop({ stop_name: "Ratna Park" }));

    render(<StopDetailPage />);

    expect(await screen.findByText("Ratna Park")).toBeInTheDocument();
    // findByText resolves on the DOM commit; the passive title effect may not
    // have flushed yet under load, so wait for it.
    await waitFor(() => expect(document.title).toBe("Ratna Park — Kathmandu Bus Route Finder"));
  });

  it("marks a missing stop as not found", async () => {
    mockedGetStop.mockRejectedValue(new ApiError("not found", "http", 404));

    render(<StopDetailPage />);

    expect(await screen.findByText(/doesn't exist/)).toBeInTheDocument();
    await waitFor(() =>
      expect(document.title).toBe("Stop S0001 not found — Kathmandu Bus Route Finder")
    );
  });

  it("marks a failed load", async () => {
    mockedGetStop.mockRejectedValue(new ApiError("network down", "network"));

    render(<StopDetailPage />);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await waitFor(() =>
      expect(document.title).toBe("Stop S0001 — failed to load — Kathmandu Bus Route Finder")
    );
  });

  it("does not keep a previous stop's title when the id changes", async () => {
    mockedGetStop.mockResolvedValue(makeStop({ stop_id: "S0001", stop_name: "Ratna Park" }));

    const { rerender } = render(<StopDetailPage />);
    expect(await screen.findByText("Ratna Park")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Ratna Park — Kathmandu Bus Route Finder"));

    mockedGetStop.mockReturnValue(new Promise(() => {}));
    navParams.stopId = "S0002";
    rerender(<StopDetailPage />);

    await waitFor(() => expect(document.title).toBe("Stop S0002 — Kathmandu Bus Route Finder"));
  });
});

describe("StopDetailPage serving routes", () => {
  it("lists the routes that serve the stop", async () => {
    mockedGetStop.mockResolvedValue(makeStop());
    mockedGetStopRoutes.mockResolvedValue([makeRouteOut({ route_name: "Ring Road" })]);

    render(<StopDetailPage />);

    expect(await screen.findByText("Ring Road")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the genuine empty state when no routes serve the stop", async () => {
    mockedGetStop.mockResolvedValue(makeStop());
    mockedGetStopRoutes.mockResolvedValue([]);

    render(<StopDetailPage />);

    expect(await screen.findByText(/No routes currently list this stop/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows an error instead of implying no routes serve the stop when the request fails", async () => {
    mockedGetStop.mockResolvedValue(makeStop());
    mockedGetStopRoutes.mockRejectedValue(new ApiError("network down", "network"));

    render(<StopDetailPage />);

    expect(await screen.findByText("Ratna Park")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /couldn't load the routes serving this stop/i
    );
    expect(screen.queryByText(/No routes currently list this stop/i)).toBeNull();
  });

  it("keeps the stop's own details and origin/destination actions on a serving-routes failure", async () => {
    mockedGetStop.mockResolvedValue(makeStop());
    mockedGetStopRoutes.mockRejectedValue(new ApiError("network down", "network"));

    render(<StopDetailPage />);

    expect(await screen.findByText("Ratna Park")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set as origin" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set as destination" })).toBeInTheDocument();
  });

  it("Retry reloads only the serving routes and renders recovery", async () => {
    mockedGetStop.mockResolvedValue(makeStop());
    mockedGetStopRoutes
      .mockRejectedValueOnce(new ApiError("network down", "network"))
      .mockResolvedValueOnce([makeRouteOut({ route_name: "Recovered Route" })]);

    render(<StopDetailPage />);

    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText("Recovered Route")).toBeInTheDocument();
    expect(mockedGetStopRoutes).toHaveBeenCalledTimes(2);
    // The serving-routes retry must not reload the stop's own details.
    expect(mockedGetStop).toHaveBeenCalledTimes(1);
  });
});
