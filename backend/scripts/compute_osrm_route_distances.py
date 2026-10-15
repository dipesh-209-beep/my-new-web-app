"""Populate routes.osrm_distance_km -- the real road distance for each
active route's full ordered stop sequence -- by calling OSRM once per
route over that route's waypoint-thinned stop sequence.

Run from backend/, against a live OSRM instance (OSRM_BASE_URL):
    python3 scripts/compute_osrm_route_distances.py

Only rewrites routes whose osrm_distance_km is still null, so it's
cheap to re-run after adding new routes -- pass --force to recompute
every active route instead (e.g. after a stop-coordinate correction).

This replaces relying on approx_distance_km (source-data-supplied,
occasionally flagged/incorrect -- see distance_flagged_for_recompute)
or haversine_distance_km (straight-line, systematically shorter than
real road distance) for anything shown to users as "distance".

Relationship to the live route-finder
-------------------------------------
osrm_distance_km is a *whole-route* figure: one OSRM request for the
entire (thinned) stop sequence. The route-finder computes its per-leg
road_geometry and path total differently -- one OSRM request per ride
leg, each with its own waypoint constraints and its own transfer/walk
handling -- so the two numbers can differ. Treat osrm_distance_km as a
good whole-route estimate for display/ranking, NOT as a value guaranteed
to equal a route-finder result's per-leg total.

It is also not the graph/haversine basis the route-finder falls back to
(see app/api/routing.py::_path_distance_source): when a path has no OSRM
geometry it uses the graph's summed per-edge haversine, which is shorter
than real road distance.

When to run
-----------
After `make import`, or after any change to a route's stops (added,
removed, re-sequenced) or to a stop's coordinates (use --force for the
latter). OSRM must be reachable (`make osrm-up`); a route whose OSRM
call fails, or whose result isn't a usable positive distance, is skipped
and reported, never written.

Cache behavior
--------------
The `routes` and `route_detail` response caches are invalidated after
*each successfully committed batch* that changed a distance (see
app/core/response_cache.py), not once at the very end -- GET /routes and
GET /routes/{route_id} embed osrm_distance_km, so an interrupted run must
not leave an already-committed batch hidden behind stale cached
responses. With REDIS_URL set this clears the shared cache every worker
reads from immediately; without Redis it only clears this process's
in-memory tier, and the API process picks the new values up when its
ROUTES_CACHE_TTL_S expires. A batch that changed nothing is not
invalidated.
"""
import argparse
import math
import sys
from typing import Optional

from app.core.response_cache import invalidate as invalidate_cache
from app.db import queries
from app.db.session import SessionLocal
from app.routing.graph_builder import haversine_distance_m
from app.routing.osrm_client import OSRMError, get_route_geometry

MIN_WAYPOINT_SPACING_M = 80  # mirrors api/routing.py::MIN_WAYPOINT_SPACING_M


def _thin_waypoints(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(coords) < 2:
        return coords
    thinned = [coords[0]]
    for lat, lng in coords[1:-1]:
        last_lat, last_lng = thinned[-1]
        if haversine_distance_m(last_lat, last_lng, lat, lng) >= MIN_WAYPOINT_SPACING_M:
            thinned.append((lat, lng))
    last = coords[-1]
    if thinned[-1] != last:
        thinned.append(last)
    return thinned


def _rounded_km(geometry: object) -> Optional[float]:
    """Rounded kilometres from an OSRM result, or None when the
    measurement is missing/not a positive finite number.

    get_route_geometry already rejects those payloads (see
    app/routing/osrm_client.py), so this is a defensive second check:
    the script must never write a bad value into the column just because
    a client or a fixture handed it one."""
    distance_m = geometry.get("distance_m") if isinstance(geometry, dict) else None
    if (
        isinstance(distance_m, (int, float))
        and not isinstance(distance_m, bool)
        and math.isfinite(distance_m)
        and distance_m > 0
    ):
        return round(distance_m / 1000, 2)
    return None


def _invalidate_distance_caches() -> None:
    """Drop the two response caches whose payload embeds osrm_distance_km
    (RouteOut via GET /routes and GET /routes/{route_id}). route_stops and
    route_geometry don't carry it, so those namespaces are left alone."""
    invalidate_cache("routes")
    invalidate_cache("route_detail")


def _commit_batch(db, changed_in_batch: int) -> int:
    """Commit the pending batch, then invalidate the distance caches iff
    the commit succeeded and that batch actually changed a stored value.

    Called after every batch commit (the periodic one and the final one),
    so a run that dies partway has already invalidated each batch it
    managed to persist -- invalidating only at the end would leave the
    earlier batches' values hidden behind a stale cache until TTL. The
    commit happens first, so a raising commit propagates before any
    invalidation and an uncommitted batch is never treated as committed.
    Returns the reset per-batch changed counter."""
    db.commit()
    if changed_in_batch > 0:
        _invalidate_distance_caches()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute even routes that already have an osrm_distance_km.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    updated = 0
    changed = 0
    skipped = 0
    changed_in_batch = 0
    try:
        routes = queries.get_active_routes(db)
        pending = [r for r in routes if args.force or r.osrm_distance_km is None]
        print(f"{len(routes)} active routes, {len(pending)} to compute...")

        for i, route in enumerate(pending, start=1):
            ordered = sorted(
                (rs for rs in route.route_stops if rs.stop is not None),
                key=lambda rs: rs.sequence_no,
            )
            coords = _thin_waypoints([(rs.stop.lat, rs.stop.lng) for rs in ordered])
            if len(coords) < 2:
                skipped += 1
                continue

            try:
                geometry = get_route_geometry(coords)
            except OSRMError as exc:
                skipped += 1
                print(f"  [{i}/{len(pending)}] {route.route_id}: OSRM failed ({exc}), skipped")
                continue

            km = _rounded_km(geometry)
            if km is None:
                skipped += 1
                print(f"  [{i}/{len(pending)}] {route.route_id}: OSRM gave no usable distance, skipped")
                continue

            previous = route.osrm_distance_km
            route.osrm_distance_km = km
            db.add(route)
            updated += 1
            # Numeric columns come back as Decimal, so compare by value.
            if previous is None or float(previous) != km:
                changed += 1
                changed_in_batch += 1
            if i % 20 == 0:
                changed_in_batch = _commit_batch(db, changed_in_batch)
            print(f"  [{i}/{len(pending)}] {route.route_id}: {km} km")

        # Final (usually partial) batch: commit, then invalidate iff this
        # last batch actually changed something.
        _commit_batch(db, changed_in_batch)
        print(f"Done. Updated {updated}, changed {changed}, skipped {skipped}.")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
