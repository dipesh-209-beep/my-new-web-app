import { afterEach, describe, expect, it, vi } from "vitest";
import React, { type ReactNode } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import type { Stop } from "@/types/route";

/**
 * Regression guard for the D11 mobile layering fix.
 *
 * Why this test is structural and not a hit test: jsdom does no layout and
 * applies no stylesheets, so `document.elementFromPoint` (used by the Phase 7
 * browser harness) cannot be reproduced here. What *is* testable here is the
 * invariant that makes the fix work: the element wrapping the Leaflet map must
 * establish its own stacking context, so Leaflet's internal z-indexes
 * (`.leaflet-bottom` = 1000, `.leaflet-control` = 800) are contained and cannot
 * paint over the mobile bottom sheet (`z-[500]`). If a later change drops that
 * containment, the route legend will again intercept taps meant for the sheet's
 * Min/Half/Full controls and drag handle. The actual browser behaviour is
 * re-verified by the Phase 7 harness (see the Phase 7.1 report).
 */

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

// The real `next/dynamic` would pull in Leaflet, which needs `window`. Rendering
// its `loading` fallback is enough: the wrapper div under test is synchronous.
vi.mock("next/dynamic", () => ({
  default: (_loader: unknown, opts?: { loading?: () => ReactNode }) =>
    opts?.loading ?? (() => null),
}));

const STOPS: Stop[] = [
  {
    stop_id: "S0001",
    stop_name: "Ratna Park",
    lat: 27.7041,
    lng: 85.31,
    is_major_stop: true,
    is_interchange: false,
    status: "active",
  },
];

vi.mock("@/lib/api", () => ({
  getAllStops: vi.fn().mockResolvedValue(STOPS),
  getStop: vi.fn(),
}));

const { default: Home } = await import("@/app/page");

afterEach(cleanup);

describe("Home map region layering (D11)", () => {
  it("wraps the map in an isolated stacking context so the mobile sheet stays on top", async () => {
    await act(async () => {
      render(<Home />);
    });

    // The dynamic map's fallback lives directly inside the wrapper.
    const mapFallback = screen.getByText(/Loading map/);
    const wrapper = mapFallback.parentElement;

    expect(wrapper).not.toBeNull();
    expect(wrapper?.className.split(/\s+/)).toContain("isolate");

    // And that wrapper must sit next to (not inside) the bottom-sheet aside, so
    // the sheet's z-[500] wins against the isolated map contents.
    const aside = screen.getByRole("complementary");
    expect(aside.nextElementSibling).toBe(wrapper);
  });
});
