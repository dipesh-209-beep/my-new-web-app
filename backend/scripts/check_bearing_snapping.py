"""
Empirical check: does adding bearing/radius constraints (see
app/api/routing.py::_bearings_for, WAYPOINT_SNAP_RADIUS_M) actually
change what OSRM snaps to for a given route, in either direction of
travel?

Run against a live OSRM instance and a live DB with route data
imported (docker compose up -d db osrm):

    cd backend
    python3 scripts/check_bearing_snapping.py --route-id R3102605

If --route-id is omitted, picks the first bidirectional route with at
least 3 stops it finds.

For both the route's forward stop order and its reverse (return-leg)
order, this calls OSRM twice -- once with no constraints (today's
behavior) and once with bearing+radius -- and reports:
  - whether the two calls agree (same distance/duration): if so, this
    route has no wrong-carriageway ambiguity to fix, and the
    constraint is a safe no-op here, as expected on undivided roads.
  - if they disagree: that's exactly the signal the fix is doing
    something -- open both polylines on a map (or diff the returned
    `geometry` coordinates) to confirm the constrained one is the
    correct carriageway, since this script can only detect *that*
    something changed, not confirm *which* result is right.

This is a read-only diagnostic -- it doesn't write anything to the DB.
"""
import argparse
import sys

from app.db import queries
from app.db.session import SessionLocal
from app.routing.graph_builder import haversine_distance_m
from app.routing.osrm_client import OSRMError, get_route_geometry
from app.api.routing import _bearings_for, WAYPOINT_SNAP_RADIUS_M, MIN_WAYPOINT_SPACING_M


def _thin(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Mirrors app/api/routing.py::_thin_waypoints, but on raw (lat, lng)
    tuples so it works the same whether coords came from a forward or a
    reversed stop list."""
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


def _pick_default_route_id(db) -> str | None:
    for route in queries.get_active_routes(db):
        if route.is_bidirectional and route.total_stops and route.total_stops >= 3:
            return route.route_id
    return None


def _compare(label: str, coords: list[tuple[float, float]]) -> None:
    print(f"\n--- {label} ({len(coords)} waypoints after thinning) ---")
    if len(coords) < 2:
        print("Fewer than 2 waypoints after thinning -- nothing to compare.")
        return

    try:
        baseline = get_route_geometry(coords)
    except OSRMError as exc:
        print(f"Baseline (unconstrained) call failed: {exc}")
        return

    bearings = _bearings_for(coords)
    radiuses = [WAYPOINT_SNAP_RADIUS_M] * len(coords)
    try:
        constrained = get_route_geometry(coords, bearings=bearings, radiuses=radiuses)
    except OSRMError as exc:
        print(
            f"Constrained call failed ({exc}) -- baseline distance was "
            f"{baseline['distance_m']:.0f}m. A failure here (rather than a "
            f"differing result) usually means the {WAYPOINT_SNAP_RADIUS_M}m "
            f"radius is too tight for this route's stop spacing; the live "
            f"code already falls back to the unconstrained result in this case."
        )
        return

    print(f"Baseline distance:    {baseline['distance_m']:.0f}m, {baseline['duration_s']:.0f}s")
    print(f"Constrained distance: {constrained['distance_m']:.0f}m, {constrained['duration_s']:.0f}s")

    same = (
        abs(baseline["distance_m"] - constrained["distance_m"]) < 1.0
        and baseline["geometry"] == constrained["geometry"]
    )
    if same:
        print("Identical -- no wrong-carriageway ambiguity here (expected on undivided roads).")
    else:
        print(
            "DIFFERENT -- the constraint changed what OSRM snapped to. "
            "Worth opening both geometries on a map to confirm the "
            "constrained one is the correct carriageway for this direction."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-id", help="Route to check. Auto-picked if omitted.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        route_id = args.route_id or _pick_default_route_id(db)
        if route_id is None:
            print("No suitable bidirectional route found (need >= 3 stops).")
            sys.exit(1)

        route = queries.get_route(db, route_id)
        if route is None:
            print(f"Route '{route_id}' not found.")
            sys.exit(1)

        route_stops = queries.get_route_stops(db, route_id)
        if len(route_stops) < 2:
            print(f"Route '{route_id}' has fewer than 2 stops.")
            sys.exit(1)

        print(f"Checking route {route_id} ({route.route_name}), "
              f"{len(route_stops)} stops, is_bidirectional={route.is_bidirectional}")

        forward_coords = _thin([(rs.stop.lat, rs.stop.lng) for rs in route_stops])
        _compare("Forward direction", forward_coords)

        if route.is_bidirectional:
            reverse_coords = _thin(list(reversed(
                [(rs.stop.lat, rs.stop.lng) for rs in route_stops]
            )))
            _compare("Reverse direction (return leg)", reverse_coords)
        else:
            print("\nRoute is not bidirectional -- no return leg to check.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
