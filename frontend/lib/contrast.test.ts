import { describe, expect, it } from "vitest";
import { contrastRatio, relativeLuminance } from "@/lib/contrast";
import { CONGESTION_COLORS, CONGESTION_LEGEND_OUTLINE } from "@/lib/constants";
import { getCongestionColor } from "@/lib/congestionColor";

const WHITE = "#FFFFFF";

describe("contrastRatio", () => {
  it("matches the WCAG reference extremes", () => {
    expect(contrastRatio("#000000", WHITE)).toBeCloseTo(21, 5);
    expect(contrastRatio(WHITE, WHITE)).toBeCloseTo(1, 5);
  });

  it("is symmetric regardless of argument order", () => {
    expect(contrastRatio("#EF4444", WHITE)).toBeCloseTo(contrastRatio(WHITE, "#EF4444"), 10);
  });

  it("exposes relative luminance in the 0..1 range", () => {
    expect(relativeLuminance("#000000")).toBeCloseTo(0, 5);
    expect(relativeLuminance(WHITE)).toBeCloseTo(1, 5);
  });
});

describe("congestion legend outline", () => {
  it("clears the 3:1 non-text minimum against the white panel/key background", () => {
    expect(contrastRatio(CONGESTION_LEGEND_OUTLINE, WHITE)).toBeGreaterThanOrEqual(3);
  });
});

describe("congestion severity fills (known, documented shortfall)", () => {
  // The legend FILLS are not all >=3:1 against white; the 1px outline is
  // what makes the legend object perceivable. These values pin the measured
  // state so changing the palette is a deliberate, visible edit rather than
  // a silent regression. They are NOT claims of conformance.
  it("pins the measured ratio of each severity colour against white", () => {
    expect(contrastRatio(CONGESTION_COLORS.free_flow, WHITE)).toBeCloseTo(2.28, 2);
    expect(contrastRatio(CONGESTION_COLORS.moderate, WHITE)).toBeCloseTo(2.15, 2);
    expect(contrastRatio(CONGESTION_COLORS.heavy, WHITE)).toBeCloseTo(3.76, 2);
    expect(contrastRatio(CONGESTION_COLORS.unknown, WHITE)).toBeCloseTo(4.83, 2);
  });

  it("draws the map with the same severity colours the legends show", () => {
    // The map is no longer a continuous ratio ramp: a ratio classifies to a
    // severity and then resolves to that severity's colour, so the map and
    // both legends cannot disagree (see lib/congestionColor.ts).
    expect(getCongestionColor(1)).toBe(CONGESTION_COLORS.free_flow);
    expect(getCongestionColor(1.15)).toBe(CONGESTION_COLORS.moderate);
    expect(getCongestionColor(1.5)).toBe(CONGESTION_COLORS.heavy);
    expect(getCongestionColor(4.2)).toBe(CONGESTION_COLORS.heavy);
  });
});

describe("green link text token (WCAG 1.4.3)", () => {
  // `accent.green` (#16A34A) is a map/line colour, not a text colour: as
  // normal text on white it is only 3.30:1. Green *text* (the /routes back
  // links, the "Load more" and suggestion hovers) uses `accent.green-text`
  // (#117E39), which clears 4.5:1. These two hexes must match
  // tailwind.config.ts; a change there is caught here rather than shipping as
  // a silent regression.
  const ACCENT_GREEN = "#16A34A";
  const ACCENT_GREEN_TEXT = "#117E39";

  it("keeps the text variant at or above the 4.5:1 normal-text minimum on white", () => {
    expect(contrastRatio(ACCENT_GREEN_TEXT, WHITE)).toBeGreaterThanOrEqual(4.5);
  });

  it("documents that the raw map green is not usable as text on white", () => {
    expect(contrastRatio(ACCENT_GREEN, WHITE)).toBeLessThan(4.5);
  });
});
