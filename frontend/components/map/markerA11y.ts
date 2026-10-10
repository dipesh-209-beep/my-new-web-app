"use client";

import L from "leaflet";

/**
 * markerA11y.ts
 *
 * Keyboard affordances for Leaflet markers.
 *
 * Leaflet's `L.marker` defaults to `keyboard: true`, which puts a
 * `tabindex="0"` and `role="button"` on every marker icon but attaches no
 * key handler (see `Marker._initInteraction` in leaflet-src.js). The result
 * is exactly the wrong combination for the all-stops layer: hundreds of
 * buttons that a keyboard or screen-reader user can tab to but cannot
 * activate.
 *
 * The map layers therefore create markers with `keyboard: false` (so Leaflet
 * adds nothing) and opt each marker in here. Two concerns are kept separate:
 *
 *   - `setMarkerFocusable` controls whether a marker is in the tab order at
 *     all. Only markers that actually do something are focusable; inert ones
 *     (e.g. a stop marker while map-picking is off) get `tabindex="-1"` and
 *     no button role, so they are skipped by Tab while remaining programmatic.
 *   - `bindMarkerActivation` makes Enter/Space perform the marker's primary
 *     action -- the same one a mouse click performs (zoom a cluster, pick a
 *     stop, open a popup). Because the handler is bound to the icon element
 *     and calls the action directly, a div icon does not also emit a native
 *     click, so there is no double activation.
 */

/** Keys that should activate a marker like a click: Enter, plus both the
 * modern (" ") and legacy ("Spacebar") names for the space bar. */
const ACTIVATION_KEYS = new Set(["Enter", " ", "Spacebar"]);

/**
 * Tracks the keydown handler bound to each icon element so re-binding an
 * element (Leaflet recreates the icon element on `setIcon`, e.g. on hover)
 * replaces the old listener instead of stacking a second one. A WeakMap
 * means removed elements are collected without any explicit cleanup.
 */
const boundKeyHandlers = new WeakMap<Element, EventListener>();

/**
 * Marker -> its `add` lifecycle listener, so re-registering replaces the
 * previous one instead of stacking (mirrors `boundKeyHandlers`).
 */
const lifecycleHandlers = new WeakMap<L.Marker, () => void>();

/**
 * Keep a marker decorated across its whole add/remove/re-add lifecycle.
 *
 * Leaflet builds a marker's icon element in `Marker._initIcon`, which runs in
 * `onAdd` -- i.e. only once the marker is on a map -- and destroys it in
 * `onRemove`. A marker that is toggled off and back on (the route legend does
 * exactly this) therefore gets a brand-new element, and anything attached to
 * the old one is gone. Decorating once at construction therefore either does
 * nothing (no element yet) or is silently lost on the next toggle; registering
 * on the layer's `add` event is what keeps the affordance alive.
 *
 * `decorate` must be idempotent, since it runs once per element and may be
 * invoked repeatedly for the same element. It is also called immediately when
 * the marker is already on a map.
 */
export function bindMarkerLifecycle(marker: L.Marker, decorate: () => void): void {
  const previous = lifecycleHandlers.get(marker);
  if (previous) marker.off("add", previous);
  marker.on("add", decorate);
  lifecycleHandlers.set(marker, decorate);
  if (marker.getElement()) decorate();
}

/**
 * Put a marker's icon element into or out of the keyboard tab order.
 *
 * `focusable` markers get `role="button"` + `tabindex="0"`; non-focusable
 * markers are removed from the sequence with `tabindex="-1"` and lose the
 * button role so assistive technology does not announce a no-op control.
 */
export function setMarkerFocusable(marker: L.Marker, focusable: boolean): void {
  const el = marker.getElement();
  if (!el) return;

  if (focusable) {
    el.setAttribute("role", "button");
    el.tabIndex = 0;
  } else {
    el.removeAttribute("role");
    el.tabIndex = -1;
  }
}

export interface MarkerActivation {
  /** Accessible name announced for the marker. */
  label: string;
  /** The marker's primary action -- the same one a mouse click performs. */
  onActivate: () => void;
}

/**
 * Wire Enter/Space on a marker's icon element to `onActivate`. Space is
 * preventDefault-ed (otherwise it scrolls the page) and the event is stopped
 * so Leaflet's map-level keyboard handler does not also react.
 *
 * Safe to call again after `setIcon` replaces the icon element: the previous
 * handler for that element is removed first.
 */
export function bindMarkerActivation(
  marker: L.Marker,
  { label, onActivate }: { label: string; onActivate: () => void }
): void {
  const el = marker.getElement();
  if (!el) return;

  el.setAttribute("aria-label", label);

  const previous = boundKeyHandlers.get(el);
  if (previous) el.removeEventListener("keydown", previous);

  // Typed as EventListener so it can live in the WeakMap and round-trip
  // through add/removeEventListener; the key check narrows it at runtime.
  const handler: EventListener = (event) => {
    const { key } = event as KeyboardEvent;
    if (!ACTIVATION_KEYS.has(key)) return;
    // Space scrolls the page by default; suppress that before it happens.
    event.preventDefault();
    // Stop the map's own key handler from also reacting (Enter is used by
    // several Leaflet controls, and arrow/zoom keys should not leak through).
    event.stopPropagation();
    onActivate();
  };
  el.addEventListener("keydown", handler);
  boundKeyHandlers.set(el, handler);
}

/** Convenience wrapper for markers that are always actionable (clusters,
 * popup pins): focusable, labelled, and activation-bound, and re-applied
 * whenever Leaflet (re)creates the marker's element on add. */
export function makeMarkerInteractive(
  marker: L.Marker,
  options: { label: string; onActivate: () => void }
): void {
  bindMarkerLifecycle(marker, () => {
    setMarkerFocusable(marker, true);
    bindMarkerActivation(marker, options);
  });
}
