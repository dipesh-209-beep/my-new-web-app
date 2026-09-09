"""
diagnostic / validate_stop_positioning.py

Exhaustive real-data validation of route-aware, direction-specific stop
positioning across EVERY route in the database.

For every route (forward + reverse when bidirectional):
  - fetch stops in ascending sequence_no
  - build travel order for the direction
  - run the exact OSRM flow used by GET /routes/{route_id}/stops:
      constrained (bearing+radius) -> fallback unconstrained
  - project each stop onto the returned geometry via
    compute_adjusted_stop_positions (the module under test)
  - for each projected position, compute its cumulative route-progress
    distance so we can detect non-monotonic (backward) progression

Outputs aggregate stats + per-route worst cases.

Run inside the backend container (has DB + OSRM access):

    docker exec -e PYTHONPATH=/app ktm_bus_backend python scripts/validate_stop_positioning.py
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from app.db import queries
from app.db.session import SessionLocal
from app.routing.graph_builder import haversine_distance_m
from app.routing.osrm_client import OSRMError, get_route_geometry
from app.routing.stop_positioning import (
    compute_adjusted_stop_positions,
    geojson_linestring_to_coords,
    MONOTONIC_POSITION_TOLERANCE_M,
)
from app.api.routing import _thin_waypoints, _bearings_for, WAYPOINT_SNAP_RADIUS_M
from app.schemas import StopOut


@dataclass
class StopProbe:
    route_id: str
    direction: str
    stop_id: str
    sequence_no: int
    canonical_lat: float
    canonical_lng: float
    display_lat: float | None
    display_lng: float | None
    source: str
    offset_m: float
    progress_m: float | None = None
    prev_progress_m: float | None = None
    backward: bool = False

    @property
    def used_fallback(self) -> bool:
        return self.source == "canonical_fallback"


def _progress_point_on_path(
    route_coords: list[tuple[float, float]], lat: float, lng: float
) -> float | None:
    """Cumulative distance along route_coords up to the point on the path
    that is closest to (lat, lng). The adjusted positions are *on* the
    polyline, but we recompute generically so progress stays meaningful
    even for canonical fallbacks (they're not on the path)."""
    from app.routing.stop_positioning import _project_point_to_segment

    if len(route_coords) < 2:
        return None
    best_progress = None
    best_dist = float("inf")
    cum = 0.0
    for i in range(len(route_coords) - 1):
        sx, sy = route_coords[i]
        ex, ey = route_coords[i + 1]
        seg_len = haversine_distance_m(sx, sy, ex, ey)
        pl, pr, d = _project_point_to_segment(lat, lng, (sx, sy), (ex, ey))
        num = (pl - sx) * (ex - sx) + (pr - sy) * (ey - sy)
        den = (ex - sx) ** 2 + (ey - sy) ** 2
        t = 0.0 if den == 0 else max(0.0, min(1.0, num / den))
        progress = cum + t * seg_len
        if d < best_dist:
            best_dist = d
            best_progress = progress
        cum += seg_len
    return best_progress


def _fetch_geometry(travel_order_stops: list, route_id: str, direction: str):
    """Mirrors _display_positions_for_direction's OSRM flow; returns
    (route_coords, constrained_ok) or (None, False) on total failure."""
    stops = [StopOut.model_validate(rs.stop) for rs in travel_order_stops]
    coords = _thin_waypoints(stops)
    if len(coords) < 2:
        return None, False
    bearings = _bearings_for(coords)
    radiuses = [WAYPOINT_SNAP_RADIUS_M] * len(coords)
    try:
        geometry = get_route_geometry(coords, bearings=bearings, radiuses=radiuses)
        return geojson_linestring_to_coords(geometry["geometry"]), True
    except OSRMError:
        try:
            geometry = get_route_geometry(coords)
            return geojson_linestring_to_coords(geometry["geometry"]), False
        except OSRMError:
            return None, False


def main() -> None:
    db = SessionLocal()
    routes = [r for r in queries.get_active_routes(db)]
    db.close()

    probes: list[StopProbe] = []
    per_direction: dict[tuple[str, str], list[StopProbe]] = {}
    screenshots: list[str] = []
    constrained_failures = 0
    total_osrm_failures = 0
    geometry_missing_directions = 0

    for route in routes:
        db = SessionLocal()
        try:
            route_stops = queries.get_route_stops(db, route.route_id)
        finally:
            db.close()
        if len(route_stops) < 2:
            continue
        directions = ["forward"] + (["reverse"] if route.is_bidirectional else [])
        for direction in directions:
            travel_order = (
                list(reversed(route_stops)) if direction == "reverse" else list(route_stops)
            )
            route_coords, constrained_ok = _fetch_geometry(travel_order, route.route_id, direction)
            if not constrained_ok:
                constrained_failures += 1
            if route_coords is None:
                geometry_missing_directions += 1
                total_osrm_failures += 1
                # record canonical fallbacks for every stop
                order = route_stops if direction == "forward" else list(reversed(route_stops))
                for rs in route_stops:
                    p = StopProbe(
                        route.route_id, direction, rs.stop.stop_id, rs.sequence_no,
                        rs.stop.lat, rs.stop.lng, None, None, "canonical_fallback", 0.0,
                    )
                    probes.append(p)
                    per_direction.setdefault((route.route_id, direction), []).append(p)
                continue

            order = route_stops if direction == "forward" else list(reversed(route_stops))
            stops_for_projection = [(rs.stop_id, rs.stop.lat, rs.stop.lng) for rs in order]
            positions = compute_adjusted_stop_positions(route_coords, stops_for_projection)

            # Positions are in TRAVEL order. The monotonicity invariant is
            # defined in travel order too -- progress must not decrease from
            # one stop to the next as the vehicle moves. (For reverse, travel
            # order is the reversed sequence, so ascending sequence_no
            # corresponds to *decreasing* progress; a check over ascending
            # order would flag correct behavior.) progress_m is the module's
            # own accepted-projection progress (authoritative), None for
            # canonical fallbacks.
            seq_by_id = {rs.stop_id: rs.sequence_no for rs in route_stops}
            prev_progress: float | None = None
            dir_probes: list[StopProbe] = []
            for pos in positions:
                rs = next(rs for rs in order if rs.stop_id == pos.stop_id)
                progress = pos.progress_m
                backward = False
                if (
                    progress is not None
                    and pos.source == "projected"
                    and prev_progress is not None
                    and progress + MONOTONIC_POSITION_TOLERANCE_M < prev_progress
                ):
                    backward = True
                p = StopProbe(
                    route.route_id, direction, rs.stop_id, seq_by_id[rs.stop_id],
                    rs.stop.lat, rs.stop.lng, pos.lat, pos.lng, pos.source, pos.offset_m,
                    progress, prev_progress, backward,
                )
                probes.append(p)
                dir_probes.append(p)
                if progress is not None:
                    prev_progress = progress
            per_direction[(route.route_id, direction)] = dir_probes

    # ---- Aggregate stats ----
    total_stops = len(probes)
    projected = [p for p in probes if p.source == "projected"]
    fallbacks = [p for p in probes if p.source == "canonical_fallback"]
    backward = [p for p in probes if p.backward]
    offsets = [p.offset_m for p in projected]
    forward_probes = [p for p in probes if p.direction == "forward"]

    print("=" * 70)
    print(f"Routes:                {len(routes)}")
    print(f"Directions checked:    {len(per_direction)}")
    print(f"Stops checked:         {total_stops}")
    print(f"  forward:             {len(forward_probes)}")
    print(f"  reverse:             {total_stops - len(forward_probes)}")
    print()
    print(f"Projection success:    {len(projected)} ({100*len(projected)/max(total_stops,1):.1f}%)")
    print(f"Canonical fallback:    {len(fallbacks)} ({100*len(fallbacks)/max(total_stops,1):.1f}%)")
    print(f"Backward projections:  {len(backward)}")
    print(f"Directions w/ missing geometry: {geometry_missing_directions}")
    print(f"OSRM total failures:   {total_osrm_failures}")
    print(f"Constrained fallbacks (of {max(len(per_direction),1)}): {constrained_failures}")
    print()
    if offsets:
        print(f"Avg projection offset: {statistics.mean(offsets):.1f} m")
        print(f"Median projection offset: {statistics.median(offsets):.1f} m")
        try:
            p95 = sorted(offsets)[int(0.95 * len(offsets))]
        except IndexError:
            p95 = offsets[-1]
        print(f"P95 projection offset: {p95:.1f} m")
        print(f"Max projection offset: {max(offsets):.1f} m")

    print()
    print("=== Backward projections (progress violation) ===")
    if not backward:
        print("  none")
    for p in sorted(backward, key=lambda p: (p.route_id, p.direction, p.sequence_no)):
        print(f"  {p.route_id} {p.direction} seq={p.sequence_no} {p.stop_id}: "
              f"progress={p.progress_m:.0f}m previous={p.prev_progress_m:.0f}m "
              f"delta={p.progress_m - p.prev_progress_m if p.progress_m and p.prev_progress_m else 0:.0f}m "
              f"offset={p.offset_m:.1f}m source={p.source}")

    print()
    print("=== Fallback rates by route/direction (worst 20) ===")
    rates = []
    for (rid, d), ps in per_direction.items():
        fb = sum(1 for p in ps if p.used_fallback)
        rates.append((rid, d, fb, len(ps), 100 * fb / max(len(ps), 1)))
    rates.sort(key=lambda r: -r[4])
    for rid, d, fb, n, pct in rates[:20]:
        print(f"  {rid} {d}: {fb}/{n} fallback ({pct:.0f}%)")

    print()
    print(f"=== Largest projection offsets (top 20) ===")
    most = sorted(projected, key=lambda p: -p.offset_m)[:20]
    for p in most:
        print(f"  {p.route_id} {p.direction} seq={p.sequence_no} {p.stop_id}: offset={p.offset_m:.1f}m progress={p.progress_m:.0f}m source={p.source}")


if __name__ == "__main__":
    main()