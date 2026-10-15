import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import CongestionPanel from "@/components/CongestionPanel";
import { CongestionProvenance } from "@/types/route";

const PROVENANCE: CongestionProvenance = {
  is_live: false,
  is_measured: false,
  note: "Estimated, not measured. Derived from OSRM free-flow durations.",
};

function renderPanel(props: Partial<ComponentProps<typeof CongestionPanel>> = {}) {
  const onToggle = vi.fn();
  const utils = render(
    <CongestionPanel
      enabled
      onToggle={onToggle}
      dayOfWeek={null}
      hourBucket={null}
      onDayChange={vi.fn()}
      onHourChange={vi.fn()}
      segmentCount={0}
      hasSeededOnly={false}
      {...props}
    />
  );
  return { ...utils, onToggle };
}

describe("CongestionPanel", () => {
  it("describes the layer as estimated, not as live or historical traffic", () => {
    renderPanel();
    expect(screen.getByText("Estimated congestion overlay")).toBeInTheDocument();
    // The old copy implied measured history; it must be gone.
    expect(screen.queryByText(/historical traffic pattern/i)).not.toBeInTheDocument();
  });

  it("renders the API provenance note when provided", () => {
    renderPanel({ provenance: PROVENANCE });
    expect(screen.getByText(PROVENANCE.note)).toBeInTheDocument();
  });

  it("falls back to its own honest note when the API omits provenance", () => {
    renderPanel({ provenance: null });
    expect(screen.getByText(/estimated, not measured/i)).toBeInTheDocument();
  });

  it("discloses that overlay lines are straight and that routing also uses undrawn zones", () => {
    renderPanel();
    expect(screen.getByText(/straight between each segment/i)).toBeInTheDocument();
    expect(screen.getByText(/area zones that aren/i)).toBeInTheDocument();
    // The overlay must still say it doesn't re-rank the routes below.
    expect(screen.getByText(/doesn.t re-rank the routes below/i)).toBeInTheDocument();
  });

  it("distinguishes a failed load from a genuinely empty result", () => {
    renderPanel({ segmentCount: 0, error: true });
    expect(screen.getByText(/Couldn.t load congestion estimates/i)).toBeInTheDocument();
    // "No estimate ... yet" would imply live collection is ongoing.
    expect(screen.queryByText(/yet\./i)).not.toBeInTheDocument();
  });

  it("reports an empty result without implying live data is still coming", () => {
    renderPanel({ segmentCount: 0, error: false });
    expect(screen.getByText("No congestion estimate for this time.")).toBeInTheDocument();
  });

  it("summarises a populated overlay and flags a seeded-only baseline", () => {
    renderPanel({ segmentCount: 3, hasSeededOnly: true });
    const summary = screen.getByText(/3 segment estimates/);
    expect(summary.textContent).toMatch(/free-flow baseline/i);
  });

  it("notes a stale overlay when a refresh failed but earlier data is shown", () => {
    renderPanel({ segmentCount: 2, error: true });
    expect(screen.getByText(/Couldn.t refresh; showing an earlier estimate/i)).toBeInTheDocument();
  });

  it("hides the body while the layer is off", () => {
    renderPanel({ enabled: false, provenance: PROVENANCE });
    // The toggle header stays visible; the disclosure body must not.
    expect(screen.getByText("Estimated congestion overlay")).toBeInTheDocument();
    expect(screen.queryByText(PROVENANCE.note)).not.toBeInTheDocument();
  });
});
