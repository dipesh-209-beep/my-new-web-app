import type { CongestionLevel } from "@/types/route";

// Route-leg palette, shared between components/BusMap.tsx (polylines on
// the map) and app/page.tsx (the matching sidebar legend). Previously
// defined independently in both files with a "kept in sync" comment --
// nothing enforced that, so a future palette change in one place could
// silently desync from the other. Import from here instead of
// redefining.
export const LEG_COLORS = ["#2563EB", "#0D9488", "#EA580C", "#DB2777"];

// Congestion severity palette, keyed by the backend's congestion_level
// (backend/app/api/congestion.py `_classify`). This is the single
// severity->colour contract: the map polylines
// (components/map/useCongestionLayer.ts) and BOTH legends (the Traffic
// panel and the on-map key) resolve a severity through this one map, so a
// colour cannot mean "moderate" on the map and "heavy" in a legend. It is
// keyed by level rather than by raw ratio precisely because a continuous
// ratio ramp cannot be reproduced by a three-label legend -- that mismatch
// was the bug this replaced (see lib/congestionColor.ts).
//
// `unknown` is used: it is the explicit fallback for a severity this build
// does not recognise. The frontend CongestionLevel type permits it (see
// types/route.ts) even though the current API only ever emits
// free_flow/moderate/heavy, so a future level renders neutral grey instead
// of the wrong colour -- see congestionLevelColor() in lib/congestionColor.ts.
export const CONGESTION_COLORS: Record<CongestionLevel, string> = {
  free_flow: "#22C55E",
  moderate: "#F59E0B",
  heavy: "#EF4444",
  unknown: "#6B7280",
};

// The ordered (low -> high) severity rows every congestion legend renders:
// one swatch + one text label each. Text labels (not colour alone) carry the
// meaning, and sharing this one list keeps the Traffic panel and the on-map
// key in the same order with the same wording.
export const CONGESTION_LEGEND: { level: CongestionLevel; label: string }[] = [
  { level: "free_flow", label: "Low" },
  { level: "moderate", label: "Moderate" },
  { level: "heavy", label: "Heavy" },
];

// Hairline outline for the congestion legend swatches and the on-map key's
// container, so the legend reads as a bounded graphical object on white:
// WCAG 1.4.11 wants >=3:1 for non-text indicators, and `ink.tertiary`
// #767194 is 4.61:1. This only bounds the swatches -- it does NOT make the
// fills themselves compliant (their light end sits below 3:1; see
// ACCESSIBILITY.md, gap 5).
export const CONGESTION_LEGEND_OUTLINE = "#767194";

// Route-finder "transfer" legs are identified on the client by this sentinel
// route_id (the backend's schemas emit "TRANSFER" for walking legs between
// buses). Single source of truth so the "is this a walk?" check reads one
// name everywhere it appears.
export const TRANSFER_ROUTE_ID = "TRANSFER";

// Request timeouts for fetch() wrappers. Most calls are quick DB reads;
// route-finder/geometry call out to OSRM so get a longer allowance. Kept in
// one place so lib/api.ts, lib/adminApi.ts, and lib/userApi.ts don't each
// redefine a magic number.
export const DEFAULT_TIMEOUT_MS = 10_000;
export const ROUTING_TIMEOUT_MS = 20_000;
