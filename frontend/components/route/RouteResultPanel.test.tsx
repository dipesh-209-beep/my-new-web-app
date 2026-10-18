import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import RouteResultPanel from "@/components/route/RouteResultPanel";
import {
  JourneyFareOut,
  RouteAlternative,
  RouteFinderResult,
  RouteLeg,
  RouteSearchResult,
  Stop,
} from "@/types/route";

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

function makeLeg(overrides: Partial<RouteLeg> = {}): RouteLeg {
  return {
    route_id: "R1",
    route_name: "R1",
    board_stop: makeStop({ stop_id: "S1", stop_name: "A" }),
    alight_stop: makeStop({ stop_id: "S2", stop_name: "B" }),
    num_ride_segments: 2,
    stops: [
      makeStop({ stop_id: "S1", stop_name: "A" }),
      makeStop({ stop_id: "S2", stop_name: "B" }),
    ],
    estimated_duration_s: 600,
    ...overrides,
  };
}

function makeJourneyFare(overrides: Partial<JourneyFareOut> = {}): JourneyFareOut {
  return {
    fare_npr_min: 20,
    fare_npr_max: 25,
    complete: true,
    ride_leg_count: 1,
    priced_leg_count: 1,
    verification_note: null,
    ...overrides,
  };
}

/** Malformed journey total for the panel's defensive-formatting guards. */
function malformedJourneyFare(overrides: Record<string, unknown>): JourneyFareOut {
  return { ...makeJourneyFare(), ...overrides } as unknown as JourneyFareOut;
}

function makeResult(overrides: Partial<RouteFinderResult> = {}): RouteFinderResult {
  return {
    origin_stop_id: "S1",
    destination_stop_id: "S2",
    total_cost: 1000,
    transfer_count: 0,
    legs: [makeLeg()],
    fare: null,
    alternatives: [],
    total_estimated_duration_s: 600,
    ...overrides,
  };
}

function renderPanel(
  result: RouteFinderResult,
  selectedIndex = -1
) {
  return render(
    <RouteResultPanel
      result={{ found: true, ...result }}
      loading={false}
      error={null}
      selectedIndex={selectedIndex}
      onSelectedIndexChange={() => {}}
    />
  );
}

describe("RouteResultPanel time and fare honesty", () => {
  it("shows the backend estimate, not the OSRM driving-profile duration, always labeled (est.)", () => {
    // estimated_duration_s = 600s (10 min) at assumed bus speed; the OSRM
    // driving profile says 300s (5 min) -- a private car at free-flow
    // speed. The panel must show 10 and never the 5.
    const leg = makeLeg({
      estimated_duration_s: 600,
      road_geometry: {
        geometry: { type: "LineString", coordinates: [[85.31, 27.7], [85.32, 27.71]] },
        distance_m: 5000,
        duration_s: 300,
      },
    });
    renderPanel(makeResult({ legs: [leg], total_estimated_duration_s: 600 }));
    expect(screen.getByText("10")).toBeTruthy();
    expect(screen.getByText("(est.)")).toBeTruthy();
    expect(screen.queryByText("5")).toBeNull();
  });

  it("renders a sub-minute estimate as '<1 min', never a bare '0 min'", () => {
    // 20s rounds to 0 whole minutes; "0 min" would read as no time at all.
    renderPanel(
      makeResult({ legs: [makeLeg({ estimated_duration_s: 20 })], total_estimated_duration_s: 20 })
    );
    expect(screen.getByText("<1")).toBeTruthy();
    expect(screen.getByText("min")).toBeTruthy();
    expect(screen.queryByText("0")).toBeNull();
  });

  it("still shows '0 min' when the backend genuinely estimates zero", () => {
    renderPanel(
      makeResult({ legs: [makeLeg({ estimated_duration_s: 0 })], total_estimated_duration_s: 0 })
    );
    expect(screen.getByText("0")).toBeTruthy();
  });

  it("exposes the result card label as a heading for the page's outline", () => {
    renderPanel(makeResult());
    expect(screen.getByRole("heading", { name: "Direct" })).toBeTruthy();
  });

  it("shows the selected alternative's own fare, not the primary result's", () => {
    const alt: RouteAlternative = {
      label: "shortest_distance",
      total_cost: 1200,
      transfer_count: 1,
      legs: [makeLeg()],
      fare: makeJourneyFare({ fare_npr_min: 40, fare_npr_max: 45 }),
      total_estimated_duration_s: 900,
    };
    renderPanel(
      makeResult({
        fare: makeJourneyFare({ fare_npr_min: 20, fare_npr_max: 25 }),
        alternatives: [alt],
      }),
      0
    );
    expect(screen.getByText(/Rs\. 40–45/)).toBeTruthy();
    expect(screen.queryByText(/Rs\. 20–25/)).toBeNull();
  });

  it("keeps the verification note neutral and inspectable instead of claiming pending confirmation", () => {
    renderPanel(
      makeResult({
        fare: makeJourneyFare({
          verification_note: "PENDING confirmation against the official gazette notice.",
        }),
      })
    );
    expect(screen.getByText("Fare verification information")).toBeTruthy();
    expect(screen.getByText("PENDING confirmation against the official gazette notice.")).toBeTruthy();
    expect(screen.getByText("Estimated fare, not a final ticket price.")).toBeTruthy();
    expect(screen.queryByText("— pending confirmation")).toBeNull();
    expect(screen.queryByText(/official fare/i)).toBeNull();
  });

  it("keeps an unexpected non-empty verification note neutral", () => {
    renderPanel(
      makeResult({
        fare: makeJourneyFare({
          verification_note: "roadside listing reviewed 2027-01; indicative only.",
        }),
      })
    );
    expect(screen.getByText("Fare verification information")).toBeTruthy();
    expect(
      screen.getByText("roadside listing reviewed 2027-01; indicative only.")
    ).toBeTruthy();
    expect(screen.queryByText(/— pending confirmation/)).toBeNull();
  });

  it("omits the verification disclosure when the fare carries no note", () => {
    renderPanel(makeResult({ fare: makeJourneyFare({ verification_note: null }) }));
    expect(screen.getByText("Rs. 20–25")).toBeTruthy();
    expect(screen.getByText("Estimated fare, not a final ticket price.")).toBeTruthy();
    expect(screen.queryByText("Fare verification information")).toBeNull();
  });

  it("shows Fare unavailable instead of Rs. undefined when amounts are missing", () => {
    renderPanel(
      makeResult({
        fare: malformedJourneyFare({ fare_npr_min: undefined, fare_npr_max: undefined }),
      })
    );
    expect(screen.getByText("Fare unavailable")).toBeTruthy();
    expect(screen.queryByText(/undefined/)).toBeNull();
    // No "Estimated fare" note paragraph when there's no valid amount to show.
    expect(screen.queryByText("Estimated fare, not a final ticket price.")).toBeNull();
  });

  it("shows Fare unavailable instead of Rs. NaN for non-finite amounts", () => {
    renderPanel(
      makeResult({
        fare: makeJourneyFare({ fare_npr_min: NaN, fare_npr_max: Infinity }),
      })
    );
    expect(screen.getByText("Fare unavailable")).toBeTruthy();
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("shows Fare unavailable for a negative amount (invalid per the >= 0 contract)", () => {
    renderPanel(makeResult({ fare: makeJourneyFare({ fare_npr_min: -1 }) }));
    expect(screen.getByText("Fare unavailable")).toBeTruthy();
  });

  it("preserves a legitimate zero fare", () => {
    renderPanel(makeResult({ fare: makeJourneyFare({ fare_npr_min: 0, fare_npr_max: 0 }) }));
    expect(screen.getByText("Rs. 0")).toBeTruthy();
    expect(screen.getByText("Estimated fare, not a final ticket price.")).toBeTruthy();
  });

  it("says fare is unavailable when no fare rule matched", () => {
    renderPanel(makeResult({ fare: null }));
    expect(screen.getByText("Fare unavailable")).toBeTruthy();
  });

  it("flags an incomplete total when some ride legs had no fare band", () => {
    renderPanel(
      makeResult({
        fare: makeJourneyFare({
          fare_npr_min: 20,
          fare_npr_max: 25,
          complete: false,
          ride_leg_count: 2,
          priced_leg_count: 1,
        }),
      })
    );
    expect(screen.getByText("Rs. 20–25")).toBeTruthy();
    expect(screen.getByText(/1 of 2 bus legs have no fare estimate/i)).toBeTruthy();
    expect(
      screen.getByText(/Estimated fare covering only the buses we could price/i)
    ).toBeTruthy();
    // The partial sum must never be presented as the complete fare.
    expect(screen.queryByText("Estimated fare, not a final ticket price.")).toBeNull();
  });
});

describe("RouteResultPanel recommendation explanation", () => {
  function makeTransferLeg(): RouteLeg {
    return makeLeg({
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      estimated_duration_s: 60,
    });
  }

  const directAlt: RouteAlternative = {
    label: "alternate_direct_route",
    total_cost: 1200,
    transfer_count: 0,
    legs: [makeLeg()],
    fare: null,
    total_estimated_duration_s: 600,
  };

  it("explains a transfer primary using the backend's rank_reason", () => {
    renderPanel(
      makeResult({
        transfer_count: 1,
        legs: [makeLeg(), makeTransferLeg()],
        alternatives: [directAlt],
        rank_reason: "avoids_congestion",
      })
    );
    expect(screen.getByText("Scores better once current congestion is factored in.")).toBeTruthy();
    expect(
      screen.getByText(
        "Ranked by straight-line stop-to-stop distance and number of transfers; frequency and fare are shown for information only and are not used to rank."
      )
    ).toBeTruthy();
  });

  it("explains a direct primary with alternatives", () => {
    renderPanel(
      makeResult({
        transfer_count: 0,
        alternatives: [directAlt],
        rank_reason: "shortest_distance",
      })
    );
    expect(screen.getByText("Shortest stop-to-stop distance among the options.")).toBeTruthy();
  });

  it("uses the visible-alternatives wording for fewest_transfers when alternatives are shown", () => {
    renderPanel(
      makeResult({
        transfer_count: 0,
        alternatives: [directAlt],
        rank_reason: "fewest_transfers",
      })
    );
    expect(screen.getByText("Fewer transfers than the other options.")).toBeTruthy();
    expect(
      screen.queryByText("Fewer transfers than the other routes the search compared.")
    ).toBeNull();
  });

  it("uses the visible-alternatives wording for best_overall when alternatives are shown", () => {
    renderPanel(
      makeResult({
        transfer_count: 0,
        alternatives: [directAlt],
        rank_reason: "best_overall",
      })
    );
    expect(
      screen.getByText(
        "Matches the other options on distance and transfers; shown first by a fixed tie-break."
      )
    ).toBeTruthy();
    expect(
      screen.queryByText(
        "Matches another route the search compared on distance and transfers; shown first by a fixed tie-break."
      )
    ).toBeNull();
  });

  it("shows an honest only-option explanation for a lone transfer", () => {
    renderPanel(
      makeResult({
        transfer_count: 1,
        legs: [makeLeg(), makeTransferLeg()],
        alternatives: [],
        rank_reason: "only_option",
      })
    );
    expect(screen.getByText("Only one route was found between these stops.")).toBeTruthy();
  });

  it("does not add an explanation to a lone direct route", () => {
    renderPanel(makeResult({ transfer_count: 0, alternatives: [], rank_reason: "only_option" }));
    expect(screen.getAllByText("Direct").length).toBeGreaterThan(0);
    expect(screen.queryByText("Only one route was found between these stops.")).toBeNull();
  });

  it("shows no explanation when the backend sent no rank_reason", () => {
    renderPanel(
      makeResult({
        transfer_count: 1,
        legs: [makeLeg(), makeTransferLeg()],
        alternatives: [directAlt],
      })
    );
    expect(screen.queryByText(/Ranked by straight-line stop-to-stop distance/)).toBeNull();
  });

  it("ignores an unknown rank_reason rather than inventing a claim", () => {
    renderPanel(
      makeResult({
        transfer_count: 1,
        legs: [makeLeg(), makeTransferLeg()],
        alternatives: [directAlt],
        // A code the UI doesn't know (e.g. from a newer backend).
        rank_reason: "made_up_code" as unknown as RouteFinderResult["rank_reason"],
      })
    );
    expect(screen.queryByText(/Ranked by straight-line stop-to-stop distance/)).toBeNull();
  });

  it("never claims fastest/cheapest/most frequent, which the data can't support", () => {
    renderPanel(
      makeResult({
        transfer_count: 1,
        legs: [makeLeg(), makeTransferLeg()],
        alternatives: [directAlt],
        rank_reason: "shortest_distance",
      })
    );
    expect(screen.queryByText(/fastest/i)).toBeNull();
    expect(screen.queryByText(/cheapest/i)).toBeNull();
    expect(screen.queryByText(/most frequent/i)).toBeNull();
  });

  // When the response shows no alternatives, "the other options" would
  // reference options the rider cannot see. The comparison still happened, so
  // the reason is shown with wording that says so without implying visibility.
  describe("wording when no alternatives are displayed", () => {
    const transferPrimary = {
      transfer_count: 1,
      legs: [makeLeg(), makeTransferLeg()],
      alternatives: [] as RouteAlternative[],
    };

    it("words fewest_transfers without implying the compared options are visible", () => {
      renderPanel(makeResult({ ...transferPrimary, rank_reason: "fewest_transfers" }));
      expect(
        screen.getByText("Fewer transfers than the other routes the search compared.")
      ).toBeTruthy();
      expect(screen.queryByText("Fewer transfers than the other options.")).toBeNull();
    });

    it("words shortest_distance without implying the compared options are visible", () => {
      renderPanel(makeResult({ ...transferPrimary, rank_reason: "shortest_distance" }));
      expect(
        screen.getByText("Shortest stop-to-stop distance of the routes the search compared.")
      ).toBeTruthy();
      expect(screen.queryByText("Shortest stop-to-stop distance among the options.")).toBeNull();
    });

    it("words best_overall without implying the compared options are visible", () => {
      renderPanel(makeResult({ ...transferPrimary, rank_reason: "best_overall" }));
      expect(
        screen.getByText(
          "Matches another route the search compared on distance and transfers; shown first by a fixed tie-break."
        )
      ).toBeTruthy();
      expect(
        screen.queryByText(
          "Matches the other options on distance and transfers; shown first by a fixed tie-break."
        )
      ).toBeNull();
    });

    it("keeps avoids_congestion wording truthful with or without visible alternatives", () => {
      renderPanel(makeResult({ ...transferPrimary, rank_reason: "avoids_congestion" }));
      expect(
        screen.getByText("Scores better once current congestion is factored in.")
      ).toBeTruthy();
    });

    it("keeps via_stops wording accurate", () => {
      renderPanel(
        makeResult({
          transfer_count: 1,
          legs: [makeLeg(), makeTransferLeg()],
          alternatives: [directAlt],
          rank_reason: "via_stops",
        })
      );
      expect(
        screen.getByText("Includes the stops you asked to travel through.")
      ).toBeTruthy();
    });

    it("renders no variant for an unknown reason code", () => {
      renderPanel(
        makeResult({
          ...transferPrimary,
          // A code the UI doesn't know (e.g. from a newer backend).
          rank_reason: "made_up_code" as unknown as RouteFinderResult["rank_reason"],
        })
      );
      expect(screen.queryByText(/the search compared/)).toBeNull();
      expect(screen.queryByText(/the other options/)).toBeNull();
    });
  });
});

describe("RouteResultPanel announces async outcomes", () => {
  // Each async outcome (loading, found, not-found, error) replaces the
  // previous one without a page navigation, so it has to land in a live
  // region or a screen-reader user never hears it. The not-found case used
  // to be a bare <div> -- silent.
  it("announces a no-route result politely instead of rendering it silently", () => {
    render(
      <RouteResultPanel
        result={{ found: false } as RouteSearchResult}
        loading={false}
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />
    );
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("No bus route found between these stops.");
  });

  it("announces the loading state politely", () => {
    render(
      <RouteResultPanel
        result={null}
        loading
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />
    );
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("Finding the best routes…");
    // The same words are also shown in the visible card (once in the sr-only
    // status, once as the visual caption).
    expect(screen.getAllByText("Finding the best routes…")).toHaveLength(2);
  });

  // The bug this guards: the found summary used to be announced from a live
  // region that was created in the same commit as its text, which assistive
  // tech does not reliably speak. Both the loading and found cards now put the
  // status region in the same position, so React reuses the node and the
  // summary is a text change in a region that already existed.
  it("carries the found announcement in the live region loading already mounted", () => {
    const result = { found: true, ...makeResult() } as RouteSearchResult;
    const { rerender } = render(
      <RouteResultPanel
        result={null}
        loading
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />
    );
    const loadingRegion = screen.getByRole("status");
    expect(loadingRegion).toHaveTextContent("Finding the best routes…");

    rerender(
      <RouteResultPanel
        result={result}
        loading={false}
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />
    );

    const foundRegion = screen.getByRole("status");
    expect(foundRegion).toBe(loadingRegion);
    expect(foundRegion).toHaveTextContent("Direct. Estimated 10 min, direct.");
  });

  // The found panel used to wrap the ENTIRE card (alternatives, timeline,
  // fare notes) in aria-live="polite", so every re-render -- including just
  // picking a different alternative -- replayed the whole card. It now
  // carries a single concise screen-reader-only status; the visible content
  // is read on demand.
  it("announces the found state through one concise status, not the whole panel", () => {
    const { container } = renderPanel(makeResult());
    const liveRegions = container.querySelectorAll("[aria-live]");
    expect(liveRegions).toHaveLength(1);
    const status = screen.getByRole("status");
    expect(liveRegions[0]).toBe(status);
    expect(status).toHaveTextContent("Direct. Estimated 10 min, direct.");
  });

  it("re-announces the selected alternative through the concise status", () => {
    const alternative: RouteAlternative = {
      label: "fastest_estimated",
      total_cost: 900,
      transfer_count: 0,
      legs: [makeLeg()],
      fare: null,
      total_estimated_duration_s: 900,
    };
    const result = makeResult({ alternatives: [alternative] });
    const { rerender } = render(
      <RouteResultPanel
        result={{ found: true, ...result }}
        loading={false}
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />
    );
    expect(screen.getByRole("status")).toHaveTextContent("Direct. Estimated 10 min, direct.");

    rerender(
      <RouteResultPanel
        result={{ found: true, ...result }}
        loading={false}
        error={null}
        selectedIndex={0}
        onSelectedIndexChange={() => {}}
      />
    );
    expect(screen.getByRole("status")).toHaveTextContent("Fastest (est.). Estimated 15 min, direct.");
  });

  it("keeps an error assertively announced and silent to the polite status", () => {
    render(
      <RouteResultPanel
        result={null}
        loading={false}
        error="Could not reach the routing service."
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the routing service.");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("keeps the error Retry wired without adding a polite announcement", () => {
    const onRetry = vi.fn();
    render(
      <RouteResultPanel
        result={null}
        loading={false}
        error="Could not reach the routing service."
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
        onRetry={onRetry}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("moves focus to the stable panel region when Retry is pressed, and it survives error -> loading", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <RouteResultPanel
        result={null}
        loading={false}
        error="Could not reach the routing service."
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
        onRetry={onRetry}
      />
    );

    const retryButton = screen.getByRole("button", { name: "Retry" });
    retryButton.focus();
    expect(retryButton).toHaveFocus();

    // Pressing Retry swaps the error card (and the button with it) for the
    // loading card; focus must not be dropped to <body>.
    fireEvent.click(retryButton);
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("region", { name: "Route results" })).toHaveFocus();

    rerender(
      <RouteResultPanel
        result={null}
        loading
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
        onRetry={onRetry}
      />
    );
    // Same node across the state change, so focus is preserved.
    expect(screen.getByRole("region", { name: "Route results" })).toHaveFocus();
  });
});

describe("RouteResultPanel journey instructions", () => {
  it("shows board, transfer and get-off steps for a two-bus journey without breaking selection", () => {
    const firstRide = makeLeg({
      route_id: "R1",
      route_name: "Kalimati-Koteshwor",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Kalimati" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      num_ride_segments: 4,
    });
    const walk = makeLeg({
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      board_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      alight_stop: makeStop({ stop_id: "S3", stop_name: "Ratna Park" }),
      num_ride_segments: 1,
      estimated_duration_s: 180,
    });
    const secondRide = makeLeg({
      route_id: "R2",
      route_name: "Ratna Park-Budhanilkantha",
      board_stop: makeStop({ stop_id: "S3", stop_name: "Ratna Park" }),
      alight_stop: makeStop({ stop_id: "S4", stop_name: "Budhanilkantha" }),
      num_ride_segments: 8,
    });

    renderPanel(makeResult({ transfer_count: 1, legs: [firstRide, walk, secondRide] }));

    expect(screen.getByText("Board at Kalimati")).toBeTruthy();
    expect(screen.getByText("Get off at Tripureshwor")).toBeTruthy();
    expect(screen.getByText("Transfer")).toBeTruthy();
    expect(screen.getByText("Walk from Tripureshwor to Ratna Park")).toBeTruthy();
    expect(screen.getByText("Board at Ratna Park")).toBeTruthy();
    expect(screen.getByText("Get off at Budhanilkantha")).toBeTruthy();
    // Ranking/announcement behaviour is untouched by the presentation change.
    expect(screen.getByRole("status")).toHaveTextContent("Recommended. Estimated");
  });
});

describe("RouteResultPanel itinerary/map selection", () => {
  it("reflects the selected leg and forwards a timeline click to onLegSelect", () => {
    const firstRide = makeLeg({
      route_id: "R1",
      board_stop: makeStop({ stop_id: "S1", stop_name: "Kalimati" }),
      alight_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
    });
    const walk = makeLeg({
      route_id: "TRANSFER",
      route_name: "Transfer (walk)",
      board_stop: makeStop({ stop_id: "S2", stop_name: "Tripureshwor" }),
      alight_stop: makeStop({ stop_id: "S3", stop_name: "Ratna Park" }),
      estimated_duration_s: 180,
    });
    const onLegSelect = vi.fn();

    render(
      <RouteResultPanel
        result={{ found: true, ...makeResult({ transfer_count: 1, legs: [firstRide, walk] }) }}
        loading={false}
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={() => {}}
        selectedLegIndex={1}
        onLegSelect={onLegSelect}
      />
    );

    const buttons = screen.getAllByRole("button", { name: /Board at|Walk from|Transfer/ });
    expect(buttons).toHaveLength(2);
    expect(buttons[1]).toHaveAttribute("aria-pressed", "true");
    expect(buttons[0]).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(buttons[0]);
    expect(onLegSelect).toHaveBeenCalledWith(0);
  });
});

describe("RouteResultPanel alternative pills", () => {
  const alternative: RouteAlternative = {
    label: "shortest_distance",
    total_cost: 1200,
    transfer_count: 1,
    legs: [makeLeg()],
    fare: null,
    total_estimated_duration_s: 900,
  };

  // The option pills moved into the shared SelectablePill primitive; this
  // locks the consumer wiring (group name, accessible names, pressed state,
  // selection callbacks) so the refactor cannot silently change behaviour.
  it("keeps the option group, pressed state and selection callbacks", () => {
    const onSelectedIndexChange = vi.fn();
    render(
      <RouteResultPanel
        result={{ found: true, ...makeResult({ alternatives: [alternative] }) }}
        loading={false}
        error={null}
        selectedIndex={-1}
        onSelectedIndexChange={onSelectedIndexChange}
      />
    );

    expect(screen.getByRole("group", { name: "Route alternatives" })).toBeInTheDocument();

    const primary = screen.getByRole("button", { name: "Direct route" });
    expect(primary).toHaveAttribute("aria-pressed", "true");
    expect(primary).toHaveTextContent("Direct");

    const alt = screen.getByRole("button", { name: /Least distance:/ });
    expect(alt).toHaveAttribute("aria-pressed", "false");
    expect(alt).toHaveTextContent("Least distance");

    fireEvent.click(alt);
    expect(onSelectedIndexChange).toHaveBeenCalledWith(0);

    fireEvent.click(primary);
    expect(onSelectedIndexChange).toHaveBeenCalledWith(-1);
  });

  it("marks the selected alternative as pressed", () => {
    render(
      <RouteResultPanel
        result={{ found: true, ...makeResult({ alternatives: [alternative] }) }}
        loading={false}
        error={null}
        selectedIndex={0}
        onSelectedIndexChange={() => {}}
      />
    );

    expect(screen.getByRole("button", { name: /Least distance:/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByRole("button", { name: "Direct route" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });
});
