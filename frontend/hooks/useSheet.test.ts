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
  // jsdom's default is 1024 (desktop). Tests that need the mobile sheet
  // layout set this to a narrow width themselves.
  window.innerWidth = 1024;
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

describe("useSheet revealResults", () => {
  it("drops full to half on a narrow viewport so the map stays usable", async () => {
    window.innerWidth = 375;
    const invalidateMap = vi.fn();
    const { result } = renderHook(() => useSheet(invalidateMap));
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));
    expect(result.current.sheetSnap).toBe("full");

    act(() => {
      result.current.revealResults();
    });

    expect(result.current.sheetSnap).toBe("half");
    // The map must be told to recalculate its size at the new sheet height.
    await waitFor(() => expect(invalidateMap).toHaveBeenCalled());
  });

  it("leaves the desktop layout untouched", async () => {
    window.innerWidth = 1024;
    const invalidateMap = vi.fn();
    const { result } = renderHook(() => useSheet(invalidateMap));
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));

    act(() => {
      result.current.revealResults();
    });

    expect(result.current.sheetSnap).toBe("full");
    expect(invalidateMap).not.toHaveBeenCalled();
  });

  it("never overrides a deliberate minimize or an already-half sheet", async () => {
    window.innerWidth = 375;
    const { result } = renderHook(() => useSheet());
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));

    act(() => {
      result.current.setSheetSnap("minimized");
    });
    act(() => {
      result.current.revealResults();
    });
    expect(result.current.sheetSnap).toBe("minimized");

    act(() => {
      result.current.setSheetSnap("half");
    });
    act(() => {
      result.current.revealResults();
    });
    expect(result.current.sheetSnap).toBe("half");
  });

  it("does not persist the automatic half position over the saved default", async () => {
    window.innerWidth = 375;
    window.localStorage.setItem(STORAGE_KEY, "full");
    const { result } = renderHook(() => useSheet());
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));

    act(() => {
      result.current.revealResults();
    });

    expect(result.current.sheetSnap).toBe("half");
    // Per-session only: a reload should still restore the user's choice.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("full");
  });

  it("keeps pointer dragging working after an automatic reveal", async () => {
    window.innerWidth = 375;
    const { result } = renderHook(() => useSheet());
    await waitFor(() => expect(result.current.sidebarHydrated).toBe(true));
    act(() => {
      result.current.revealResults();
    });
    expect(result.current.sheetSnap).toBe("half");

    // Drag from the half height (360) down toward minimized and release.
    act(() => {
      result.current.handleHandlePointerDown(pointerDown(360));
    });
    act(() => {
      result.current.handleHandlePointerMove(pointerMove(520));
    });
    act(() => {
      result.current.handleHandlePointerUp();
    });
    expect(result.current.sheetSnap).toBe("minimized");
  });
});
