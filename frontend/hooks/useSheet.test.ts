import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useSheet } from "@/hooks/useSheet";

const STORAGE_KEY = "ktm-transit:search-panel-minimized";

type PointerLike = React.PointerEvent<HTMLSpanElement>;

function pointerDown(clientY: number): PointerLike {
  return {
    clientY,
    pointerId: 1,
    currentTarget: { setPointerCapture: vi.fn() },
  } as unknown as PointerLike;
}

function pointerMove(clientY: number): PointerLike {
  return { clientY } as unknown as PointerLike;
}

beforeEach(() => {
  window.innerHeight = 800;
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSheet", () => {
  it("defaults to the full position before hydration", () => {
    const { result } = renderHook(() => useSheet());
    expect(result.current.sheetSnap).toBe("full");
    expect(result.current.sidebarHydrated).toBe(false);
    expect(result.current.showMinimized).toBe(false);
  });

  it("hydrates the stored position from localStorage", async () => {
    window.localStorage.setItem(STORAGE_KEY, "half");
    const { result } = renderHook(() => useSheet());
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));
    expect(result.current.sheetSnap).toBe("half");
  });

  it("still reads the legacy minimized flag", async () => {
    window.localStorage.setItem(STORAGE_KEY, "1");
    const { result } = renderHook(() => useSheet());
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));
    expect(result.current.sheetSnap).toBe("minimized");
    expect(result.current.showMinimized).toBe(true);
  });

  it("persists a new position and invalidates the map", async () => {
    const invalidateMap = vi.fn();
    const { result } = renderHook(() => useSheet(invalidateMap));
    act(() => {
      result.current.setSheetSnap("half");
    });
    expect(result.current.sheetSnap).toBe("half");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("half");
    await waitFor(() => expect(invalidateMap).toHaveBeenCalled());
  });

  it("toggles minimized <-> full without ever landing on half", () => {
    const { result } = renderHook(() => useSheet());
    act(() => {
      result.current.toggleSidebarMinimized();
    });
    expect(result.current.sheetSnap).toBe("minimized");
    act(() => {
      result.current.toggleSidebarMinimized();
    });
    expect(result.current.sheetSnap).toBe("full");
    act(() => {
      result.current.setSheetSnap("half");
      result.current.toggleSidebarMinimized();
    });
    expect(result.current.sheetSnap).toBe("minimized");
  });

  it("snaps a pointer drag to the nearest position", () => {
    const { result } = renderHook(() => useSheet());
    // Release near the half height (45% of 800 = 360): start at full (600),
    // drag so the live height lands on 360.
    act(() => {
      result.current.handleHandlePointerDown(pointerDown(600));
    });
    act(() => {
      result.current.handleHandlePointerMove(pointerMove(840));
    });
    expect(result.current.dragHeightPx).toBe(360);
    act(() => {
      result.current.handleHandlePointerUp();
    });
    expect(result.current.sheetSnap).toBe("half");
    expect(result.current.dragHeightPx).toBeNull();
  });

  it("clamps a drag past the viewport", () => {
    const { result } = renderHook(() => useSheet());
    act(() => {
      result.current.handleHandlePointerDown(pointerDown(600));
    });
    act(() => {
      result.current.handleHandlePointerMove(pointerMove(-4000));
    });
    // 92% of an 800px viewport.
    expect(result.current.dragHeightPx).toBe(736);
  });
});
