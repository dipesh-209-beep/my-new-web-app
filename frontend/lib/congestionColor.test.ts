import { describe, expect, it } from "vitest";
import {
  HEAVY_RATIO,
  MODERATE_RATIO,
  classifyCongestionRatio,
  congestionLevelColor,
  getCongestionColor,
} from "@/lib/congestionColor";
import { CONGESTION_COLORS } from "@/lib/constants";

// The frontend mirror of backend/app/api/congestion.py `_classify`
// (_MODERATE_RATIO = 1.15, _HEAVY_RATIO = 1.5). These assertions pin the
// boundary behaviour on both sides of each threshold so the map, the
// popups and the legends can never classify the same ratio differently.
describe("congestion severity thresholds", () => {
  it("classifies just below, at, and just above the moderate threshold", () => {
    expect(classifyCongestionRatio(MODERATE_RATIO - 0.01)).toBe("free_flow");
    expect(classifyCongestionRatio(MODERATE_RATIO)).toBe("moderate");
    expect(classifyCongestionRatio(MODERATE_RATIO + 0.01)).toBe("moderate");
  });

  it("classifies just below, at, and just above the heavy threshold", () => {
    expect(classifyCongestionRatio(HEAVY_RATIO - 0.01)).toBe("moderate");
    expect(classifyCongestionRatio(HEAVY_RATIO)).toBe("heavy");
    expect(classifyCongestionRatio(HEAVY_RATIO + 0.01)).toBe("heavy");
  });

  it("treats a non-numeric ratio as unknown but a large ratio as heavy", () => {
    expect(classifyCongestionRatio(Number.NaN)).toBe("unknown");
    expect(classifyCongestionRatio(Number.POSITIVE_INFINITY)).toBe("heavy");
  });
});

describe("severity-to-colour contract", () => {
  it("resolves every severity through the one shared palette", () => {
    expect(congestionLevelColor("free_flow")).toBe(CONGESTION_COLORS.free_flow);
    expect(congestionLevelColor("moderate")).toBe(CONGESTION_COLORS.moderate);
    expect(congestionLevelColor("heavy")).toBe(CONGESTION_COLORS.heavy);
    expect(congestionLevelColor("unknown")).toBe(CONGESTION_COLORS.unknown);
  });

  it("keeps the ratio helper and the level helper in agreement (map == legend)", () => {
    expect(getCongestionColor(1.0)).toBe(congestionLevelColor("free_flow"));
    expect(getCongestionColor(MODERATE_RATIO)).toBe(congestionLevelColor("moderate"));
    expect(getCongestionColor(HEAVY_RATIO)).toBe(congestionLevelColor("heavy"));
    expect(getCongestionColor(9)).toBe(congestionLevelColor("heavy"));
  });
});
