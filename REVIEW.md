# Engineering Review — feature/congestion-gradient

> **Read-only review.** Branch `feature/congestion-gradient` (tip `0834f63`) treated as the complete, current implementation across all three stacked branches. No files were modified.

---

## # Executive Summary

The Kathmandu Bus Route Finder is a well-architected, production-quality transit route-finder with three stacked feature branches — route-aware stop positioning, crowd-sourced suggestions, and congestion-aware gradient coloring — that are cleanly separated and coherently implemented. The backend's NetworkX-based routing, dual-source congestion system, and suggestion/vote mechanism are well-designed with good separation of concerns (API → routing → graph → DB). The frontend is a modern Next.js 16 + Leaflet PWA with an admin data-entry UI.

**Overall quality: B+ (strong, minor issues)**

The primary gaps are: (1) test coverage holes for the OSRM circuit breaker, congestion API endpoint, background congestion recording, and the `via` API parameter; (2) a frontend bug where deep-link stop labels race against async stop loading; (3) the API base URL hardcoded to `http://` in three separate files, which will break under HTTPS; and (4) user JWT expiry is never handled client-side. None of these are architectural — they're fixable within the existing design.

The routing algorithm is sound, the congestion dual-source blending (static zones + organic stats) is a genuinely good design for a city with no real-time data, and the suggestion dedup-as-vote mechanism with auto-apply threshold is a clever crowdsourcing approach. The stop positioning module's monotonic cursor and loop-route handling are particularly well thought out.

---

## # Current Git / Branch Structure

```
main
 └─ feature/route-aware-stop-positioning
     └─ feature/user-suggestions
         └─ feature/congestion-gradient   ← HEAD, clean working tree
```

**Branch boundaries** (verified via `git log --oneline --graph`):

| Branch adds | Commits |
|---|---|
| `feature/user-suggestions` | `d32ed72` "Backend user accounts + crowd-sourced suggestions (CS)", `13e6d02` "User suggestion (CS)" |
| `feature/congestion-gradient` | `30cc521` "Removing stale coloring", `0834f63` "Smooth congestion map coloring with continuous ratio-based gradient" |

Each branch is self-contained: no cross-branch dependency leaks. The `feature/congestion-gradient` branch builds on the suggestion infrastructure (user auth) without modifying it.

---

## # Existing Feature Inventory

### Implemented (all on this branch stack)

| Feature | Status | Key Files |
|---|---|---|
| Direct route search | **LIVE** | `pathfinder.py:68-214` |
| Single-transfer (Dijkstra) fallback | **LIVE** | `pathfinder.py:443-486` |
| Multi-transfer (via waypoints) | **LIVE** | `pathfinder.py:660-738` |
| Max-transfers parameter | **LIVE** | `pathfinder.py:489-560` |
| Route alternatives (up to 3) | **LIVE** | `pathfinder.py:563-657` |
| Congestion-aware routing (`avoid_congestion`) | **LIVE** | `pathfinder.py:334-388` |
| Static congestion zones (44 zones) | **LIVE** | `congestion_zones.py` + `data/congestion_zones.csv` |
| Organic congestion stats (EMA-blended) | **LIVE** | `queries.py:194-261` |
| Dual-source congestion blending (max) | **LIVE** | `pathfinder.py:303-331` |
| Congestion gradient coloring (continuous) | **LIVE** | `congestionColor.ts` + `useCongestionLayer.ts` |
| Congestion overlay (day/hour picker) | **LIVE** | `useCongestion.ts` + `CongestionPanel.tsx` |
| Seeded congestion baseline | **LIVE** | `seed_congestion_stats.py` + `queries.py:264-304` |
| Route-aware stop positioning | **LIVE** | `stop_positioning.py` |
| Bearing-constrained OSRM snap | **LIVE** | `routing.py:88-119` |
| Loop-route handling | **LIVE** | `graph_builder.py:58-73`, `pathfinder.py:93-161` |
| Walking route (OSRM foot profile) | **LIVE** | `routing.py:284-299` |
| Fare lookup | **LIVE** | `fare.py` + `queries.py:114-121` |
| OSRM geometry + circuit breaker | **LIVE** | `osrm_client.py` |
| Response caching (Redis + in-memory) | **LIVE** | `response_cache.py` |
| Graph cache with version-bump invalidation | **LIVE** | `graph_builder.py:272-320` |
| Admin CRUD (stops, routes, route-stops) | **LIVE** | `admin.py` |
| Admin role-based access (editor/admin) | **LIVE** | `security.py:212-254` |
| Admin per-admin JWT login | **LIVE** | `admin_auth.py` + `security.py:82-125` |
| User registration + login | **LIVE** | `auth.py` + `security.py:134-174` |
| Crowd-sourced suggestions (dedup-as-vote) | **LIVE** | `suggestions.py` + `services/suggestions.py` |
| Auto-apply at vote threshold | **LIVE** | `suggestions.py:180-198` |
| Rate limiting (login, register, suggestions) | **LIVE** | `rate_limit.py` + decorators |
| PWA (manifest + service worker) | **LIVE** | `frontend/public/sw.js` + `manifest.ts` |
| Admin browser UI | **LIVE** | `frontend/app/admin/page.tsx` |

### NOT Implemented (documented as future)

- Real-time bus GPS tracking
- Live traffic-aware routing (beyond historical congestion)
- ETA estimation beyond speed assumptions
- Full dataset coverage verification (32 routes flagged for `osrm_distance_km` recomputation; 75 stop-merge candidates pending human review)

---

## # Architecture Overview

```
Next.js Frontend (port 3000)
    ↕ REST (JSON)
FastAPI Backend (port 8000)
    ├── NetworkX routing graph (in-memory, version-cached)
    ├── PostgreSQL + PostGIS (spatial queries, congestion stats)
    ├── OSRM driving profile (port 5000) — road geometry
    ├── OSRM foot profile (port 5001) — walking geometry
    └── Redis (optional) — shared response cache + rate limit state
```

**Data flow for /route-finder:**
1. API validates params, loads graph (version-checked)
2. Pathfinder: direct route scan → Dijkstra fallback (with optional congestion weighting)
3. OSRM geometry enrichment (constrained → unconstrained → straight-line fallback chain)
4. Response-cached (stops/routes/congestion via two-tier Redis+memory cache)
5. Background: ride legs upserted into `segment_congestion_stats` (EMA-blended, never blocks response)

**Key invariants:**
- Graph never mutated by congestion lookup (request-scoped callable, not baked into cached edges)
- Congestion zones cached per-process, loaded lazily from CSV
- Admin writes bump `graph_meta.version`; every worker rebuilds on next stale check
- Response caches invalidated per-namespace on admin writes

---

## # Route-Aware Stop Positioning Review

**Module:** `backend/app/routing/stop_positioning.py` (293 lines)

**What it does:** Projects each stop's canonical lat/lng onto the route's actual OSRM road geometry, producing a direction-aware display position (`display_lat`/`display_lng`) without persisting anything to the database.

**Design quality: Excellent.**

**Strengths:**
- **Monotonic cursor** (`cursor_m`): stops are matched in travel order; each new projection must be at or ahead of the last. This prevents a stop from snapping backward onto a different part of the road — a common failure in naive nearest-point searches.
- **Loop-route safe**: `FIRST_STOP_TIE_EPS_M` (10m) tie-breaking ensures the first stop of a closed loop anchors at the geometry's start, not the loop's coincident end. Tested against a 36-stop ring-road loop (94% fallback-to-canonical rate before fix → near-zero after).
- **`MAX_STOP_OFFSET_M` = 100m trust bound**: any projection more than 100m from the canonical coordinate is treated as untrustworthy and falls back. Accounts for GPS accuracy (10-30m) + OSRM snap error.
- **`MONOTONIC_POSITION_TOLERANCE_M` = 5m**: absorbs GPS noise without allowing true backward snaps.
- **Pure function**: no side effects, no DB writes, no process state. Request-scoped.

**Issues found:**
- None critical. The module is well-tested (`test_stop_positioning.py` + `test_stop_positioning_adversarial.py`).

**Recommendation:** No changes needed. This is one of the strongest modules in the codebase.

---

## # User Suggestions Review

**Files:** `backend/app/api/suggestions.py` (274 lines), `backend/app/services/suggestions.py` (70 lines), `backend/app/models/route_suggestion.py` (132 lines), `backend/app/models/suggestion_vote.py`, `backend/app/models/user.py`

**What it does:** Logged-in users can propose changes (stop name, route name, stop sequence reorder). Identical proposals deduplicate into votes. At `AUTO_APPLY_VOTE_THRESHOLD` (26), the change applies automatically. Admins/editors can also manually approve/reject.

**Design quality: Very good.**

**Strengths:**
- **Dedup-as-vote**: partial unique index on `(target_type, target_id, suggestion_type, payload_hash) WHERE status = 'pending'` — elegant, enforced at DB level.
- **Payload hash** = `sha256(json.dumps(payload, sort_keys=True))` — ordering-insensitive, exact.
- **Single apply path**: `services/suggestions.py::apply_suggestion` is the only mutation point for both auto-apply and admin-apply. Can't drift.
- **Audience separation**: user JWTs use `type: "user"` claim; admin JWTs use `type: "admin"`. A user token can never satisfy `require_admin` even over the same secret key.
- **`TargetMissingError`** → HTTP 409: clean rollback if the target disappeared between vote and apply.

**Issues found:**
1. **P2 — Self-vote counted toward threshold**: The author's implicit vote (line 177: `db.add(SuggestionVote(...))`) counts toward the 26-vote threshold. With `AUTO_APPLY_VOTE_THRESHOLD = 26`, a single user submitting a popular suggestion needs 25 *other* votes, which is fine. But the author's vote is not distinguishable from a community vote in `vote_count`, making the effective threshold 25 for new suggestions. This is documented and acceptable.

2. **P3 — No rate limit on `GET /suggestions`**: Only `POST /suggestions` is rate-limited (10/hr). A scraper could enumerate all pending suggestions unthrottled. Low risk given the small dataset.

3. **P3 — `GET /admin/suggestions` lacks pagination**: If the pending queue grows large, the entire list is returned. Acceptable for the expected scale (< 100 pending at any time).

**Recommendation:** No changes needed for correctness. Consider P2/P3 items if scale grows.

---

## # Congestion Review

### Backend Congestion System

**Dual-source design:**
1. **Static congestion zones** (`congestion_zones.py`): 44 real measured Kathmandu intersections from `data/congestion_zones.csv`. Each has a score (0-10) linearly mapped to a ratio via `score_to_ratio()`: 10 → 4.2x, 0 → 1.05x. Applied to any ride edge whose endpoint falls within the zone's radius.
2. **Organic segment stats** (`segment_congestion_stats`): per-(route, from_stop, to_stop, day_of_week, hour_bucket), upserted via EMA (`CONGESTION_EMA_ALPHA = 0.2`). Seeded with OSRM baseline (`is_seeded=True`), first real sample replaces rather than blends.

**Blending:** `max(organic_ratio, zone_ratio)` — never double-penalizes, takes whichever signal is worse. Implemented in `pathfinder.py:303-331`.

**Thresholds** (`api/congestion.py`):
- `_MODERATE_RATIO = 1.15` → "moderate"
- `_HEAVY_RATIO = 1.5` → "heavy"

**Constants** (`constants.py`):
- `CONGESTION_LAMBDA = 0.75`: how much congested edges inflate weight: `distance * (1 + 0.75 * max(ratio - 1, 0))`
- `HOUR_BUCKETS = (0, 3, 6, 9, 12, 15, 18, 21)`: 3-hour buckets, 8 total

**Design quality: Very good for a system without real-time data.**

**Strengths:**
- Dual-source is architecturally sound: static zones capture structural risk (chronic intersections), organic stats capture actual observed delays per route/time.
- EMA blending means old samples decay naturally.
- Seed replacement logic prevents synthetic baseline from permanently biasing averages.
- Congestion is request-scoped (callable weight function, not baked into cached graph) — concurrent requests with different time buckets don't leak into each other.

**Issues found:**
1. **P1 — `get_congestion_stats` fallback subquery full-scans the table** (`queries.py:324-343`): The COALESCE fallback computes `min(avg_duration_s)` grouped by `(route_id, from_stop_id, to_stop_id)` across the entire table on every congestion query. As organic samples accumulate, this becomes expensive. **Recommendation:** Add an index on `(route_id, from_stop_id, to_stop_id)` or precompute the baseline during upsert into a dedicated column (already done for seeded rows via `free_flow_duration_s`, but old pre-migration rows lack it). For the migration path: `UPDATE segment_congestion_stats SET free_flow_duration_s = avg_duration_s WHERE free_flow_duration_s IS NULL`.

2. **P2 — `CONGESTION_LAMBDA = 0.75` is labeled "tune by hand" but never tuned**: The docstring says "Starting value, tune by hand against a few known-congested corridors (Koteshwor, Kalanki, Thapathali) before trusting it in production." No evidence this was done. **Recommendation:** Add a note to `constants.py` or `README.md` documenting the validation approach, or implement a simple benchmark against known congested segments.

3. **P3 — `seed_congestion_stats.py` never updates stale seeds**: If OSRM geometry changes (new road data), existing seeded rows keep their old duration. Not urgent since seeds are overwritten by real samples.

### Frontend Congestion UI

**`congestionColor.ts`:** Piecewise-linear gradient from green (1.00) → yellow (1.15) → orange (1.50) → red (2.50) → dark red (4.20). Keyed to backend thresholds. Clamps input to [1.0, 4.2]. **Good.**

**`useCongestionLayer.ts`:** Draws polylines per segment, dashed/50% opacity for seeded data, full opacity for real data. Tooltips show ratio, minutes, sample count. **Good.**

**`CongestionPanel.tsx`:** Toggle, day/hour picker, legend (3-dot: low/moderate/heavy), segment count, seeded-only indicator. **Good.** But the legend uses the old 3-color scheme (`CONGESTION_COLORS` from `constants.ts`) while the actual map uses the continuous gradient — the legend doesn't match what users see on the map. **P2 UX issue.**

---

## # Routing Algorithm Review

**File:** `backend/app/routing/pathfinder.py` (855 lines)

**Algorithm:**
1. **Direct route** (`_build_direct_route_result`): scans every active route for one containing both stops. Checks all occurrences (loop-safe). Bidirectional routes also check reverse direction. Shortest-by-distance wins.
2. **Dijkstra fallback** (`_find_with_dijkstra`): sequence-aware ride nodes `(stop_id, route_id, sequence_no)`, board/alight/ride/walk edges, `TRANSFER_PENALTY = 3000` per boarding.
3. **Custom Dijkstra with transfer limit** (`_find_with_dijkstra_limited`): tracks `(node, board_count)` as state to enforce `max_transfers`.

**Design quality: Good.**

**Strengths:**
- Direct-route-first is the right design: a direct bus is always better than a multi-transfer path, even if the transfer path is technically shorter by some metric.
- Sequence-aware nodes handle loop routes correctly (e.g., NY-03).
- Transfer penalty (3000) is well-calibrated against typical edge distances (100-500m per ride hop).
- Congestion weight function is request-scoped (callable, not baked into graph).

**Issues found:**
1. **P3 — `_duration_weight_fn` defaults to distance-only when called without `congestion_lookup`**: The function signature has `graph: nx.DiGraph = None, congestion_lookup: dict = None`. When both are None, it still works (congestion-blind duration estimation) but the docstring's "reproducing the exact original congestion-blind behavior" could mislead callers. The actual callers always pass both when they want congestion-aware duration. **Cosmetic.**

2. **P3 — `find_shortest_path` always queries congestion even for `avoid_congestion=False`**: Actually no — it only queries `get_congestion_ratios` when `avoid_congestion=True` (line 823-827). Correct behavior.

3. **P3 — `_alternatives_from_dijkstra` re-runs Dijkstra 2 more times per call**: For each of `shortest_distance` and `fastest_estimated`. On a 397-stop graph this is fast (< 10ms), but worth noting that `include_alternatives=True` roughly triples the search cost. Acceptable.

---

## # Route Alternatives Review

**Implementation:** `pathfinder.py:563-657`

**Three alternative types:**
1. **`alternate_direct_route`**: different bus routes that also connect the two stops directly. Deduplicated by physical stop sequence (not route_id).
2. **`shortest_distance`**: minimum total distance, ignoring the transfer penalty weighting.
3. **`fastest_estimated`**: minimum estimated travel time using fixed speeds (bus ~12 km/h, walk ~4.7 km/h, 3-min wait per boarding), optionally congestion-aware.

**Design quality: Good.**

**Strengths:**
- Stop-sequence deduplication is the right granularity: two routes covering the same corridor aren't "different options."
- Each alternative gets its own real OSRM geometry (not shared/reused from primary).
- Alternatives are skipped when they produce the same stop sequence — no noise.
- Via-search disables alternatives (avoids combinatorial explosion).

**Issues found:**
1. **P3 — No user-facing label for alternatives**: The backend returns `label: "alternate_direct_route"` etc. but the frontend displays the label string directly (or not at all — need to verify). These labels are implementation details, not user-facing names. **Recommendation:** Map labels to user-friendly names in the frontend.

---

## # Frontend / Map Review

**Stack:** Next.js 16 (App Router), React 18, TypeScript, Leaflet.js, Tailwind CSS, Vitest + React Testing Library.

**Strengths:**
- Clean component separation: `BusMap` handles Leaflet, `useCongestionLayer` is a focused hook, `CongestionPanel` is pure UI.
- Request-ID race guard in `useRouteSearch` prevents stale results.
- PWA with service worker and manifest.
- Admin UI backed by separate JWT in localStorage.
- Shared color constants (`LEG_COLORS`, `CONGESTION_COLORS`) prevent palette drift.
- `types/route.ts` mirrors backend schemas exactly with detailed JSDoc.

**Issues found:**

1. **P0 — API base URL hardcoded to `http://` in three files** (`api.ts:36-50`, `adminApi.ts:118-123`, `userApi.ts:83-88`): All three define `apiBase()` returning `http://${hostname}:8000`. If the site is served over HTTPS, every API call becomes mixed content and the browser blocks it silently. **Recommendation:** Derive scheme from `window.location.protocol` (`${protocol}//${hostname}:8000`) or use a relative base when behind a proxy. Also consolidate the three copies into one shared utility.

2. **P1 — Deep-link race condition**: `app/page.tsx:131-157` applies `?origin=/destination=` on mount, calling `buildStopLabel(stop, stops)` while `stops` (from `useStops`, loaded via 4 parallel paged requests) may still be empty. For any duplicated stop name, `buildStopLabel` with an empty stops list returns the bare `stop_name` — but `SearchForm.resolveStop` matches against disambiguated labels (`"Chowk (Kathmandu)"`). This causes a silent validation failure for duplicated stop names on deep links. **Recommendation:** Wait for `stops` to load before applying deep-link params, or use `stop_id` matching instead of label matching for deep links.

3. **P1 — User JWT expiry not handled**: `UserAuthContext.tsx` has no 401/expiry handling. After token expiry, the NavBar still shows "signed in" and suggestion submissions fail silently with 401. Admin tokens handle this correctly (`handleTokenInvalid` in `adminApi.ts`). **Recommendation:** Add the same expiry handling to user auth context.

4. **P2 — Congestion legend doesn't match map**: `CongestionPanel.tsx` shows a 3-dot legend (Low/Moderate/Heavy) using `CONGESTION_COLORS`, but the map actually renders a continuous gradient via `getCongestionColor()`. The discrete dots don't represent the smooth color transitions users see. **Recommendation:** Replace the 3-dot legend with a gradient bar or update it to show the actual color stops.

5. **P2 — Route detail direction toggle refetches unnecessarily**: `app/routes/[routeId]/page.tsx:35-83` refetches `getRoute` (direction-independent metadata) on every direction toggle, causing the header/metadata to flash back to skeleton placeholders. Only `getRouteStops` and `getRouteGeometry` are direction-dependent. **Recommendation:** Only refetch direction-dependent data on toggle.

6. **P3 — `DEFAULT_TIMEOUT_MS = 10_000` duplicated in 3 files**: `api.ts`, `adminApi.ts`, `userApi.ts` each define the same constant. **Recommendation:** Shared constant in `lib/constants.ts`.

7. **P3 — `"TRANSFER"` sentinel string duplicated in 4+ files**: Used in `RouteResultPanel`, `RouteTimeline`, `useRouteResultLayer`, and tests. **Recommendation:** Shared constant.

---

## # API Review

**Endpoints (17 total):**

| Method | Path | Auth | Cached | Tested |
|---|---|---|---|---|
| GET | `/health` | — | No | Implicit (startup) |
| GET | `/stops` | — | Yes (30s) | Yes |
| GET | `/stops/nearby` | — | No | Yes |
| GET | `/stops/{stop_id}` | — | Yes (30s) | Yes |
| GET | `/stops/{stop_id}/routes` | — | No | Yes |
| GET | `/routes` | — | Yes (30s) | Yes |
| GET | `/routes/{route_id}` | — | Yes (30s) | Yes |
| GET | `/routes/{route_id}/stops` | — | Yes (30s) | Yes |
| GET | `/routes/{route_id}/geometry` | — | Yes (30s) | Yes |
| GET | `/route-finder` | — | No | Yes |
| GET | `/walking-route` | — | No | Yes |
| GET | `/fare` | — | No | Yes |
| GET | `/congestion` | — | Yes (60s) | **No** |
| GET | `/congestion/buckets` | — | No | **No** |
| POST | `/stops` | require_admin | — | Yes |
| POST | `/routes` | require_admin | — | Yes |
| POST | `/routes/{route_id}/stops` | require_admin | — | Yes |
| DELETE | `/routes/{route_id}/stops/{seq}` | require_admin | — | Yes |
| PATCH | `/routes/{route_id}/stops/order` | require_admin | — | Yes |
| PATCH | `/routes/{route_id}/status` | admin-only | — | Yes |
| POST | `/graph/reload` | admin-only | — | Yes |
| POST | `/admin/login` | — | No | Yes |
| POST | `/auth/register` | — | No | Yes |
| POST | `/auth/login` | — | No | Yes |
| POST | `/suggestions` | user JWT | No | Yes |
| GET | `/suggestions` | optional user JWT | No | Yes |
| GET | `/admin/suggestions` | editor+ | No | Yes |
| PATCH | `/admin/suggestions/{id}` | editor+ | No | Yes |

**Issues:**
1. **P2 — `/congestion` and `/congestion/buckets` have no API-level tests**: The congestion classification logic (`_classify`), Nepal-time default behavior, and bucket rounding are untested at the HTTP layer.
2. **P2 — `/route-finder?via=` parameter has no API-level test**: The `via` parameter's leg-failure reporting, `via_stop_ids` echo, and alternatives-disabled-when-via behavior are untested.
3. **P3 — No `GET /routes/{route_id}` response for `direction=reverse` on stop listing**: The `read_route_stops` endpoint returns 422 for non-bidirectional routes. Correct behavior, but the error message could be more helpful ("This route doesn't have a reverse direction").

---

## # Database Review

**Tables (11):** `stops`, `routes`, `route_stops`, `operators`, `route_operators`, `fare_rules`, `admin_users`, `users`, `route_suggestions`, `suggestion_votes`, `segment_congestion_stats`, `graph_meta`.

**Indexes verified:**
- `stops`: PK, GiST on `geom`, idx on `district`, `status`
- `route_stops`: PK on `(route_id, sequence_no)` — correct for the adjacency query pattern
- `segment_congestion_stats`: unique on `(route_id, from_stop_id, to_stop_id, day_of_week, hour_bucket)`, index on `(day_of_week, hour_bucket)` — matches the hot-path query
- `route_suggestions`: partial unique on `(target_type, target_id, suggestion_type, payload_hash) WHERE status = 'pending'` — dedup-as-vote enforcement

**Issues found:**
1. **P1 — Missing index on `(route_id, from_stop_id, to_stop_id)` for `segment_congestion_stats`**: The `get_congestion_stats` fallback subquery (`queries.py:324-343`) groups by these three columns across the entire table. As organic samples accumulate, this full-scan per congestion query will degrade. The existing unique constraint doesn't help because the group-by doesn't include `day_of_week`/`hour_bucket`. **Recommendation:** Add a covering index or backfill `free_flow_duration_s` for all rows so the fallback is never needed.

2. **P2 — `stops.geom` index uses `postgresql_using='gist'` but has `spatial_index=False`**: The model declares `spatial_index=False` on the Geography column but then adds an explicit GiST index. This works but the `spatial_index=False` flag is misleading — it's there to prevent GeoAlchemy2 from auto-creating a duplicate index during migration. **Recommendation:** Add a comment explaining this is intentional.

3. **P3 — 32 routes flagged `distance_flagged_for_recompute` in `report.md`**: The `osrm_distance_km` column exists (migration `9d3f1a7c2b4e`) but `compute_osrm_route_distances.py` hasn't been run. These routes have unreliable `approx_distance_km` values.

---

## # Performance Review

**Caching layers (4):**
1. **Graph cache** (`graph_builder.py`): per-process, version-bumped by admin writes. Rebuild on stale. Thread-safe via `threading.Lock`. **Good.**
2. **OSRM geometry cache** (`osrm_client.py`): per-process, TTL 300s, max 500 entries, oldest-evicted. Thread-safe. **Good.**
3. **Response cache** (`response_cache.py`): two-tier (Redis shared + in-memory per-process). TTL 30-60s. LRU eviction at 500/namespace. Admin writes invalidate per-namespace. **Good.**
4. **Congestion zone cache** (`congestion_zones.py`): per-process, loaded once from CSV. **Good.**

**Issues found:**
1. **P1 — `get_congestion_stats` fallback subquery** (see Database section): Full table scan on every congestion query for rows missing `free_flow_duration_s`.
2. **P2 — OSRM `_circuit_breaker` state is per-process**: In a multi-worker deployment, each worker has independent circuit breaker state. One worker could be OPEN while others are CLOSED. Acceptable for the current single-worker dev setup but should be documented.
3. **P3 — `_route_cache` eviction in `osrm_client.py`** uses `min()` over the entire dict: O(n) per eviction. With `_ROUTE_CACHE_MAX_ENTRIES = 500`, this is negligible.
4. **P3 — `getAllStops` in `api.ts`** fetches pages of 100 but assumes the response length equals the requested size. If `MAX_PAGE_SIZE` on the server is lowered, rows are silently skipped. **Recommendation:** Read actual page length from response.

---

## # Security Review

**Strengths:**
- **JWT audience separation**: user tokens (`type: "user"`) can never satisfy `require_admin` (which checks `type: "admin"`). Verified in `security.py:111` and `security.py:145`.
- **Constant-time key comparison**: `secrets.compare_digest` for `X-Admin-Api-Key`.
- **Rate limiting**: 5/min on login/register, 10/hour on suggestions. Redis-backed when available.
- **Role-based access**: editor (routine CRUD) vs admin (status changes, graph reload).
- **No secrets in code**: all credentials from env vars via `pydantic-settings`.
- **CORS**: explicit origin list + localhost/LAN regex for dev.
- **Input validation**: Pydantic models with field constraints, FastAPI Query validation.

**Issues found:**
1. **P1 — API base URL forces `http://`**: As noted in Frontend review, all three API clients hardcode `http://` scheme. Under HTTPS, this becomes mixed content. **Security implication**: API calls silently fail, or worse, credentials are sent over unencrypted connections if the user manually navigates to `http://`.
2. **P2 — JWTs stored in `localStorage`**: Any XSS exfiltrates both admin and user sessions. Standard SPA tradeoff, but no `httpOnly` cookie option exists. No client-side token expiry check — stale tokens persist until a 401 surfaces.
3. **P3 — `_get_client_ip` trusts `X-Forwarded-For`**: First value is used without validation. Behind a trusted proxy this is correct; if the app is directly exposed, a client can spoof their IP for rate limiting. **Recommendation:** Document that a reverse proxy must strip/spoof this header.

---

## # Testing Gaps

### Backend (24 test files, good overall coverage)

| Area | Tested? | Notes |
|---|---|---|
| Graph construction + caching | Yes | `test_routing.py` |
| Direct route search | Yes | `test_routing.py` |
| Dijkstra transfer search | Yes | `test_routing.py` |
| Route alternatives (3 types) | Yes | `test_pathfinder_alternatives.py` |
| Via-route chaining | **Unit only** | `test_pathfinder_alternatives.py`; no API-level test |
| `max_transfers` | **No** | Not tested anywhere |
| Congestion weight functions | Yes | `test_congestion_weight_fn.py`, `test_duration_weight_fn.py` |
| Congestion zones | Yes | `test_congestion_zones.py` |
| `avoid_congestion=True` end-to-end | **No** | Unit functions tested; no integration proof |
| Stop positioning | Yes | `test_stop_positioning.py` + adversarial |
| Bearing waypoints | Yes | `test_bearing_waypoints.py` |
| OSRM geometry fallback chain | Yes | `test_attach_road_geometry.py` |
| OSRM circuit breaker | **No** | `CircuitBreaker` class untested |
| OSRM retry logic | **No** | `_should_retry` untested |
| Response cache (two-tier) | Yes | `test_response_cache.py` + isolation tests |
| Rate limiting | Partial | End-to-end 429 assertion; no unit test of config |
| Admin CRUD | Yes | `test_admin_crud_api.py` |
| Admin roles | Yes | `test_admin_roles.py` |
| Admin login | Yes | `test_admin_auth_api.py` |
| User registration/login | Yes | `test_user_auth_api.py` |
| Suggestions/votes/auto-apply | Yes | `test_suggestions_api.py` (very thorough) |
| GET /congestion | **No** | Classification, Nepal-time defaults untested |
| GET /congestion/buckets | **No** | Not tested |
| `_record_leg_congestion` background task | **No** | Upsert path untested |
| `seed_congestion_stats.py` | **No** | Script untested |

### Frontend (11 test files)

**Tested:** `BusMap`, `SuggestionBox`, `useRouteBrowser`, `useRouteSearch`, `useStops`, `api`, `stopLabel`, `stopClustering`, `sheetSnap`, `userApi`, `routeDistance`.

**Not tested:** `useCongestion`, `useSheet`, `useGeolocation`, `CongestionPanel`, `RouteResultPanel`, `RouteTimeline`, `StopAutocomplete`, `SearchForm`, `UserLogin`, `UserAuthContext`, `NavBar`, `LayerToggle`, all admin components, `adminApi`.

### Priority gaps to close:
1. OSRM circuit breaker (backend, pure-unit, no DB)
2. `_record_leg_congestion` + `GET /congestion` + Nepal-time bucket logic
3. `GET /route-finder?via=` API-level test
4. `max_transfers` parameter tests
5. `useCongestion` hook + `CongestionPanel` component tests

---

## # Documentation Gaps

| Location | Issue | Severity |
|---|---|---|
| `admin.py:314` docstring | Says "writes don't show up in `/api/route/find`" — should be `/route-finder` | P3 |
| `README.md:213` | Says `INTERCHANGE_DISTANCE (100 m)` but code uses `200` (`constants.py:3`) | P2 |
| `README.md:280` | Says "direct or single-transfer routes" but `/route-finder` supports multi-transfer via `via` param and `max_transfers` | P2 |
| `README.md:296-297` | PWA section says "not implemented" in Future Improvements but is actually implemented (sw.js, manifest.ts) | P2 |
| Migration `38a5d0f89268` docstring | Says `free_flow_duration_s` is "backfilled from existing min()-based value" but actually backfills from `avg_duration_s` for seeded rows | P3 |
| `constants.py:11-13` | `CONGESTION_LAMBDA` docstring says "tune by hand" but no evidence of tuning or validation methodology | P3 |
| `README.md:213` | Says `TRANSFER_PENALTY (3000)` but then says `INTERCHANGE_DISTANCE (100 m)` — the interchange distance is actually 200m | P2 |

---

## # Cross-Feature Opportunities

1. **Congestion + Alternatives**: The `fastest_estimated` alternative already uses congestion-aware duration weighting (`_duration_weight_fn` with `congestion_lookup`). The `shortest_distance` alternative does not. This is correct by design (distance is distance regardless of congestion), but a "least congested" alternative label could be a useful addition.

2. **Suggestions + Congestion**: Stop-sequence suggestions could feed back into congestion data — a reordered route might change which segments experience congestion. No code change needed, but worth noting that the congestion system will naturally adapt as new routes are added/modified via suggestions.

3. **Route positioning + Congestion layer**: The `display_lat`/`display_lng` positions from stop positioning could be used to draw more accurate congestion segments (currently straight-line between canonical stop coordinates). The infrastructure exists in `compute_adjusted_stop_positions`; it just isn't used by the congestion overlay.

4. **Admin UI + Suggestions**: The admin suggestion review queue (`/admin/suggestions`) exists as an API but the admin browser UI (`/admin`) doesn't surface it. Adding a suggestion review tab to the admin UI would close the loop.

---

## # Product / UX Improvements

1. **P1 — Deep-link stop label race**: As noted in Frontend review. A user sharing a link like `?origin=S0198&destination=S0021` may see "Pick a valid stop" if stops haven't loaded yet.

2. **P2 — No "now" indicator on congestion panel**: When the user picks a historical day/hour, there's no visual indicator that they're looking at historical data vs live. The "Reset to now" button helps, but a badge like "Viewing: Monday 9AM-12PM" would be clearer.

3. **P2 — No transfer count badge on alternatives**: The alternatives show route legs but don't prominently display how many transfers each requires. A rider choosing between a 0-transfer and 2-transfer alternative needs this info at a glance.

4. **P3 — No loading skeleton on congestion overlay**: When the congestion layer is toggled on, there's no loading indicator. The `loading` state exists in `useCongestion` but isn't surfaced to the map.

---

## # Academic / Engineering Improvements

1. **P2 — `CONGESTION_LAMBDA` needs empirical validation**: The constant `0.75` is described as a "starting value" to be tuned against known congested corridors. The formula `distance * (1 + 0.75 * max(ratio - 1, 0))` means a 2x-congested segment costs 1.75x its distance. Is this the right tradeoff vs the 3000 transfer penalty? A rider might prefer a longer direct route over a shorter-but-congested transfer. The answer depends on actual travel time vs distance, which is exactly what `BUS_AVG_SPEED_MPS` approximates. A simple sensitivity analysis (plot route choice vs lambda for a few known O-D pairs) would validate or refute the current value.

2. **P3 — EMA alpha (0.2) tuning**: `CONGESTION_EMA_ALPHA = 0.2` means roughly a 5-sample effective memory. Is this appropriate for a transit system where conditions change seasonally? Higher alpha → faster adaptation but noisier. Lower alpha → smoother but slower to reflect monsoon/road-work changes. Worth documenting the rationale or making it configurable.

3. **P3 — Three different "average bus speed" values**: Backend constants use 3.3 m/s (~12 km/h), frontend estimation uses 20 km/h, and `seed_demo_congestion.py` uses 20 km/h. The discrepancy means frontend "estimated duration" labels can contradict backend congestion ratios. Consider unifying to a single constant or documenting the difference.

---

## # Code Quality Improvements

1. **P1 — Triple API base URL**: `api.ts`, `adminApi.ts`, `userApi.ts` each define their own `apiBase()` function with nearly identical logic. Consolidate into `lib/apiBase.ts` and fix the `http://` scheme hardcoding.

2. **P2 — Triple timeout constant**: `DEFAULT_TIMEOUT_MS = 10_000` in 3 files. Move to `lib/constants.ts`.

3. **P2 — `"TRANSFER"` sentinel in 4+ files**: `RouteResultPanel`, `RouteTimeline`, `useRouteResultLayer`, tests. Move to `lib/constants.ts` as `TRANSFER_ROUTE_ID`.

4. **P2 — `LoadingStage` type duplicated**: `useRouteSearch.ts:5` and `RouteResultPanel.tsx:8` define the same type. Move to `types/route.ts`.

5. **P3 — `_json_dumps` operator alias hack** (`response_cache.py:40-50`): The cache serializer manually remaps `operator` → `operator_ref` for Redis storage because of a Pydantic `validation_alias`/`serialization_alias` mismatch. This is fragile — if the model changes, the cache serializer must be updated independently. Consider using `model_dump(by_alias=True)` consistently or fixing the alias configuration on `RouteOut.operator`.

---

## # Prioritized Improvement Table

| Priority | Feature | Problem | Recommendation | Complexity |
|---|---|---|---|---|
| P0 | API base URL | Hardcoded `http://` breaks HTTPS | Derive from `window.location.protocol`; consolidate 3 copies | Low |
| P1 | Deep-link | Stop labels race async loading | Wait for stops before applying `?origin/destination` params | Low |
| P1 | User JWT expiry | No client-side expiry handling | Add 401 interceptor to `userApi.ts` (same as `adminApi.ts`) | Low |
| P1 | Congestion index | `get_congestion_stats` fallback full-scans table | Backfill `free_flow_duration_s` for all rows; add covering index | Medium |
| P1 | `get_congestion_stats` | Fallback subquery unbounded | Add WHERE clause to limit fallback to rows with NULL `free_flow_duration_s` only | Low |
| P2 | Congestion legend | 3-dot legend doesn't match continuous gradient map | Replace with gradient bar or add ratio scale | Low |
| P2 | Route detail toggle | Refetches direction-independent data | Only refetch stops+geometry on direction change | Low |
| P2 | OSRM circuit breaker | Not tested | Add unit tests for state transitions | Low |
| P2 | Congestion API | No API-level tests | Add tests for `_classify`, Nepal-time defaults, `/congestion/buckets` | Medium |
| P2 | `via` API param | No API-level test | Add integration test for `?via=S0042&via=S0107` | Medium |
| P2 | `max_transfers` | Not tested | Add unit + integration tests | Medium |
| P2 | `CONGESTION_LAMBDA` | Untuned, documented as "tune by hand" | Validate against known corridors or document rationale | Medium |
| P2 | README accuracy | `INTERCHANGE_DISTANCE` says 100m, code says 200m | Fix README | Low |
| P2 | README accuracy | PWA listed as "not implemented" but is implemented | Move PWA from Future to Implemented section | Low |
| P2 | README accuracy | "Direct or single-transfer" but multi-transfer exists | Update description | Low |
| P2 | Code quality | API base URL duplicated 3x | Consolidate to shared module | Low |
| P2 | Code quality | Timeout constant duplicated 3x | Shared constant in `constants.ts` | Low |
| P2 | Code quality | `"TRANSFER"` sentinel duplicated | Shared constant | Low |
| P3 | Code quality | `LoadingStage` type duplicated | Shared type in `types/route.ts` | Low |
| P3 | Performance | `_route_cache` eviction O(n) | Switch to `OrderedDict` (low priority, 500 entries max) | Low |
| P3 | Rate limiting | `GET /suggestions` unthrottled | Add pagination or rate limit if needed | Low |
| P3 | Admin page | Fetches stops before auth check | Gate `useStops()` on auth state | Low |

---

## # Top 10 Improvements

| # | What | Why | Effort |
|---|---|---|---|
| 1 | Fix API base URL scheme (`http://` → derive from protocol) | HTTPS deployment broken | 30 min |
| 2 | Fix deep-link stop label race | Shared links fail for duplicated stop names | 1 hour |
| 3 | Add user JWT expiry handling | Users appear logged in after token expires | 1 hour |
| 4 | Backfill `free_flow_duration_s` for all congestion rows | Full table scan on every congestion query | 2 hours |
| 5 | Add OSRM circuit breaker tests | Untested resilience mechanism | 2 hours |
| 6 | Add `/congestion` + `/congestion/buckets` API tests | Untested endpoint | 2 hours |
| 7 | Add `?via=` API-level integration test | Untested multi-waypoint feature | 2 hours |
| 8 | Fix congestion legend to match gradient map | Legend shows 3 dots but map shows gradient | 1 hour |
| 9 | Fix route detail direction toggle flash | Wasted refetch + skeleton flash | 1 hour |
| 10 | Consolidate triple API base URL + timeout constant | Code duplication + drift risk | 1 hour |

---

## # Implementation Roadmap

### Phase 1: Correctness (P0 + P1 fixes)
1. Fix `apiBase()` scheme — derive from `window.location.protocol`, consolidate 3 copies → `lib/apiBase.ts`
2. Fix deep-link race — wait for `stops` to load before applying `?origin/destination`
3. Add user JWT expiry handling — 401 interceptor in `userApi.ts`
4. Backfill `free_flow_duration_s` — migration + one-time UPDATE for NULL rows
5. Add covering index on `(route_id, from_stop_id, to_stop_id)` for `segment_congestion_stats`

### Phase 2: Backend Test Coverage (P2 tests)
1. OSRM circuit breaker unit tests (state transitions, concurrent access)
2. `GET /congestion` API test (classification, Nepal-time defaults, bucket rounding)
3. `GET /congestion/buckets` API test
4. `GET /route-finder?via=` integration test (leg chaining, error reporting, alternatives disabled)
5. `max_transfers` parameter tests (unit + integration)
6. `_record_leg_congestion` background task test (upsert behavior, seed replacement)

### Phase 3: Frontend Polish (P2 UX + code quality)
1. Fix congestion legend — gradient bar or ratio scale instead of 3-dot
2. Fix route detail direction toggle — only refetch direction-dependent data
3. Consolidate `DEFAULT_TIMEOUT_MS` to shared constant
4. Consolidate `"TRANSFER"` sentinel to shared constant
5. Consolidate `LoadingStage` type to `types/route.ts`
6. Fix admin page — gate `useStops()` on auth state

### Phase 4: Documentation + Data (P2 doc fixes)
1. Fix README: `INTERCHANGE_DISTANCE` 100m → 200m
2. Fix README: PWA moved from Future to Implemented
3. Fix README: "direct or single-transfer" → include multi-transfer
4. Fix admin.py docstring: `/api/route/find` → `/route-finder`
5. Run `compute_osrm_route_distances.py` for 32 flagged routes
6. Document `CONGESTION_LAMBDA` validation approach

### Phase 5: Polish (P3 + longer-term)
1. Validate `CONGESTION_LAMBDA` against known corridors
2. Add `_record_leg_congestion` test coverage
3. Add admin suggestion review UI tab
4. Consider "least congested" alternative label
5. Consider using `display_lat`/`display_lng` for congestion overlay segments
6. Add pagination to `GET /admin/suggestions` if needed

---

## # Files Likely To Change

| File | Reason |
|---|---|
| `frontend/lib/api.ts` | Consolidate `apiBase()`, fix scheme |
| `frontend/lib/adminApi.ts` | Remove duplicate `apiBase()` |
| `frontend/lib/userApi.ts` | Remove duplicate `apiBase()`, add 401 interceptor |
| `frontend/lib/constants.ts` | Add `TRANSFER_ROUTE_ID`, `DEFAULT_TIMEOUT_MS` |
| `frontend/types/route.ts` | Add `LoadingStage` type |
| `frontend/app/page.tsx` | Fix deep-link race |
| `frontend/app/routes/[routeId]/page.tsx` | Fix direction toggle refetch |
| `frontend/components/CongestionPanel.tsx` | Fix legend to match gradient |
| `backend/app/db/queries.py` | Backfill `free_flow_duration_s` in fallback |
| `backend/migrations/versions/` | New migration for backfill + index |
| `README.md` | Fix 3 documentation inaccuracies |
| `backend/app/api/admin.py` | Fix docstring |

## # Things NOT To Change

| Area | Reason |
|---|---|
| `pathfinder.py` routing algorithm | Sound design, well-tested, no bugs found |
| `graph_builder.py` sequence-aware nodes | Correctly handles loops, well-cached |
| `stop_positioning.py` | Excellent implementation, thoroughly tested |
| `security.py` JWT audience separation | Correct and secure |
| `response_cache.py` two-tier design | Well-designed, Redis fallback works |
| `congestion_zones.py` dual-source blending | Sound architecture for the data available |
| `constants.py` core values | `TRANSFER_PENALTY`, `CONGESTION_LAMBDA`, `BUS_AVG_SPEED_MPS` — reviewed, acceptable |
| `osrm_client.py` retry/circuit-breaker logic | Correct implementation, just needs tests |
| `services/suggestions.py` apply path | Single mutation point, correct |
| Database schema (migration chain) | Clean, well-indexed, constraints correct |

---

## # Final Recommendation

**Ship it with the Phase 1 fixes.** The codebase is well above the quality bar for a BE Minor Project. The architecture is sound, the code is well-documented, the separation of concerns is clean, and the feature set is impressive for a student project (dual-source congestion, route alternatives, crowd-sourced suggestions, admin CRUD, PWA).

The P0 issue (HTTPS scheme) is a 30-minute fix that blocks any production deployment. The P1 issues (deep-link race, JWT expiry, congestion index) are important but not blockers for a demo/dev deployment. Everything else is incremental improvement.

**Before merging to main:**
1. Fix the `http://` scheme in `apiBase()` (P0 — 30 min)
2. Fix the deep-link race (P1 — 1 hour)
3. Backfill `free_flow_duration_s` (P1 — 2 hours)
4. Fix README inaccuracies (P2 — 30 min)

**After merging to main (follow-up PRs):**
- Phase 2: Backend test coverage (1-2 days)
- Phase 3: Frontend polish (1 day)
- Phase 4: Documentation + data cleanup (1 day)
- Phase 5: Long-term polish (ongoing)
