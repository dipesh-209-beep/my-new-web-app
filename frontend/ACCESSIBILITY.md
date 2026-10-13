# Accessibility

**Last reviewed: 2026-10-13**

This is an honest assessment, not a conformance claim. **This application has
not been audited against WCAG** and no conformance level (A, AA, or AAA) is
claimed. What follows is what was checked in the code, what is known to be
weak, and how to check it yourself.

## What is in place

Verified by reading the source, not by audit:

- **`<html lang="en">`** (`app/layout.tsx`) — required for correct screen-reader
  pronunciation.
- **The autocomplete is a correctly built combobox** (`components/search/StopAutocomplete.tsx`):
  `role="combobox"` with `aria-expanded`, `aria-controls`, `aria-autocomplete`,
  and `aria-activedescendant` tracking the highlighted option, plus keyboard
  handling for arrows/Enter/Escape. This is the single most important
  interactive widget on the page and it is done properly.
- **Form controls are labelled.** Inputs are wrapped in `<label>` elements
  with visible text, or given an `sr-only` `<label htmlFor>`. Accessible names
  are provided by real markup, not by `placeholder`.
- **Decorative icons are `aria-hidden`** (`components/icons/TransitIcons.tsx`),
  so they are not announced as unlabelled graphics.
- **A single, consistent focus indicator** (`:focus-visible`, 2px solid
  `#e0a614` with 2px offset) rather than relying on per-browser defaults that
  are easy to lose against a light theme.
- **Landmarks**: `nav` in `NavBar.tsx`, and one `main#main-content` in
  `app/layout.tsx` that every page renders into (so `/stops` and `/routes`
  share it).
- **A skip link** to `#main-content`, first in the tab order, visually hidden
  until focused. Without it a keyboard user traverses the whole nav bar on
  every page load.
- **`prefers-reduced-motion` is honoured** (`app/globals.css`). Transitions
  and animations are collapsed to 0.01ms rather than 0, so anything waiting on
  `transitionend` (Leaflet's fades, the bottom sheet) still fires; Leaflet's
  own zoom/pan easing is disabled outright.
- **Congestion is not encoded by colour alone at the point of reading.**
  Segment popups pair the colour with a text label ("Free-flow", "Moderate
  congestion", "Heavy congestion"), a numeric ratio (`2.4x free-flow`), and a
  sample count — so a user who cannot distinguish the gradient colours still
  gets the information.
- **Automated checks in CI.** `jsx-a11y` rules are errors in
  `eslint.config.mjs`, not warnings: `label-has-associated-control`,
  `click-events-have-key-events`, `no-static-element-interactions`,
  `interactive-supports-focus`, `no-noninteractive-element-interactions`,
  `no-noninteractive-tabindex`, `anchor-is-valid`, `heading-has-content`, plus
  `alt-text` and the four `aria-*` correctness rules promoted from next's
  warnings. The codebase currently passes all of them. Runtime structure/ARIA
  checks (`axe-core` via `vitest-axe`, in `npm test`) cover the components and
  states listed under gap 3.
- **Touch targets are at least 44×44 CSS px** (WCAG 2.5.5 Target Size) for the
  controls this project authors: nav links and the brand mark, the
  sign-in/minimize/restore buttons, the autocomplete clear buttons, the
  origin/destination swap, "use my location"/"pick on map"/"+ Add a stop",
  the via-stop remove button, the Submit button, the map-layer and traffic
  toggle pills, the congestion day/hour selects and "Reset to now", the route
  alternative pills, the bottom-sheet toggle, the map's zoom and recenter
  controls, and the all-stops cluster bubbles. Markers draw at their natural
  size but are wrapped in a transparent hit box at least 44×44 px — centered
  (`withHitArea`) or bottom-anchored so a pin's tip stays on its coordinate
  (`withBottomAnchoredHitArea`) — in `components/map/markerKit.ts`, so the
  drawn marker keeps its shape while the tappable area meets the goal. The
  authored controls and cluster bubbles were measured in a headless Chromium
  pass at 320/360/390 px; the marker hit boxes (stop tag, route pin,
  user-location) are verified by unit tests asserting `iconSize`/`iconAnchor`
  (`BusMap.test.tsx`), not yet measured in a browser. Leaflet's own attribution
  is inline text (exempt under 2.5.5).
- **Map markers are keyboard-operable where they do something.** Cluster
  bubbles are focusable buttons that zoom in on Enter or Space, and route
  origin/destination/transfer pins and the "your location" marker open their
  popup the same way (`components/map/markerA11y.ts`, applied in the map
  layer hooks). Markers that are purely informational — individual stops
  while map-picking is off, the sequenced stops of a browsed route — are
  removed from the tab order (`tabindex="-1"`, no button role) instead of
  being announced as controls that do nothing. This is what stops the
  all-stops layer from exposing hundreds of tab stops that cannot be
  activated. Because Leaflet builds a marker's icon element only once it is
  on a map (`onAdd`) and rebuilds it on `setIcon` and on remove/re-add (the
  route legend toggles layers exactly this way), the affordance is registered
  against the marker's `add` event rather than bound once at construction, so
  it survives a legend off/on toggle (`bindMarkerLifecycle` in
  `components/map/markerA11y.ts`; covered by `BusMap.test.tsx`). The separate
  hover icon swap is re-decorated by the layer hook itself (`decorateStop()` in
  `components/map/useAllStopsLayer.ts`), not by `bindMarkerLifecycle`. Cluster
  keyboard operability was checked in a headless Chromium pass at 320/360/390
  px; the route-pin and user-location keyboard activation are verified by unit
  tests (`BusMap.test.tsx`), not yet measured in a browser.
- **Asynchronous route-search outcomes are announced.** The loading, found,
  no-route and error states each render inside an `aria-live` region (the
  loading and found panels use `aria-live="polite"`; the error uses
  `role="alert"`; the "No bus route found" panel uses `role="status"`), so a
  screen-reader user hears the result of a search without a page change.
- **Reflow at 320 px and at 200% text.** The page lays out without horizontal
  scrolling down to 320 CSS px wide (checked at 320/360/390 px, including with
  the traffic and via-stop panels expanded). It also holds at 200% browser text
  scaling: a headless Chromium probe measured no horizontal overflow in the
  sidebar at 320/360/390 px and at a 1280 px desktop. The header collapses to a
  single row and its wordmark drops to the "K" mark below the `sm` breakpoint.

## Known gaps

Ordered roughly by how much they cost a user.

### 1. The map is only partly keyboard-operable

Marker *activation* is now reachable by keyboard: cluster bubbles respond to
Enter/Space by zooming in, and the origin/destination/transfer and
user-location pins open their popups, with the app's `:focus-visible` ring
showing focus. Leaflet's default of making every marker a focusable button
with no key handler — which made the all-stops layer hundreds of unactivatable
tab stops — is disabled (`keyboard: false`) and replaced by explicit,
per-marker affordances.

The map as a whole is still not keyboard-navigable, though. There is no
keyboard panning, no way to move focus from one marker to the next with arrow
keys, and individual stop markers only enter the tab order while map-picking
is armed. Leaflet's polyline layers remain non-interactive for keyboard users,
so a route cannot be traced through the map itself.

The fix is not a Leaflet setting; it needs a non-map equivalent (a list of
stops/routes that can be tabbed through and expanded) alongside the map. The
route results, stop details and fare information are already rendered as text
in the surrounding panel, so the information is reachable — the exploration
experience is what remains weak.

### 2. The congestion legend is in the panel, not on the map

The gradient key (a bar labelled Low / Moderate / Heavy) sits in the Traffic
panel (`components/CongestionPanel.tsx`), not on the map itself. On desktop the
panel and map sit side by side, so the key is co-visible with the overlay. On a
phone the two share the screen through a bottom sheet: collapsing the sheet to
see the whole map also hides the key. The per-segment popups still pair colour
with a text label and a numeric ratio, so nothing is unreadable — but the
at-a-glance key is not always visible next to the overlay.

### 3. Automated runtime testing covers structure, not appearance

`axe-core` runs through `vitest-axe`, registered in `vitest.setup.ts` and run
by `npm test`. It currently checks the rendered DOM of four files:

- `NavBar.a11y.test.tsx` — the default expanded nav bar.
- `RouteResultPanel.a11y.test.tsx` — the "found" result state.
- `SearchForm.a11y.test.tsx` — the empty form, rendering the real
  `StopAutocomplete` (not a stub).
- `StopAutocomplete.a11y.test.tsx` — the collapsed combobox, the expanded
  suggestion listbox, and the no-match message.

That catches structural and ARIA problems (roles, accessible names,
parent/child relationships, required attributes) in those states. It does
**not** establish:

- **Colour contrast** — jsdom does not render styles, so axe's contrast rule
  cannot be trusted there; contrast is still measured by hand (gap 5).
- **Layout, overflow, reflow or target sizes** — there is no layout engine in
  jsdom.
- **Keyboard or screen-reader behaviour** — axe reports markup, not what a
  browser or assistive technology does with it.
- **States other than those listed** — the other result states (loading,
  error, no-route), the minimized nav bar, the via-stop and validation states
  of the form, the congestion panel, the map, and the `/admin` UI have no axe
  pass. `eslint-plugin-jsx-a11y` still catches static errors across all of
  them, and there is no axe run against a built page in CI.

### 4. `control-has-associated-label` is not enabled, deliberately

Its inverse is enabled, but this rule is not, because it produces 26 false
positives on this codebase. Every one is the pattern

```tsx
<label>
  <span>Username</span>
  <input />
</label>
```

which is correct markup. A minimal probe confirmed the rule fires on the
wrapping-label-with-nested-span form while staying silent on `aria-label` and
on a named `<button>`, so it is failing to read the `<span>`'s text rather than
finding a real gap. Enabling it would add 26 errors of noise that train
everyone to ignore the a11y lint, which is worse than omitting it. Revisit if
the plugin fixes the behaviour.

### 5. Not checked

- **Colour contrast ratios.** Partially measured now, and partly fixed. The
  text palette was measured against WCAG 1.4.3 (4.5:1 for normal text) and
  1.4.11 (3:1 for non-text indicators); four failures were found and fixed:
  the focus ring was `#E0A614` (2.18:1 on white, now `brand.dark` `#B9860B`
  at 3.24:1), `ink.tertiary` was `#8D89A6` (3.35:1, now `#767194` at
  4.61:1), and the green/yellow/red **status text** tokens failed on the
  tinted backgrounds they sit on — as much as 2.66:1 — and now have
  text-only variants in `accent.{green,yellow,red}-text` that clear 4.5:1
  on both the `/5` and `/10` tints.

  **What is still not measured:** the five-stop congestion ramp
  (`lib/congestionColor.ts`) has not been checked at all. Adjacent
  green/yellow/orange/red stops are not required to contrast with each
  other, but the extremes are used as text/background in places and should
  be measured. There is also no automated check — nothing in CI catches a
  future palette regression — and the two remaining `#16A34A` links on
  `/routes/[routeId]` ("← Back to all routes", on white) still sit at
  3.30:1, as does the `hover:text-accent-green` on the breadcrumb link.
- **Live regions** for the *other* asynchronous updates. The route-search
  outcomes (loading / found / no-route / error) are now live regions and
  covered by tests (`RouteResultPanel.test.tsx`); the geolocation and
  congestion fetches resolve asynchronously and whether those are announced
  politely is still unverified.
- **The mobile bottom sheet** (`hooks/useSheet.ts`) — drag, focus trapping and
  Escape behaviour for a dialog-like surface.
- **Screen reader smoke tests** on the main flows: search, select a route,
  walk to a stop, and the `/admin` UI. No spoken pass is on record for any of
  them, so the marker keyboard work above is verified structurally and with a
  headless browser, not by hearing it read.

## How to check

1. **Static:** `cd frontend && npm run lint` — the `jsx-a11y` rules above.
2. **Automated runtime:** `cd frontend && npm test` runs `axe-core` through
   `vitest-axe` over the components and states listed in gap 3. Add a
   `*.a11y.test.tsx` next to a component to extend it. There is still no axe
   pass against a *built* page in CI.
3. **Keyboard only:** unplug the mouse. Tab from page load — the skip link
   should be first. Reach every control that responds to a click. Confirm the
   focus ring is visible on each. On the map, cluster bubbles and popup pins
   should activate on Enter/Space; individual stops are intentionally not in
  the tab order unless map-picking is armed. Keyboard panning and marker-to-
   marker navigation are still missing; that is gap 1.
4. **Screen reader:** VoiceOver (macOS) or NVDA (Windows). Verify the
   autocomplete announces its expanded state and highlighted option, and that
   the results panel updates are announced.
5. **Contrast:** any colour-contrast checker against the rendered page, at
   both themes, plus the congestion ramp specifically.
6. **Zoom/reflow:** 200% browser text scaling and 320px viewport width. Check
   that the sidebar does not scroll horizontally at 320 px + 200%. The one
   control seen clipping there — the Traffic panel's On/Off pill — was fixed
   in `components/ui/LayerToggle.tsx`: the label column shrinks (`min-w-0`)
   while the pill keeps its 44 px target (`shrink-0`). `LayerToggle.test.tsx`
   pins those classes (jsdom cannot measure layout).

## If you fix one thing

Add a keyboard-navigable list alongside the map. It addresses the largest
gap, and the data is already structured for it — stops and routes are fetched
as JSON and the results panel already renders them as text.
