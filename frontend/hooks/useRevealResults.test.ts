import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRevealResults } from "@/hooks/useRevealResults";
import { RouteSearchResult } from "@/types/route";

const found: RouteSearchResult = {
  found: true,
  origin_stop_id: "S1",
  destination_stop_id: "S2",
  total_cost: 10,
  transfer_count: 0,
  fare: null,
  alternatives: [],
  legs: [],
};

function render(
  initialResult: RouteSearchResult | null,
  revealResults: () => void
) {
  return renderHook(
    ({ result }: { result: RouteSearchResult | null }) =>
      useRevealResults(result, revealResults),
    { initialProps: { result: initialResult } }
  );
}

describe("useRevealResults", () => {
  it("stays quiet with no result (idle, loading or error states)", () => {
    const reveal = vi.fn();
    const { rerender } = render(null, reveal);
    expect(reveal).not.toHaveBeenCalled();

    rerender({ result: null });
    expect(reveal).not.toHaveBeenCalled();
  });

  it("stays quiet for a no-route result, leaving the empty state as-is", () => {
    const reveal = vi.fn();
    render({ found: false }, reveal);
    expect(reveal).not.toHaveBeenCalled();
  });

  it("reveals the sheet once when a search succeeds", () => {
    const reveal = vi.fn();
    render(found, reveal);
    expect(reveal).toHaveBeenCalledTimes(1);
  });

  it("does not re-fire for the same result object across re-renders", () => {
    const reveal = vi.fn();
    const { rerender } = render(found, reveal);
    rerender({ result: found });
    rerender({ result: found });
    expect(reveal).toHaveBeenCalledTimes(1);
  });

  it("reveals again for a new successful result (another search)", () => {
    const reveal = vi.fn();
    const { rerender } = render(found, reveal);
    rerender({ result: { ...found } });
    expect(reveal).toHaveBeenCalledTimes(2);
  });
});
