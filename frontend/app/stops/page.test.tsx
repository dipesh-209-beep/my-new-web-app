import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import StopsPage from "@/app/stops/page";
import { useStops } from "@/hooks/useStops";
import { Stop } from "@/types/route";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/hooks/useStops", () => ({
  useStops: vi.fn(),
}));

const mockedUseStops = vi.mocked(useStops);

afterEach(cleanup);

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

function mockStops(stops: Stop[]) {
  mockedUseStops.mockReturnValue({ stops, loading: false, error: false, reload: vi.fn() });
}

describe("StopsPage search field labelling", () => {
  it("names the search field with real label markup, independent of its placeholder", () => {
    mockedUseStops.mockReturnValue({ stops: [], loading: false, error: false, reload: vi.fn() });

    render(<StopsPage />);

    const field = screen.getByRole("textbox", { name: "Search stops…" });
    expect(field).toBeInTheDocument();

    // The accessible name must survive without the placeholder attribute --
    // dom-accessibility-api (which getByRole name queries use) derives no name
    // from placeholder, so this fails if only a placeholder is present.
    field.removeAttribute("placeholder");
    expect(screen.getByRole("textbox", { name: "Search stops…" })).toBe(field);
  });
});

describe("StopsPage matches the autocomplete's search semantics", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const type = (value: string) =>
    fireEvent.change(screen.getByRole("textbox"), { target: { value } });

  const settle = () =>
    act(() => {
      vi.advanceTimersByTime(300);
    });

  it("finds the dataset name from a compact query like 'ratnapark'", () => {
    // The old raw `stop_name.toLowerCase().includes()` filter returned nothing
    // for "ratnapark" while the autocomplete returned Ratna Park.
    mockStops([
      makeStop({ stop_id: "S0013", stop_name: "Ratna Park" }),
      makeStop({ stop_id: "S0023", stop_name: "Kalimati" }),
    ]);

    render(<StopsPage />);
    type("ratnapark");
    settle();

    expect(screen.getByRole("status")).toHaveTextContent('1 stop matches "ratnapark".');
    expect(screen.getByText("Ratna Park")).toBeInTheDocument();
    expect(screen.queryByText("Kalimati")).not.toBeInTheDocument();
  });

  it("finds 'Koteshwor Stop' from the 'koteshwar' spelling", () => {
    mockStops([
      makeStop({ stop_id: "S0107", stop_name: "Koteshwor Stop" }),
      makeStop({ stop_id: "S0023", stop_name: "Kalimati" }),
    ]);

    render(<StopsPage />);
    type("koteshwar");
    settle();

    expect(screen.getByRole("status")).toHaveTextContent('1 stop matches "koteshwar".');
    expect(screen.getByText("Koteshwor Stop")).toBeInTheDocument();
    expect(screen.queryByText("Kalimati")).not.toBeInTheDocument();
  });

  it("searches a stop's alias, not just its name", () => {
    mockStops([
      makeStop({ stop_id: "S0071", stop_name: "पिङ्गलस्थान", aliases: "Pingalasthan" }),
      makeStop({ stop_id: "S0023", stop_name: "Kalimati" }),
    ]);

    render(<StopsPage />);
    type("pingalasthan");
    settle();

    expect(screen.getByRole("status")).toHaveTextContent('1 stop matches "pingalasthan".');
    expect(screen.getByText("पिङ्गलस्थान")).toBeInTheDocument();
  });

  it("does not match on district (consistent with the autocomplete)", () => {
    mockStops([
      makeStop({ stop_id: "S0013", stop_name: "Ratna Park", district: "KATHMANDU" }),
    ]);

    render(<StopsPage />);
    type("kathmandu");
    settle();

    expect(screen.getByRole("status")).toHaveTextContent('No stops match "kathmandu".');
  });
});

describe("StopsPage filter status", () => {
  beforeEach(() => {
    // Fake only the debounce timer, so React's own scheduling stays real and
    // the input events fire synchronously (no userEvent/timer interaction).
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const type = (value: string) =>
    fireEvent.change(screen.getByRole("textbox"), { target: { value } });

  const settle = () =>
    act(() => {
      vi.advanceTimersByTime(300);
    });

  it("stays silent until the filter settles, then announces the match count", () => {
    mockStops([
      makeStop({ stop_id: "S0001", stop_name: "Ratna Park" }),
      makeStop({ stop_id: "S0002", stop_name: "Kalimati" }),
    ]);

    render(<StopsPage />);

    type("rat");
    // Debounced: nothing is announced the instant the query changes, even
    // though the visible list has already filtered down to one stop.
    expect(screen.getByRole("status")).toBeEmptyDOMElement();

    settle();
    expect(screen.getByRole("status")).toHaveTextContent('1 stop matches "rat".');
  });

  it("announces when nothing matches", () => {
    mockStops([makeStop({ stop_name: "Ratna Park" })]);

    render(<StopsPage />);

    type("zzz");
    settle();

    expect(screen.getByRole("status")).toHaveTextContent('No stops match "zzz".');
  });

  it("announces recovery from no matches back to matches", () => {
    mockStops([makeStop({ stop_name: "Ratna Park" })]);

    render(<StopsPage />);

    type("zzz");
    settle();
    expect(screen.getByRole("status")).toHaveTextContent('No stops match "zzz".');

    type("rat");
    settle();
    expect(screen.getByRole("status")).toHaveTextContent('1 stop matches "rat".');
  });

  it("does not announce when the query is cleared", () => {
    mockStops([makeStop({ stop_name: "Ratna Park" })]);

    render(<StopsPage />);

    type("rat");
    settle();
    expect(screen.getByRole("status")).toHaveTextContent('1 stop matches "rat".');

    type("");
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});
