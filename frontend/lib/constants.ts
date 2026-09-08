// Route-leg palette, shared between components/BusMap.tsx (polylines on
// the map) and app/page.tsx (the matching sidebar legend). Previously
// defined independently in both files with a "kept in sync" comment --
// nothing enforced that, so a future palette change in one place could
// silently desync from the other. Import from here instead of
// redefining.
export const LEG_COLORS = ["#2563EB", "#0D9488", "#EA580C", "#DB2777"];

// Congestion-level palette, shared between components/map/useCongestionLayer.ts
// (the map polylines) and components/CongestionPanel.tsx (the matching
// legend) -- previously each defined this map independently, the same
// "kept in sync by hand" risk LEG_COLORS above already had.
export const CONGESTION_COLORS: Record<string, string> = {
  free_flow: "#22C55E",
  moderate: "#F59E0B",
  heavy: "#EF4444",
  unknown: "#6B7280",
};
