# Accessibility

**Last reviewed: 2026-09-29**

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
- **Landmarks**: `nav` in `NavBar.tsx`, `main` in `app/page.tsx`.
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
  warnings. The codebase currently passes all of them.

## Known gaps

Ordered roughly by how much they cost a user.

### 1. The map is not keyboard-operable

Leaflet is not keyboard-navigable by default: markers, popups, and the
polyline layers are canvas/DOM elements with no tab stops, no focus order, and
no key handling. A keyboard or screen-reader user cannot pan the map, select a
stop, or read a route's stops through it.

This is the largest accessibility gap in the app, and the app *is* a map. The
route results, stop details and fare information are all rendered in the
surrounding panel as text, so the information is reachable — but the
exploration experience is not.

The fix is not a Leaflet setting; it needs a non-map equivalent (a list of
stops/routes that can be tabbed through and expanded) alongside the map.

### 2. No gradient legend for the congestion overlay

Congestion is coloured on the map with no on-screen key explaining the
gradient. A user who cannot distinguish the colours, or who is looking at a
greyscale screenshot, has no way to interpret the overlay as a whole — they
would have to open every segment popup individually. The popups carry the
information; nothing summarises it.

### 3. No automated runtime testing

`eslint-plugin-jsx-a11y` catches a class of static errors. It cannot tell you
whether a label is *meaningful*, whether focus order makes sense, whether
contrast passes, or whether a live region announces a route change. There is
no `axe-core`, no `jest-axe`/Vitest equivalent, and no manual
screen-reader pass on record.

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
- **Live regions** for asynchronous updates. Route search, geolocation and
  congestion fetches all resolve asynchronously; whether results are announced
  politely is unverified.
- **The mobile bottom sheet** (`hooks/useSheet.ts`) — drag, focus trapping and
  Escape behaviour for a dialog-like surface.
- **Zoom and text scaling** at 200%, and reflow at 320px wide.
- **Touch target sizes** in the map controls and stop cluster bubbles.
- **Screen reader smoke tests** on the main flows: search, select a route,
  walk to a stop, and the `/admin` UI.

## How to check

1. **Static:** `cd frontend && npm run lint` — the `jsx-a11y` rules above.
2. **Automated runtime:** add `axe-core` to the Vitest setup for the existing
   component tests, and run axe against a built page in CI. Neither exists
   yet; the component tests in `components/`, `hooks/` and `lib/` are the
   natural place.
3. **Keyboard only:** unplug the mouse. Tab from page load — the skip link
   should be first. Reach every control that responds to a click. Confirm the
   focus ring is visible on each. The map will fail this; that is gap 1.
4. **Screen reader:** VoiceOver (macOS) or NVDA (Windows). Verify the
   autocomplete announces its expanded state and highlighted option, and that
   the results panel updates are announced.
5. **Contrast:** any colour-contrast checker against the rendered page, at
   both themes, plus the congestion ramp specifically.
6. **Zoom/reflow:** 200% browser zoom, and 320px viewport width.

## If you fix one thing

Add a keyboard-navigable list alongside the map. It addresses the largest
gap, and the data is already structured for it — stops and routes are fetched
as JSON and the results panel already renders them as text.
