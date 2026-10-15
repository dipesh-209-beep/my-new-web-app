import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useCongestion } from "@/hooks/useCongestion";
import * as api from "@/lib/api";
import { CongestionProvenance, CongestionResponse, CongestionSegment } from "@/types/route";

function makeSegment(overrides: Partial<CongestionSegment> = {}): CongestionSegment {
  return {
    route_id: "R0001",
    from_stop_id: "S0001",
    to_stop_id: "S0002",
    avg_duration_s: 300,
    avg_distance_m: 2000,
    free_flow_duration_s: 300,
    congestion_ratio: 1,
    congestion_level: "free_flow",
    sample_count: 1,
    is_seeded: true,
    ...overrides,
  };
}

const PROVENANCE: CongestionProvenance = {
  is_live: false,
  is_measured: false,
  note: "Estimated, not measured.",
};

function makeResponse(overrides: Partial<CongestionResponse> = {}): CongestionResponse {
  return {
    day_of_week: 4,
    hour_bucket: 6,
    segments: [],
    provenance: PROVENANCE,
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCongestion", () => {
  it("is off by default and makes no request until toggled on", () => {
    const spy = vi.spyOn(api, "getCongestion");
    const { result } = renderHook(() => useCongestion());

    expect(result.current.enabled).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("loads segments and provenance once enabled", async () => {
    vi.spyOn(api, "getCongestion").mockResolvedValue(
      makeResponse({ segments: [makeSegment({ is_seeded: false })] })
    );

    const { result } = renderHook(() => useCongestion());
    act(() => result.current.toggle());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.segments).toHaveLength(1);
    expect(result.current.provenance).toEqual(PROVENANCE);
    expect(result.current.error).toBe(false);
  });

  it("reports a failed load as an error, not as a valid empty result", async () => {
    vi.spyOn(api, "getCongestion").mockRejectedValue(new api.ApiError("boom", "network"));

    const { result } = renderHook(() => useCongestion());
    act(() => result.current.toggle());

    await waitFor(() => expect(result.current.loading).toBe(false));
    // The distinction that matters: empty + error must not read as
    // "checked, no congestion".
    expect(result.current.error).toBe(true);
    expect(result.current.segments).toEqual([]);
  });

  it("treats a null provenance from the API as null (no fabricated live flag)", async () => {
    vi.spyOn(api, "getCongestion").mockResolvedValue(makeResponse({ provenance: null }));

    const { result } = renderHook(() => useCongestion());
    act(() => result.current.toggle());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.provenance).toBeNull();
  });

  it("flags hasSeededOnly only when every segment is a seeded baseline", async () => {
    vi.spyOn(api, "getCongestion").mockResolvedValue(
      makeResponse({ segments: [makeSegment({ is_seeded: true }), makeSegment({ is_seeded: true })] })
    );

    const { result } = renderHook(() => useCongestion());
    act(() => result.current.toggle());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasSeededOnly).toBe(true);
  });

  it("clears hasSeededOnly when at least one segment is a recorded estimate", async () => {
    vi.spyOn(api, "getCongestion").mockResolvedValue(
      makeResponse({ segments: [makeSegment({ is_seeded: true }), makeSegment({ is_seeded: false })] })
    );

    const { result } = renderHook(() => useCongestion());
    act(() => result.current.toggle());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasSeededOnly).toBe(false);
  });
});
