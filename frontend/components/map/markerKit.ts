"use client";

/**
 * markerKit.ts
 *
 * Single source for the map's marker shapes. Before this file, five
 * different hooks (useAllStopsLayer, useRouteBrowserLayer,
 * useRouteResultLayer, useUserLocationLayer, useRecenterControl) each
 * built their own `L.divIcon` HTML string from scratch -- every stop,
 * origin, destination, and transfer point rendered as the same
 * white-bordered colored circle with a drop shadow, which is the default
 * shape any map library gives you for free. That's the generic-map-pin
 * look; nothing in it reads as "Kathmandu bus stop" rather than "generic
 * location."
 *
 * This file gives the app two shapes instead, each carrying meaning:
 *
 *  - a small rectangular "tag" (busTagIcon) for anything that IS a bus
 *    stop -- individual stops on the all-stops layer, sequenced stops on
 *    a browsed route, intermediate stops along a found trip. Rectangular
 *    and dark like a physical route-board plate, with the bus glyph
 *    doing double duty as icon and meaning, rather than a color-only dot.
 *  - a teardrop pin (pinIcon) reserved for the handful of markers that
 *    are genuinely single, non-repeating points: trip origin,
 *    destination, and the user's own location. Keeping stops and
 *    "special points" visually distinct is itself useful information --
 *    at a glance, dots vs. tags tells you which markers are "a stop you
 *    could tap" vs. "the specific point this search is about."
 *
 * `brand.DEFAULT` (marigold, see tailwind.config.ts) is used in exactly
 * one place here -- the active/hover/picked stop -- so it keeps meaning
 * "this one, right now" instead of becoming another color in the mix.
 * Every other color passed in is a functional route/leg color, not a
 * brand color.
 */
import L from "leaflet";

const INK = "#1B1A2E";
const INK_DIM = "#C9C7D6";
const BRAND = "#E0A614";

/** WCAG 2.5.5 target size (also the app's 44px convention, see
 * components/map/useRecenterControl.ts). Interactive markers wrap their
 * visual in a centered box at least this large so the tap/click target meets
 * the goal while the drawn marker keeps its natural size. */
const MIN_TARGET_PX = 44;

/** Center a marker's visual inside a hit box that is at least
 * MIN_TARGET_PX square, keeping the visual's own dimensions. The icon
 * anchor stays at the center, so the drawn marker still sits exactly on its
 * coordinate -- only the clickable area grows. */
function withHitArea(
  inner: string,
  visualWidth: number,
  visualHeight: number
): { html: string; iconSize: [number, number]; iconAnchor: [number, number] } {
  const width = Math.max(MIN_TARGET_PX, visualWidth);
  const height = Math.max(MIN_TARGET_PX, visualHeight);
  return {
    html: `<span style="display:flex;align-items:center;justify-content:center;width:${width}px;height:${height}px;">${inner}</span>`,
    iconSize: [width, height],
    iconAnchor: [width / 2, height / 2],
  };
}

/** Bottom-anchored variant of `withHitArea` for the teardrop pins. A pin's tip
 * is the geographic point, so the hit box grows *upward* (the visual sits at
 * the bottom-center of the box) and the icon anchor stays on the tip. A
 * centered hit box would lift the tip off the coordinate. */
function withBottomAnchoredHitArea(
  inner: string,
  visualWidth: number,
  visualHeight: number
): { html: string; iconSize: [number, number]; iconAnchor: [number, number] } {
  const width = Math.max(MIN_TARGET_PX, visualWidth);
  const height = Math.max(MIN_TARGET_PX, visualHeight);
  return {
    html: `<span style="display:flex;align-items:flex-end;justify-content:center;width:${width}px;height:${height}px;">${inner}</span>`,
    iconSize: [width, height],
    iconAnchor: [width / 2, height],
  };
}

const BUS_GLYPH_PATH =
  "M5 4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v11a2 2 0 0 1-1 1.73V19a1 1 0 0 1-1 1h-1a1 1 0 0 1-1-1v-1H8v1a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-2.27A2 2 0 0 1 4 15V4Zm2 0v6h10V4H7Zm.5 9.25a1.25 1.25 0 1 0 0 2.5 1.25 1.25 0 0 0 0-2.5Zm9 0a1.25 1.25 0 1 0 0 2.5 1.25 1.25 0 0 0 0-2.5Z";

function busGlyph(fill: string, size: number): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="${fill}"><path d="${BUS_GLYPH_PATH}"/></svg>`;
}

export type StopTagState = "default" | "dimmed" | "active";

/**
 * A single bus stop: a small rounded-rectangle plate with the bus glyph,
 * in a route-board silhouette rather than a plain dot. `state` covers the
 * three looks every stop marker in this app needs: normal, dimmed
 * (search-in-progress background context), and active (hover / current
 * pick target) -- callers swap icons via `marker.setIcon(...)` rather
 * than toggling a CSS class, matching how Leaflet divIcons already work
 * elsewhere in this codebase.
 */
export function busTagIcon(state: StopTagState = "default"): L.DivIcon {
  const isActive = state === "active";
  const isDimmed = state === "dimmed";
  const bg = isActive ? BRAND : isDimmed ? INK_DIM : INK;
  const glyphFill = isActive ? INK : "#ffffff";
  const w = isActive ? 24 : 18;
  const h = isActive ? 18 : 14;
  const glyphSize = isActive ? 13 : 10;

  return L.divIcon({
    className: "",
    ...withHitArea(
      `<span style="display:flex;align-items:center;justify-content:center;
      width:${w}px;height:${h}px;border-radius:3px;background:${bg};
      border:1.5px solid #ffffff;box-shadow:0 0 0 1px rgba(27,26,46,0.35);">
      ${busGlyph(glyphFill, glyphSize)}</span>`,
      w,
      h
    ),
  });
}

/**
 * Sequenced stop along a browsed/found route -- same tag silhouette as
 * busTagIcon, but with the route's own line color as the plate and the
 * sequence number instead of the bus glyph (the glyph would be redundant
 * once you're already looking at one specific route's stop list; the
 * number is the useful information here).
 */
export function sequencedStopIcon(sequenceNo: number, color: string): L.DivIcon {
  return L.divIcon({
    className: "",
    html: `<span style="display:flex;align-items:center;justify-content:center;
      min-width:19px;height:16px;padding:0 3px;border-radius:3px;background:${color};
      color:#ffffff;font-size:10px;font-weight:700;font-variant-numeric:tabular-nums;
      border:1.5px solid #ffffff;box-shadow:0 0 0 1px rgba(27,26,46,0.35);">
      ${sequenceNo}</span>`,
    iconSize: [19, 16],
    iconAnchor: [9.5, 8],
  });
}

export type PinRole = "origin" | "destination" | "transfer" | "user";

// Origin=blue/destination=red matches the pre-existing convention already
// used by SearchForm.tsx's From/To connector dots (and is the near-
// universal "start/end" mapping-app convention) -- these pins intentionally
// do NOT reach for the brand marigold. Marigold is reserved for the user's
// own location and the active/picked stop state below, where it can't
// collide with an existing, already-understood color meaning. It also
// matters for a reason beyond consistency: marigold's contrast against
// white is too low to double as a small-text/link color (~2.2:1), so
// confining it to icon fills (which pair it with white/ink glyphs, not
// text-on-white) keeps every use of it accessible.
const PIN_COLOR: Record<PinRole, string> = {
  origin: "#2563EB",
  destination: "#DC2626",
  transfer: "#7C3AED", // unchanged functional purple, matches existing leg/transfer color
  user: BRAND,
};

const PIN_SIZE: Record<PinRole, number> = {
  origin: 26,
  destination: 26,
  transfer: 16,
  user: 18,
};

/**
 * Teardrop pin for the small set of markers that are genuinely singular
 * points rather than one-of-many stops: trip origin, destination, a
 * transfer point, or the user's own location. A pin shape (vs. the
 * rectangular stop tag) is itself the signal "this is the specific point
 * the search/trip is about," independent of its color.
 */
export function pinIcon(role: PinRole): L.DivIcon {
  const color = PIN_COLOR[role];
  const size = PIN_SIZE[role];
  const glyph =
    role === "user"
      ? `<circle cx="12" cy="9.5" r="3" fill="#ffffff"/>`
      : role === "transfer"
        ? ""
        : `<circle cx="12" cy="9.5" r="3.4" fill="#ffffff"/>`;

  return L.divIcon({
    className: "",
    ...withBottomAnchoredHitArea(
      `<svg width="${size}" height="${size}" viewBox="0 0 24 24">
      <path d="M12 22s-7.5-6.8-7.5-12A7.5 7.5 0 0 1 19.5 10c0 5.2-7.5 12-7.5 12Z"
        fill="${color}" stroke="#ffffff" stroke-width="1.5"/>
      ${glyph}
    </svg>`,
      size,
      size
    ),
    // Relative to the anchor (the tip), not the box: popups still open at the
    // top of the pin regardless of how large the hit box is.
    popupAnchor: [0, -size],
  });
}

/** The pulsing "you are here" dot -- kept circular (a pin would be an
 * odd metaphor for your own live position) but recolored to the brand
 * mark, matching pinIcon("user") for the marker itself. */
export function userLocationPulseIcon(): L.DivIcon {
  return L.divIcon({
    className: "",
    // Centered marker, so the centered hit area keeps the dot on its
    // coordinate; the 32px pulse ring (16px dot + 8px inset) still fits.
    ...withHitArea(
      `<span style="position:relative;display:block;width:16px;height:16px;">
        <span style="position:absolute;inset:-8px;border-radius:9999px;background:rgba(224,166,20,0.25);"></span>
        <span style="position:absolute;inset:0;border-radius:9999px;background:${BRAND};border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(27,26,46,0.35);"></span>
      </span>`,
      16,
      16
    ),
  });
}

/** Cluster bubble for the all-stops layer -- kept circular (a cluster
 * genuinely is a count, and a circular badge is the clearest shape for a
 * number at a glance) but recolored from neutral gray to ink, matching
 * the rest of the kit instead of standing apart from it. */
export function clusterIcon(count: number): L.DivIcon {
  const size = count >= 100 ? 34 : count >= 10 ? 30 : 26;
  return L.divIcon({
    className: "",
    ...withHitArea(
      `<span style="display:flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;border-radius:9999px;background:${INK};color:#ffffff;font-size:11px;font-weight:700;border:2px solid #ffffff;box-shadow:0 0 0 1px rgba(27,26,46,0.35);">${count}</span>`,
      size,
      size
    ),
  });
}
