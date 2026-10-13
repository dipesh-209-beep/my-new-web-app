import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RouteEstimates, { formatDurationMinutes, formatFareRange } from "./RouteEstimates";
import { FareOut } from "@/types/route";

const NOTE =
  "desk_estimate_2026-08: prior value matched the Jan-2025 rate; scaled. PENDING confirmation against the official Bagmati Province gazette notice.";

function makeFare(overrides: Partial<FareOut> = {}): FareOut {
  return {
    fare_id: "F03",
    min_distance_km: 10,
    max_distance_km: 15,
    fare_npr_min: 30,
    fare_npr_max: 35,
    student_discount_pct: 45,
    verification_note: NOTE,
    ...overrides,
  };
}

/** A shape `request<T>` could hand back unvalidated from a malformed or
 * unexpected 200 response -- typed as FareOut only to exercise the UI guard. */
function malformedFare(overrides: Record<string, unknown>): FareOut {
  return { ...makeFare(), ...overrides } as unknown as FareOut;
}

describe("formatDurationMinutes", () => {
  it("formats sub-hour durations as minutes", () => {
    expect(formatDurationMinutes(45)).toBe("45 min");
  });

  it("formats whole hours without a minute remainder", () => {
    expect(formatDurationMinutes(120)).toBe("2 hr");
  });

  it("formats hours and minutes together", () => {
    expect(formatDurationMinutes(66)).toBe("1h 6m");
  });
});

describe("formatFareRange", () => {
  it("renders a range when min and max differ", () => {
    expect(formatFareRange(makeFare())).toBe("Rs. 30–35");
  });

  it("renders a single value when min equals max", () => {
    expect(formatFareRange(makeFare({ fare_npr_min: 25, fare_npr_max: 25 }))).toBe("Rs. 25");
  });

  it("preserves a legitimate zero fare", () => {
    expect(formatFareRange(makeFare({ fare_npr_min: 0, fare_npr_max: 0 }))).toBe("Rs. 0");
  });

  it("keeps up to two decimals and drops trailing zeros", () => {
    expect(formatFareRange(makeFare({ fare_npr_min: 24.5, fare_npr_max: 30 }))).toBe(
      "Rs. 24.5–30"
    );
    expect(formatFareRange(makeFare({ fare_npr_min: 24.05, fare_npr_max: 24.05 }))).toBe("Rs. 24.05");
  });

  it("returns null for missing amounts", () => {
    expect(formatFareRange(malformedFare({ fare_npr_min: undefined }))).toBeNull();
    expect(formatFareRange(malformedFare({ fare_npr_max: undefined }))).toBeNull();
  });

  it("returns null for non-numeric or non-finite amounts", () => {
    expect(formatFareRange(malformedFare({ fare_npr_min: "30" }))).toBeNull();
    expect(formatFareRange(makeFare({ fare_npr_min: NaN }))).toBeNull();
    expect(formatFareRange(makeFare({ fare_npr_max: Infinity }))).toBeNull();
  });

  it("returns null for a negative amount (invalid per the >= 0 contract)", () => {
    expect(formatFareRange(makeFare({ fare_npr_min: -1 }))).toBeNull();
  });
});

describe("RouteEstimates", () => {
  it("shows the duration and fare, keeping the verification note neutral and inspectable", () => {
    render(<RouteEstimates durationMinutes={66} fare={makeFare()} fareLoading={false} />);

    expect(screen.getByText("1h 6m")).toBeInTheDocument();
    expect(screen.getByText("Estimated duration")).toBeInTheDocument();
    expect(screen.getByText("Rs. 30–35")).toBeInTheDocument();
    expect(
      screen.getByText("Estimated fare to ride the full route — not a final ticket price.")
    ).toBeInTheDocument();
    // Neutral disclosure + the raw note, not paraphrased into a status claim.
    expect(screen.getByText("Fare verification information")).toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    // The UI itself must never assert the fare is official or verified.
    expect(screen.queryByText(/verified/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/official fare/i)).not.toBeInTheDocument();
  });

  it("keeps an unexpected non-empty verification note neutral", () => {
    render(
      <RouteEstimates
        durationMinutes={66}
        fare={makeFare({ verification_note: "roadside listing reviewed 2027-01; indicative only." })}
        fareLoading={false}
      />
    );

    expect(screen.getByText("Fare verification information")).toBeInTheDocument();
    expect(
      screen.getByText("roadside listing reviewed 2027-01; indicative only.")
    ).toBeInTheDocument();
    // The UI must not inject "pending confirmation" for a note that never said so.
    expect(screen.queryByText(/pending confirmation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/official fare/i)).not.toBeInTheDocument();
  });

  it("omits the disclosure when the fare carries no verification note", () => {
    render(
      <RouteEstimates
        durationMinutes={66}
        fare={makeFare({ verification_note: null })}
        fareLoading={false}
      />
    );

    expect(screen.getByText("Rs. 30–35")).toBeInTheDocument();
    expect(screen.queryByText("Fare verification information")).not.toBeInTheDocument();
    expect(
      screen.getByText("Estimated fare to ride the full route — not a final ticket price.")
    ).toBeInTheDocument();
  });

  it("treats a whitespace-only verification note as absent", () => {
    render(
      <RouteEstimates
        durationMinutes={66}
        fare={makeFare({ verification_note: "   " })}
        fareLoading={false}
      />
    );

    expect(screen.queryByText("Fare verification information")).not.toBeInTheDocument();
  });

  it("shows an honest unavailable state when no fare is available", () => {
    render(<RouteEstimates durationMinutes={66} fare={null} fareLoading={false} />);

    expect(screen.getByText("Fare estimate unavailable for this route.")).toBeInTheDocument();
    expect(screen.queryByText(/^Rs\./)).not.toBeInTheDocument();
  });

  it("shows a checking state while the fare lookup is in flight", () => {
    render(<RouteEstimates durationMinutes={66} fare={null} fareLoading />);

    expect(screen.getByText(/checking fare/i)).toBeInTheDocument();
    expect(screen.queryByText("Fare estimate unavailable for this route.")).not.toBeInTheDocument();
  });

  it("shows unavailable, not 'Rs. undefined', when fare amounts are missing", () => {
    render(
      <RouteEstimates
        durationMinutes={66}
        fare={malformedFare({ fare_npr_min: undefined, fare_npr_max: undefined })}
        fareLoading={false}
      />
    );

    expect(screen.getByText("Fare estimate unavailable for this route.")).toBeInTheDocument();
    expect(screen.queryByText(/undefined/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Rs\./)).not.toBeInTheDocument();
  });

  it("shows unavailable, not 'Rs. NaN', for non-finite amounts", () => {
    render(
      <RouteEstimates
        durationMinutes={66}
        fare={makeFare({ fare_npr_min: NaN, fare_npr_max: Infinity })}
        fareLoading={false}
      />
    );

    expect(screen.getByText("Fare estimate unavailable for this route.")).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("displays a legitimate zero fare rather than treating it as missing", () => {
    render(
      <RouteEstimates
        durationMinutes={66}
        fare={makeFare({ fare_npr_min: 0, fare_npr_max: 0 })}
        fareLoading={false}
      />
    );

    expect(screen.getByText("Rs. 0")).toBeInTheDocument();
    expect(screen.queryByText("Fare estimate unavailable for this route.")).not.toBeInTheDocument();
  });

  it("shows duration unavailable when the route has no duration estimate", () => {
    render(<RouteEstimates durationMinutes={null} fare={makeFare()} fareLoading={false} />);

    expect(screen.getByText("Unavailable for this route.")).toBeInTheDocument();
  });

  it("shows duration unavailable, not 'NaN', for a non-finite duration", () => {
    render(<RouteEstimates durationMinutes={NaN} fare={makeFare()} fareLoading={false} />);

    expect(screen.getByText("Unavailable for this route.")).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });
});
