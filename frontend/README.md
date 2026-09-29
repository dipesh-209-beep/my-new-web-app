# Frontend (Next.js + TypeScript + Leaflet)

Search for a stop-to-stop bus route across the Kathmandu Valley and see it
drawn on an interactive map, with live data from the FastAPI backend.

## Tech

Next.js 16 (App Router), React 18, TypeScript, Tailwind CSS, Leaflet.js via
`react-leaflet`.

## Running locally

```bash
cd frontend
npm install
npm run dev
```

Visit http://localhost:3000. By default the app talks to the backend at
`http://localhost:8000` — override this with an env var if your backend runs
elsewhere:

```bash
# frontend/.env.local
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Production build

```bash
npm run build
npm run start
```

### Tests

Vitest + React Testing Library, jsdom environment. Covers `lib/` (pure
helpers and the `fetch` wrapper in `lib/api.ts`, with `fetch` mocked) and
`hooks/` (via `renderHook`, with `lib/api` mocked).

```bash
npm test          # single run, used in CI
npm run test:watch
```

`npm run lint` also runs the `jsx-a11y` accessibility rules as errors — see
[Accessibility](#accessibility).

## What's here

```
app/            layout, global styles, and page.tsx (composes the hooks
                  and components below into the actual search UI)
                    - routes/page.tsx, routes/[routeId]/page.tsx --
                      route list/browse and single-route detail pages
                    - stops/page.tsx, stops/[stopId]/page.tsx -- stop
                      list/browse and single-stop detail pages
                    - manifest.ts, icon.tsx, apple-icon.tsx -- PWA web
                      app manifest + generated icons (see PWA below)
                    - offline/page.tsx -- fallback shown for an
                      uncached navigation while offline
components/
  route/          route-result rendering once a search succeeds
                    - RouteResultPanel.tsx -- top-level result view:
                      primary result + up to 2 alternatives
                      (alternate_direct_route / shortest_distance /
                      fastest_estimated), fare display, selection state
                      shared with the map (see app/page.tsx)
                    - RouteTimeline.tsx -- leg-by-leg breakdown (ride vs.
                      walking transfer), colored per LEG_COLORS
  search/          origin/destination input UI
                    - SearchForm.tsx -- inputs, swap button, "Use my
                      location" button (backed by /stops/nearby)
                    - StopAutocomplete.tsx -- the <datalist>-driven
                      autocomplete dropdown
  layout/
    NavBar.tsx    top nav, mounted in app/layout.tsx
  BusMap.tsx      imperative Leaflet map: colored polylines per route leg
                    (dashed for walking transfers), distinct origin/
                    destination/transfer markers, road-following geometry
                    from OSRM when available, the historical congestion
                    overlay, and a legend
  CongestionPanel.tsx   toggle + day-of-week/hour-bucket pickers for the
                          congestion overlay (defaults to "now" in Nepal
                          time), calls /congestion
  RoutesPanel.tsx  route list/browse view, separate from a single
                    origin->destination search result
  ServiceWorkerRegistration.tsx  registers public/sw.js on mount; see
                    PWA below
hooks/          one hook per concern, holding the state + fetch logic
                  that used to live inline in page.tsx
                    - useStops.ts -- loads and paginates the full stop
                      list for autocomplete
                    - useGeolocation.ts -- "Use my location" flow:
                      browser geolocation -> nearest stop via
                      /stops/nearby -> labeled via lib/stopLabel.ts
                    - useRouteSearch.ts -- the /route-finder search flow
                      (avoid_congestion, include_alternatives)
                    - useRouteBrowser.ts -- the routes-list browsing flow
                    - useCongestion.ts -- /congestion fetching for the
                      overlay
lib/
  api.ts          central fetch client -- all backend calls go through
                    here rather than each component calling fetch()
                    directly
  constants.ts    shared values that must stay identical across
                    components, e.g. LEG_COLORS (BusMap.tsx and
                    RouteTimeline.tsx both import from here rather than
                    each defining their own copy)
  routeDistance.ts  shared distance-formatting helpers used by
                    RouteResultPanel/RouteTimeline
  stopLabel.ts    stop_name isn't guaranteed unique across the valley
                    (e.g. duplicate "Chowk"/"Bus Park" names); this
                    builds a disambiguated label (name + district/
                    stop_id when a name collides) used consistently by
                    SearchForm's matching, StopAutocomplete's dropdown,
                    and useGeolocation's auto-fill -- so a stop picked
                    any of those three ways always resolves back to the
                    same physical stop.
types/route.ts  mirrors backend/app/schemas.py. Keep in sync if the
                  backend's response shapes change.
```

## PWA

`app/manifest.ts` generates the web app manifest (installable, standalone
display), `app/icon.tsx`/`app/apple-icon.tsx` generate the icons it
references, and `ServiceWorkerRegistration.tsx` registers
`public/sw.js` on mount (fails silently if unsupported -- progressive
enhancement, not a requirement). The service worker cache-first-serves
the app shell and stale-while-revalidates `/stops`, `/routes`, and
`/congestion`; live/OSRM-dependent calls (`/route-finder`,
`/walking-route`, `/routes/{id}/geometry`) are deliberately left
network-only so a stale cached response can't show a wrong route. See
`public/sw.js` for the full rationale, and `app/offline/page.tsx` for
what an uncached navigation shows while offline.

## Accessibility

`npm run lint` enforces `jsx-a11y` rules **as errors**, not warnings — the
autocomplete is a correct ARIA combobox, form controls are labelled with real
markup, decorative icons are `aria-hidden`, there is a skip link as the first
tab stop, and `prefers-reduced-motion` is honoured.

This is **not** a WCAG conformance claim and no level is claimed. The known
gaps — chiefly that Leaflet is not keyboard-operable, and that the congestion
gradient has no legend — plus the manual checks still to do, are documented
in [`ACCESSIBILITY.md`](ACCESSIBILITY.md). Read it before adding UI.

## Notes

- The map only renders client-side (`next/dynamic` with `ssr: false`), since
  Leaflet touches `window`.
- `SearchForm` resolves typed stop names to `stop_id`s via the loaded stop
  list — the `<datalist>` autocomplete is a soft suggestion, so submitting a
  name that doesn't match one of the suggestions surfaces an inline error
  rather than hitting the API with garbage.
- Backend "no route found" is a 404, so the frontend adds a client-side
  `found: boolean` to distinguish "no route" from a real error.
- "Use my location" uses the browser Geolocation API, then calls
  `/stops/nearby` to offer nearby stops and `/walking-route` to draw the
  walking path to the selected one.
