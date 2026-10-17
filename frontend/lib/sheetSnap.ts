/**
 * Snap-point math for the mobile search-panel bottom sheet
 * (app/page.tsx). Pulled out as pure functions so the actual snapping
 * decision -- "given where the user let go, which of the three stops is
 * this closest to" -- is unit-testable without a browser/touch
 * environment, even though the drag gesture itself (pointer events) isn't.
 *
 * Three stops, same idea as Google/Apple Maps' bottom sheet:
 *   minimized -- a thin strip, just enough to restore it
 *   half      -- enough to see the form, map still mostly visible
 *   full      -- the original single "expanded" state this sheet had
 *                before drag support existed
 */
export type SheetSnap = "minimized" | "full" | "half";

const MINIMIZED_HEIGHT_PX = 56;
const HALF_HEIGHT_FRACTION = 0.45;
const FULL_HEIGHT_FRACTION = 0.75;

/** Width below which the bottom sheet is the mobile layout. Matches the
 * Tailwind `md:` breakpoint (min-width: 768px) used throughout app/page.tsx,
 * so JS-driven sheet decisions agree with the CSS that lays the page out. */
export const MOBILE_BREAKPOINT_PX = 768;

/** True on the narrow viewports where the search panel is an overlay sheet.
 * On wider viewports the panel is a static column and its snap height is
 * ignored (`md:max-h-none`), so sheet-position logic must not run there. */
export function isNarrowViewport(viewportWidthPx: number): boolean {
  return viewportWidthPx < MOBILE_BREAKPOINT_PX;
}

/** The sheet's target height in px for a given snap point, at the given
 * viewport height. */
export function snapHeightPx(snap: SheetSnap, viewportHeightPx: number): number {
  if (snap === "minimized") return MINIMIZED_HEIGHT_PX;
  if (snap === "half") return viewportHeightPx * HALF_HEIGHT_FRACTION;
  return viewportHeightPx * FULL_HEIGHT_FRACTION;
}

/** Given a height the user dragged the sheet to, find the closest of the
 * three snap points to settle on release. */
export function nearestSnap(heightPx: number, viewportHeightPx: number): SheetSnap {
  const snaps: SheetSnap[] = ["minimized", "half", "full"];
  let best: SheetSnap = "full";
  let bestDistance = Infinity;
  for (const snap of snaps) {
    const distance = Math.abs(snapHeightPx(snap, viewportHeightPx) - heightPx);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = snap;
    }
  }
  return best;
}

/** Clamp a live drag height to a sane range -- a little below "minimized"
 * so the handle doesn't disappear off the bottom edge, and a little below
 * the full viewport so the sheet never covers the navbar. */
export function clampDragHeightPx(heightPx: number, viewportHeightPx: number): number {
  return Math.max(MINIMIZED_HEIGHT_PX * 0.7, Math.min(viewportHeightPx * 0.92, heightPx));
}

/** localStorage previously stored "1"/"0" for minimized/full (before the
 * "half" state existed). Reads either the old format or a stored SheetSnap
 * value, defaulting to "full" for anything else (missing key, corrupted
 * value, private-browsing storage that silently no-ops).
 */
export function parseStoredSnap(raw: string | null): SheetSnap {
  if (raw === "1") return "minimized";
  if (raw === "0") return "full";
  if (raw === "minimized" || raw === "half" || raw === "full") return raw;
  return "full";
}
