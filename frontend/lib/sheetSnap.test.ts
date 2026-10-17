import { describe, expect, it } from "vitest";
import {
  clampDragHeightPx,
  isNarrowViewport,
  nearestSnap,
  parseStoredSnap,
  snapHeightPx,
} from "./sheetSnap";

const VIEWPORT = 800;

describe("snapHeightPx", () => {
  it("minimized is a fixed pixel height regardless of viewport", () => {
    expect(snapHeightPx("minimized", VIEWPORT)).toBe(56);
    expect(snapHeightPx("minimized", 1200)).toBe(56);
  });

  it("half and full scale with viewport height", () => {
    expect(snapHeightPx("half", VIEWPORT)).toBeCloseTo(360, 0);
    expect(snapHeightPx("full", VIEWPORT)).toBeCloseTo(600, 0);
  });
});

describe("nearestSnap", () => {
  it("snaps to minimized when dragged close to the bottom", () => {
    expect(nearestSnap(80, VIEWPORT)).toBe("minimized");
  });

  it("snaps to half when dragged to roughly the middle", () => {
    expect(nearestSnap(350, VIEWPORT)).toBe("half");
  });

  it("snaps to full when dragged high", () => {
    expect(nearestSnap(650, VIEWPORT)).toBe("full");
  });

  it("picks the closer of two adjacent snaps at the midpoint", () => {
    const midBetweenMinimizedAndHalf = (snapHeightPx("minimized", VIEWPORT) + snapHeightPx("half", VIEWPORT)) / 2;
    // Exactly on the boundary -- either neighbor is an acceptable answer,
    // but it must be one of them, not "full".
    expect(["minimized", "half"]).toContain(nearestSnap(midBetweenMinimizedAndHalf, VIEWPORT));
  });
});

describe("clampDragHeightPx", () => {
  it("does not clamp values already in range", () => {
    expect(clampDragHeightPx(400, VIEWPORT)).toBe(400);
  });

  it("clamps below the floor", () => {
    expect(clampDragHeightPx(-100, VIEWPORT)).toBeGreaterThan(0);
  });

  it("clamps above the ceiling so the sheet never covers the navbar", () => {
    expect(clampDragHeightPx(10000, VIEWPORT)).toBeLessThan(VIEWPORT);
  });
});

describe("isNarrowViewport", () => {
  it("treats widths below the md breakpoint as the mobile sheet layout", () => {
    expect(isNarrowViewport(375)).toBe(true);
    expect(isNarrowViewport(767)).toBe(true);
  });

  it("treats the md breakpoint and above as desktop", () => {
    expect(isNarrowViewport(768)).toBe(false);
    expect(isNarrowViewport(1024)).toBe(false);
  });
});

describe("parseStoredSnap", () => {
  it("reads the old binary format for backward compatibility", () => {
    expect(parseStoredSnap("1")).toBe("minimized");
    expect(parseStoredSnap("0")).toBe("full");
  });

  it("reads a stored SheetSnap value directly", () => {
    expect(parseStoredSnap("half")).toBe("half");
    expect(parseStoredSnap("minimized")).toBe("minimized");
    expect(parseStoredSnap("full")).toBe("full");
  });

  it("defaults to full for anything else", () => {
    expect(parseStoredSnap(null)).toBe("full");
    expect(parseStoredSnap("garbage")).toBe("full");
    expect(parseStoredSnap("")).toBe("full");
  });
});
