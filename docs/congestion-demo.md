# Congestion-Aware Routing — Before/After Demo

This documents real `avoid_congestion=false` vs `avoid_congestion=true`
results from `GET /route-finder`, run against the live dataset by actually
calling the FastAPI endpoint (`fastapi.testclient.TestClient`, same code
path a real request hits) — not by calling internal functions directly.

## Two independent congestion mechanisms

`pathfinder.py` combines two signals per ride edge, taking whichever is
worse (max, not additive — a real traffic jam doesn't get worse just
because two data sources both know about it):

1. **Per-route-segment** (`app/db/queries.py`'s
   `segment_congestion_stats`) — durations recorded over time, keyed to a
   specific `(route_id, from_stop_id, to_stop_id)`. These are OSRM
   free-flow durations from `/route-finder` responses (see
   `_record_leg_congestion` in `app/api/routing.py`), **not** observed
   traffic. The demo seeder (`scripts/seed_demo_congestion.py`) writes
   synthetic ratios from the severity table's score instead.
2. **Geographic zones** (`app/routing/congestion_zones.py`) — fixed
   points with a radius (200–500m, scaled by the table's severity score)
   and a ratio, affecting **any** route passing near that point, not just
   one. Built from `data/congestion_zones.csv`, itself derived from
   `data/congestion_loss_table.csv`, a static per-intersection severity
   table whose origin this repository does **not** record, matched against
   this project's actual stops — see `data/stop_congestion_scores.csv` for
   the match manifest. Treat its scores as unverified estimates; see "What
   this data is and isn't" below.

The zone mechanism is the more realistic of the two: a real traffic jam
at Tripureshwor slows down every bus passing through it, not just one
operator's route. Confirmed against the live graph: **34 real active
routes** pass within Tripureshwor's 500m zone radius.

## What this data is and isn't

Read this before quoting any congestion number or calling the feature
"live traffic".

- **There is no live, real-time, or measured traffic feed anywhere in
  this stack.** `GET /congestion` states this in its `provenance` block
  (`is_live: false`, `is_measured: false`) and the UI renders that note.
- **Segment stats are OSRM-derived, not observed.** Rows recorded by
  `/route-finder` come from OSRM's `road_geometry` duration — a
  routing-engine free-flow estimate, the same source the seeded baseline
   uses — and the demo seeder fabricates ratios from the (unverified)
   severity table's score. A row with `is_seeded: false` is *recorded*,
   not *measured*: the flag does not mean "real traffic".
- **Zones are derived from a study table whose origin the repository does
  not document.** `data/congestion_loss_table.csv` is described in this
  doc as a "91-location Kathmandu traffic study", but `data/raw/README.md`
  does not record how it was collected, and `data/LICENSING.md` lists the
  congestion tables as authored by this project. Treat the severity scores
  as a desk estimate unless and until that provenance is confirmed; do not
  present them as authoritative measurements.
- **The map overlay is an approximation.** `GET /congestion` covers only
  the per-segment mechanism and draws a straight line between each
  segment's two stops, not the road geometry. Routing's
  `avoid_congestion=true` additionally uses geographic zones the overlay
  does not draw, so the overlay can understate what routing avoids.

## Setup

Zones load automatically — no seed step needed — as soon as
`data/congestion_zones.csv` exists. To also seed the organic mechanism:

```bash
cd backend
python -m scripts.seed_demo_congestion
```

## Case 1: Same-corridor bus swap (zones alone, no organic data needed)

**Tripureshwor → a stop past Jamal, no congestion:**

```
GET /route-finder?origin=S0056&destination=S0072&avoid_congestion=false
legs: R-SAJHA-01 -> R3102124
```

**Same request, congestion avoidance on (zones only, organic table empty):**

```
GET /route-finder?origin=S0056&destination=S0072&avoid_congestion=true
legs: R2988836 -> R3102124
```

**What happened:** with the Tripureshwor zone active (radius 500m, ratio
4.20, derived from the top score — 10 — in the severity table), *every*
route passing near Tripureshwor is penalized — not just `R-SAJHA-01`. The
router picks `R2988836` (Thankot–Ratna Park) instead, a real route that
avoids the zone entirely.

## Case 2: Full reroute via a different feeder route

**Maitighar → Gongabu Bus Park, no congestion:**

```
GET /route-finder?origin=S0097&destination=S0252&avoid_congestion=false
legs: R2909799 -> R-NY-07 -> [walk transfer] -> R-SAJHA-01
```

**Same request, congestion avoidance on:**

```
GET /route-finder?origin=S0097&destination=S0252&avoid_congestion=true
legs: R2909799 -> R-NY-06 -> [walk transfer] -> R-SAJHA-01
```

**What happened:** `R-SAJHA-01` is the *only* route serving Gongabu Bus
Park at all, so it still has to be ridden for the final leg — but the
router picks a different second-leg feeder (`R-NY-06` instead of
`R-NY-07`) to avoid a zone along the original path, joining `R-SAJHA-01`
via a different transfer point.

## A case where nothing changes, and why that's correct

Direct routes are only scored congestion-aware when `avoid_congestion=true`
(see `pathfinder.py`'s `find_shortest_path` docstring): the direct route
and the transfer fallback are then compared on the same cost basis, so a
direct route can lose to a less-congested transfer. When it is
free-flowing or otherwise still the cheaper option, the direct ride stays.
Under `avoid_congestion=false`, a viable direct route retains the
historical direct-first behavior; under `avoid_congestion=true`, the
comparison can pick a transfer route over a congested direct route. A
direct route remains available whenever the transfer search has nothing
cheaper to offer.

## Severity data behind the zones

`data/congestion_zones.csv` matches 41 of this project's stops against
locations in `data/congestion_loss_table.csv`. That table records a
severity `Score` per location but **no author, date, method or citation**,
and no raw-source entry; treat the scores as unverified desk estimates, not
measurements (see "What this data is and isn't" above and
`data/README.md`). As recorded in the table: Tripureshwor has the highest
score (10), Koteshwor the next (10). Two stops (Narayangopal Chowk, Gongabu
Bus Park) aren't matched to any table location, so they use the table's own
average score (2.17) as an explicit, honest fallback rather than an invented
number — see `scripts/seed_demo_congestion.py`'s `OVERRIDE_SEGMENTS`
comment for detail on that tradeoff.

## Limitation worth stating plainly

Zone radii are a straight-line approximation from each segment's two stop
endpoints, not a check against actual road geometry — a segment could
technically curve away from a zone despite an endpoint being within
radius, or pass close to a zone without either endpoint being inside it.
Good enough for routing decisions at this scale, but worth knowing if
results ever look surprising for a specific segment.
