import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import CongestionPanel from "@/components/CongestionPanel";
import { CongestionProvenance } from "@/types/route";
import { CONGESTION_COLORS, CONGESTION_LEGEND, CONGESTION_LEGEND_OUTLINE } from "@/lib/constants";

/** jsdom normalizes an inline hex colour to `rgb(...)`; mirror that here. */
function rgbString(hex: string): string {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `rgb(${r}, ${g}, ${b})`;
}

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

  it("renders one outlined swatch per severity, matching the shared palette", () => {
    const { container } = renderPanel();
    const swatches = Array.from(
      container.querySelectorAll<HTMLElement>("[data-congestion-swatch]")
    );
    expect(swatches).toHaveLength(CONGESTION_LEGEND.length);
    CONGESTION_LEGEND.forEach(({ level }) => {
      const swatch = container.querySelector<HTMLElement>(
        `[data-congestion-swatch="${level}"]`
      );
      expect(swatch).not.toBeNull();
      // The swatch colour is the exact colour the map draws for that
      // severity, so the key can't mislabel a line.
      expect(swatch!.style.background).toBe(rgbString(CONGESTION_COLORS[level]));
      expect(swatch!.style.borderStyle).toBe("solid");
      expect(swatch!.style.borderWidth).toBe("1px");
      expect(swatch!.style.borderColor).toBe(rgbString(CONGESTION_LEGEND_OUTLINE));
    });
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

  // D4: the panel previously painted its status only as visible text, so a
  // screen-reader user who enabled Traffic heard the toggle press but nothing
  // about the result. The states are now exposed through one polite region.
  describe("status announcements", () => {
    it("exposes exactly one polite status region", () => {
      const { container } = renderPanel({ segmentCount: 3 });
      const liveRegions = container.querySelectorAll("[aria-live]");
      expect(liveRegions).toHaveLength(1);
      const status = screen.getByRole("status");
      expect(status).toBe(liveRegions[0]);
      expect(status).toHaveAttribute("aria-live", "polite");
    });

    it("announces a populated overlay and its seeded-baseline caveat", () => {
      renderPanel({ segmentCount: 3, hasSeededOnly: true });
      const status = screen.getByRole("status");
      expect(status).toHaveTextContent("3 congestion segments.");
      expect(status).toHaveTextContent("Free-flow baseline only.");
    });

    it("announces an empty result without a loading or live-collection claim", () => {
      renderPanel({ segmentCount: 0, error: false });
      const status = screen.getByRole("status");
      expect(status).toHaveTextContent("No congestion segments for this time.");
      expect(status).not.toHaveTextContent(/loading/i);
      expect(status).not.toHaveTextContent(/yet\./i);
    });

    it("announces an initial fetch failure distinctly from an empty result", () => {
      renderPanel({ segmentCount: 0, error: true });
      const status = screen.getByRole("status");
      expect(status).toHaveTextContent("Congestion estimates failed to load");
      expect(status).not.toHaveTextContent(/no congestion segments/i);
    });

    it("announces a refresh failure that is showing earlier data", () => {
      renderPanel({ segmentCount: 2, error: true });
      const status = screen.getByRole("status");
      expect(status).toHaveTextContent("2 congestion segments.");
      expect(status).toHaveTextContent("Refresh failed; showing earlier data.");
    });

    it("announces loading only while there is no data yet", () => {
      renderPanel({ segmentCount: 0, loading: true });
      expect(screen.getByRole("status")).toHaveTextContent("Loading congestion estimates…");
    });

    it("does not re-announce loading during a background refresh with existing data", () => {
      renderPanel({ segmentCount: 4, loading: true });
      const status = screen.getByRole("status");
      expect(status).not.toHaveTextContent(/loading/i);
      // The previous result text is retained, so a poll goes unannounced...
      expect(status).toHaveTextContent("4 congestion segments.");
      // ...even though the visible caption still shows the transient state.
      expect(screen.getByText("Loading…")).toBeInTheDocument();
    });

    it("updates the same persistent region from empty to populated", () => {
      const { rerender } = renderPanel({ segmentCount: 0 });
      const first = screen.getByRole("status");
      expect(first).toHaveTextContent("No congestion segments for this time.");

      rerender(
        <CongestionPanel
          enabled
          onToggle={vi.fn()}
          dayOfWeek={null}
          hourBucket={null}
          onDayChange={vi.fn()}
          onHourChange={vi.fn()}
          segmentCount={5}
          hasSeededOnly={false}
        />
      );

      const second = screen.getByRole("status");
      expect(second).toBe(first);
      expect(second).toHaveTextContent("5 congestion segments.");
    });
  });

  // D5: "Reset to now" is the control in this panel that unmounts when it is
  // activated, so a keyboard press on it used to take focus with it and drop
  // it to <body>. The keyboard case is driven through a stateful harness so
  // the button genuinely unmounts on activation, rather than the test merely
  // asserting that it is controlled by props that never change.
  describe("Reset to now focus", () => {
    function renderWithCustomTime() {
      function Harness() {
        const [dayOfWeek, setDayOfWeek] = useState<number | null>(2);
        const [hourBucket, setHourBucket] = useState<number | null>(9);
        return (
          <CongestionPanel
            enabled
            onToggle={() => {}}
            dayOfWeek={dayOfWeek}
            hourBucket={hourBucket}
            onDayChange={setDayOfWeek}
            onHourChange={setHourBucket}
            segmentCount={0}
            hasSeededOnly={false}
          />
        );
      }
      return render(<Harness />);
    }

    it("is absent when day and hour are both 'now'", () => {
      renderPanel();
      expect(screen.queryByRole("button", { name: "Reset to now" })).toBeNull();
    });

    it("appears when a custom day is set", () => {
      renderPanel({ dayOfWeek: 2 });
      expect(screen.getByRole("button", { name: "Reset to now" })).toBeInTheDocument();
    });

    it("appears when a custom hour is set", () => {
      renderPanel({ hourBucket: 9 });
      expect(screen.getByRole("button", { name: "Reset to now" })).toBeInTheDocument();
    });

    it("unmounts on keyboard activation and leaves focus on the time select", async () => {
      renderWithCustomTime();
      const user = userEvent.setup();

      const reset = screen.getByRole("button", { name: "Reset to now" });
      reset.focus();
      expect(reset).toHaveFocus();

      await user.keyboard("{Enter}");

      // The button is gone and both values are back to "now"...
      expect(screen.queryByRole("button", { name: "Reset to now" })).toBeNull();
      const hourSelect = document.getElementById("congestion-hour");
      expect(hourSelect).toBeInTheDocument();
      // ...and focus was handed to the surviving control, not dropped.
      expect(document.activeElement).toBe(hourSelect);
      expect(document.activeElement).not.toBe(document.body);
    });

    it("still exposes exactly one polite live region after a reset", async () => {
      const { container } = renderWithCustomTime();
      const user = userEvent.setup();
      const statusBefore = screen.getByRole("status");

      screen.getByRole("button", { name: "Reset to now" }).focus();
      await user.keyboard("{Enter}");

      expect(container.querySelectorAll("[aria-live]")).toHaveLength(1);
      const statusAfter = screen.getByRole("status");
      expect(statusAfter).toBe(statusBefore);
      expect(statusAfter).toHaveAttribute("aria-live", "polite");
      expect(statusAfter).toHaveTextContent("No congestion segments for this time.");
    });
  });
});
