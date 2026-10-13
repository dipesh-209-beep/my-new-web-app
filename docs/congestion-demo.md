# Congestion-Aware Routing — Before/After Demo

This documents real `avoid_congestion=false` vs `avoid_congestion=true`
results from `GET /route-finder`, run against the live dataset by actually
calling the FastAPI endpoint (`fastapi.testclient.TestClient`, same code
path a real request hits) — not by calling internal functions directly.

## Two independent congestion mechanisms

`pathfinder.py` combines two signals per ride edge, taking whichever is
worse (max, not additive — a real traffic jam doesn't get worse just
because two data sources both know about it):

1. **Organic, per-route-segment** (`app/db/queries.py`'s
   `segment_congestion_stats`) — learned from real ride durations over
   time, keyed to a specific `(route_id, from_stop_id, to_stop_id)`.
   Seeded for a demo via `scripts/seed_demo_congestion.py`.
2. **Geographic zones** (`app/routing/congestion_zones.py`) — fixed
   points with a radius (200–500m, scaled by real severity) and a ratio,
   affecting **any** route passing near that point, not just one. Built
   from `data/congestion_zones.csv`, itself derived from
   `data/congestion_loss_table.csv` (a real 91-location Kathmandu traffic
   study) matched against this project's actual stops — see
   `data/stop_congestion_scores.csv` for the match manifest.

The zone mechanism is the more realistic of the two: a real traffic jam
at Tripureshwor slows down every bus passing through it, not just one
operator's route. Confirmed against the live graph: **34 real active
routes** pass within Tripureshwor's 500m zone radius.

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
4.20, derived from its real rank #1-of-91 severity score), *every* route
passing near Tripureshwor is penalized — not just `R-SAJHA-01`. The
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

## Real severity data behind the zones

`data/congestion_zones.csv` matches 42 of this project's stops against
real measured locations from `data/congestion_loss_table.csv`. Per that
study: Tripureshwor is the **#1 worst-congested point measured in the
entire city** (score 10, rank 1 of 91); Koteshwor is #2. Two stops
(Narayangopal Chowk, Gongabu Bus Park) aren't in the source study, so they
use the dataset's own average score (2.17) as an explicit, honest
fallback rather than an invented number — see
`scripts/seed_demo_congestion.py`'s `OVERRIDE_SEGMENTS` comment for detail
on that tradeoff.

## Limitation worth stating plainly

Zone radii are a straight-line approximation from each segment's two stop
endpoints, not a check against actual road geometry — a segment could
technically curve away from a zone despite an endpoint being within
radius, or pass close to a zone without either endpoint being inside it.
Good enough for routing decisions at this scale, but worth knowing if
results ever look surprising for a specific segment.
