# Accessibility

**Last reviewed: 2026-10-16**

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
- **The map-layer toggles are named for the layer they control**
  (`components/ui/LayerToggle.tsx`). The pill's only visible text is
  "On"/"Off" — a state, not a name — so the control is labelled with
  `aria-labelledby` pointing at the visible layer title, and `aria-pressed`
  carries the state: "Traffic, toggle button, pressed", never an unlabelled
  "On".
- **The sign-in control is a disclosure**
  (`components/layout/NavBar.tsx`). `aria-expanded` tracks the popup,
  `aria-controls` points at it only while open, Escape closes it and returns
  focus to the trigger, and an outside click closes it. It deliberately does
  **not** claim `aria-haspopup`, because the popup holds a login form or a
  sign-out action rather than a menu or a dialog.
- **Decorative icons are `aria-hidden`** (`components/icons/TransitIcons.tsx`),
  so they are not announced as unlabelled graphics.
- **A single, consistent focus indicator** (`:focus-visible`, 2px solid
  `#B9860B` with 2px offset) rather than relying on per-browser defaults that
  are easy to lose against a light theme.
- **Landmarks**: `nav` in `NavBar.tsx`, and one `main#main-content` in
  `app/layout.tsx` that every page renders into (so `/stops` and `/routes`
  share it). Two named `role="region"` landmarks exist as focus targets: the
  route results (`components/route/RouteResultPanel.tsx`, "Route results") and
  the routes list (`app/routes/page.tsx`, "Routes").
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
  recorded-estimate count — so a user who cannot distinguish the gradient
  colours still gets the information.
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
  alternative pills, the bottom-sheet position selector, the map's zoom and
  recenter
  controls, and the all-stops cluster bubbles. Markers draw at their natural
  size but are wrapped in a transparent hit box at least 44×44 px — centered
  (`withHitArea`) or bottom-anchored so a pin's tip stays on its coordinate
  (`withBottomAnchoredHitArea`) — in `components/map/markerKit.ts`, so the
  drawn marker keeps its shape while the tappable area meets the goal. The
  authored controls and cluster bubbles were measured in a headless Chromium
  pass at 320/360/390 px (a one-off manual run; no reproducible probe script is
  checked in); the marker hit boxes (stop tag, route pin, user-location) are
  verified by unit tests asserting `iconSize`/`iconAnchor`
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
  px (a one-off manual run; no reproducible probe script is checked in); the
  route-pin and user-location keyboard activation are verified by unit
  tests (`BusMap.test.tsx`), not yet measured in a browser.
- **Asynchronous route-search outcomes are announced.** The loading state and
  the "No bus route found" state use `role="status"` (polite); the error uses
  `role="alert"`. The found state deliberately does **not** wrap the whole
  results card in a live region — doing so replayed the entire card
  (alternatives, timeline, fare notes) on every re-render, including simply
  picking a different alternative. It instead exposes one concise
  screen-reader-only `role="status"` summary ("Recommended. Estimated 12 min,
  1 transfer.") that also updates when a different alternative is selected. The
  found summary is written into the same status region the loading card already
  rendered — the two cards put it in the same position, so React reuses that
  node across the transition — so the result is spoken as an update to a region
  that already existed rather than one that appears together with its text. A
  screen-reader user hears the result of a search without a page change, and
  without the whole card being re-read.
- **Route- and stop-list updates are announced.** `/routes` and `/stops` each
  render one screen-reader-only `role="status"` region. It reports the settled
  match count (with `, N shown` while `/routes` still has pages to load), the
  "Load more" progress, or the no-match message — so filtering and pagination
  no longer change the visible list silently. `/stops` filters client-side and
  instantly, so its announcement is debounced by 300 ms (the interval the
  `/routes` server-side search already uses) to avoid a new count on every
  keystroke; an empty query announces nothing, and neither does clearing it.
  The region is always present but kept empty until a change is worth
  announcing, because a live region rendered at the same moment as its text is
  often not spoken. This is a markup/behaviour assertion in
  `app/routes/page.test.tsx` and `app/stops/page.test.tsx`, not a spoken pass.
- **Controls that disappear or become busy do not drop keyboard focus.** A
  control removed from the DOM takes focus with it, so the three that unmount on
  activation move focus to a survivor first: the via-stop remove button
  (`SearchForm.tsx`) to the always-present "+ Add a stop along the way", the
  error card's Retry button (`RouteResultPanel.tsx`) to the panel root, which is
  the same node in every state so focus also survives the error → loading →
  found transition, and the congestion panel's "Reset to now"
  (`CongestionPanel.tsx`) to the time select that stays mounted beside it.
  Controls that are only *temporarily* unavailable keep focus instead of being
  natively `disabled`: "Find route", "Use my location",
  "+ Add a stop" (at the 3-stop limit) and `/routes` "Load more" use
  `aria-disabled` and guard their own action, and when the final page arrives
  the `Load more` button hands focus to the routes list rather than letting it
  fall to `<body>`. When a `Load more` page finishes with more pages remaining,
  the button stays mounted and focus stays on it. The focus moves and the
  "an aria-disabled control does nothing" behaviour are asserted in
  `components/search/SearchForm.test.tsx`,
  `components/route/RouteResultPanel.test.tsx`, `app/routes/page.test.tsx`,
  `hooks/useRouteBrowser.test.ts` and `components/CongestionPanel.test.tsx`.
  This is a DOM assertion — jsdom drops focus to `<body>` on removal but does
  not blur a disabled element, so the disabled-vs-focus interaction itself is
  a browser behaviour, not a measured one here.
- **Reflow at 320 px and at 200% text.** The page lays out without horizontal
  scrolling down to 320 CSS px wide (checked at 320/360/390 px, including with
  the traffic and via-stop panels expanded). It also holds at 200% browser text
  scaling: a headless Chromium probe measured no horizontal overflow in the
  sidebar at 320/360/390 px and at a 1280 px desktop (a one-off manual run; no
  reproducible probe script is checked in). The wordmark drops to the
  "K" mark below the `sm` breakpoint, and the sign-in popup is capped to the
  viewport width (`max-w-[calc(100vw-1rem)]`, with `max-sm:ml-auto` on its
  actions group) so the login form stays on-screen even when the narrow header
  wraps the actions onto a second row.

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

The map container itself is keyboard-focusable and Leaflet's default keyboard
handling is left enabled, so while it has focus the arrow keys pan and `+`/`-`
zoom the map. What is still missing is marker-to-marker movement: there is no
way to step focus from one marker to the next with the keyboard (the arrow keys
pan the map instead), individual stop markers enter the tab order only while
map-picking is armed, and Leaflet's polyline layers are not focusable, so a
route cannot be traced through the map by keyboard.

Panning and zooming are already provided by Leaflet; what still needs building
is a non-map equivalent — a list of stops/routes that can be tabbed through and
expanded — alongside the map. The route results, stop details and fare
information are already rendered as text in the surrounding panel, so the
information is reachable — the exploration experience is what remains weak.

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
by `npm test`. It currently checks the rendered DOM of five files:

- `NavBar.a11y.test.tsx` — the default expanded nav bar, and the sign-in popup
  open.
- `RouteResultPanel.a11y.test.tsx` — the found, loading, no-route and error
  result states.
- `SearchForm.a11y.test.tsx` — the empty form, rendering the real
  `StopAutocomplete` (not a stub).
- `StopAutocomplete.a11y.test.tsx` — the collapsed combobox, the expanded
  suggestion listbox, and the no-match message.
- `StopsLoadError.a11y.test.tsx` — the stop-load-failure notice with and
  without a stale stop list.

That catches structural and ARIA problems (roles, accessible names,
parent/child relationships, required attributes) in those states. It does
**not** establish:

- **Colour contrast** — jsdom does not render styles, so axe's contrast rule
  cannot be trusted there; contrast is still measured by hand (gap 5).
- **Layout, overflow, reflow or target sizes** — there is no layout engine in
  jsdom.
- **Keyboard or screen-reader behaviour** — axe reports markup, not what a
  browser or assistive technology does with it.
- **States other than those listed** — the minimized nav bar, the via-stop and
  validation states of the form, the congestion panel, the map, and the
  `/admin` UI have no axe pass. `eslint-plugin-jsx-a11y` still catches static
  errors across all of them, and there is no axe run against a built page in
  CI.

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
- **Live regions** for the *other* asynchronous updates. All four route-search
  outcomes (loading / found / no-route / error) are live regions in the markup;
  role, politeness and one-region-per-state are asserted in
  `RouteResultPanel.test.tsx`, with an axe pass on each state in
  `RouteResultPanel.a11y.test.tsx`. These are markup assertions, not a spoken
  pass — jsdom has no assistive technology, so what a screen reader actually
  announces is still unverified. A successful "Use my location" is announced
  through a polite `role="status"` region in `SearchForm.tsx` (the nearest
  stop's name only — `/stops/nearby` returns no distance and the walking
  distance is optional, so no distance is claimed); its transitions and
  message contents are asserted in `hooks/useGeolocation.test.ts` and
  `components/search/SearchForm.test.tsx`. The congestion overlay is exposed
  through a single persistent polite `role="status"` region in
  `components/CongestionPanel.tsx` — not per segment and not the whole
  overlay — whose message tracks the loading, populated, empty, fetch-failure
  and refresh-failure states and is asserted in
  `components/CongestionPanel.test.tsx`. That message is derived from the
  settled result rather than the loading flag, so the three-minute background
  refresh does not re-announce a transient "Loading…" while earlier results
  are still shown. As with the regions above, this is a markup/DOM assertion:
  what a screen reader actually announces, and how often, is not
  independently verified in a real browser.
- **The mobile bottom sheet** (`hooks/useSheet.ts`) — the pointer drag itself,
  plus focus trapping and Escape behaviour for a dialog-like surface. Its three
  snap positions are now keyboard-selectable through a native radio group
  (`components/ui/SheetSnapControl.tsx`, mobile only), so a keyboard user can
  reach `half` without dragging; the drag handle stays a pointer-only gesture.
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
   should activate on Enter/Space, and the map container itself is focusable:
   with it focused, the arrow keys pan and `+`/`-` zoom, with the focus ring
   visible. Individual stops are intentionally not in the tab order unless
   map-picking is armed, and there is no keyboard move from marker to marker
   (the arrow keys pan instead); that is gap 1.
4. **Screen reader:** VoiceOver (macOS) or NVDA (Windows). Verify the
   autocomplete announces its expanded state and highlighted option, and that
   the results panel updates are announced.
5. **Contrast:** any colour-contrast checker against the rendered page, at
   both themes, plus the congestion ramp specifically.
6. **Zoom/reflow:** 200% browser text scaling and 320px viewport width. Check
   that the sidebar does not scroll horizontally at 320 px + 200%, and that the
   open sign-in popup is fully visible at 320 px (its form is capped to the
   viewport and its actions group is kept flush right when the header wraps)
   while at 1280 px it is unchanged. The one control seen clipping there — the
   Traffic panel's On/Off pill — was fixed in `components/ui/LayerToggle.tsx`:
   the label column shrinks (`min-w-0`) while the pill keeps its 44 px target
   (`shrink-0`). `LayerToggle.test.tsx` pins those classes and
   `NavBar.test.tsx` pins the popup's width cap and group alignment (jsdom
   cannot measure layout).

## If you fix one thing

Add a keyboard-navigable list alongside the map. It addresses the largest
gap, and the data is already structured for it — stops and routes are fetched
as JSON and the results panel already renders them as text.
