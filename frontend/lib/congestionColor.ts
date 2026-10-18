import { CONGESTION_COLORS } from "@/lib/constants";
import type { CongestionLevel } from "@/types/route";

// Frontend mirror of the backend's ratio thresholds
// (backend/app/api/congestion.py `_MODERATE_RATIO` / `_HEAVY_RATIO`). The
// API already sends a `congestion_level` per segment; these exist so a bare
// ratio (e.g. from a payload that omitted the level) is classified the same
// way. If they ever drift from the backend, a segment could be labelled
// "heavy" in its popup but drawn "moderate" on the map.
export const MODERATE_RATIO = 1.15;
export const HEAVY_RATIO = 1.5;

/** Map a congestion ratio to the backend's severity buckets. A non-numeric
 * ratio (NaN) has no severity, so it resolves to `unknown` rather than being
 * silently clamped to an arbitrary end of the scale. */
export function classifyCongestionRatio(ratio: number): CongestionLevel {
  if (Number.isNaN(ratio)) return "unknown";
  if (ratio >= HEAVY_RATIO) return "heavy";
  if (ratio >= MODERATE_RATIO) return "moderate";
  return "free_flow";
}

/** The one severity->colour lookup. Falls back to `unknown` for a level this
 * build does not recognise, so an unrecognised severity is neutral grey
 * rather than a wrong colour (see CONGESTION_COLORS in lib/constants.ts). */
export function congestionLevelColor(level: CongestionLevel): string {
  return CONGESTION_COLORS[level] ?? CONGESTION_COLORS.unknown;
}

/** Colour for a raw ratio. Kept as the ratio-friendly entry point; it simply
 * classifies the ratio and defers to congestionLevelColor, so the map and the
 * legends resolve through the same severity->colour contract and cannot
 * disagree. */
export function getCongestionColor(ratio: number): string {
  return congestionLevelColor(classifyCongestionRatio(ratio));
}
