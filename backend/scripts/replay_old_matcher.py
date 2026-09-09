"""Replay the OLD segment-index-cursor stop matcher against real data to
quantify how many actual backward projections it produced (before the
progress-based fix). Read-only: uses the live OSRM geometry but an
in-script replica of the old algorithm."""
from __future__ import annotations

from dataclasses import dataclass

from app.db import queries
from app.db.session import SessionLocal
from app.routing.graph_builder import haversine_distance_m
from app.routing.osrm_client import OSRMError, get_route_geometry
from app.routing.stop_positioning import (
    MAX_STOP_OFFSET_M,
    _cumulative_route_metrics,
    _project_point_to_segment_with_t,
    geojson_linestring_to_coords,
)
from app.api.routing import _thin_waypoints, _bearings_for, WAYPOINT_SNAP_RADIUS_M
from app.schemas import StopOut


@dataclass
class Rec:
    route_id: str
    direction: str
    seq: int
    stop_id: str
    source: str
    offset_m: float
    progress_m: float | None
    backward: bool


def old_matcher(route_coords, stops):
    """The pre-fix algorithm: segment-index cursor, global search from cursor."""
    if len(route_coords) < 2:
        return [(sid, lat, lng, 0.0, "canonical_fallback", None) for sid, lat, lng in stops]
    n = len(route_coords) - 1
    seg_lens, cum = _cumulative_route_metrics(route_coords)
    cursor = 0
    out = []
    for sid, lat, lng in stops:
        best_dist = float("inf")
        best_pt = None
        best_idx = -1
        for i in range(cursor, n):
            pl, pr_, d, t = _project_point_to_segment_with_t(lat, lng, route_coords[i], route_coords[i + 1])
            if d < best_dist:
                best_dist, best_pt, best_idx = d, (pl, pr_), i
        if best_pt is not None and best_dist <= MAX_STOP_OFFSET_M:
            prog = cum[best_idx] + ((best_pt[0] - route_coords[best_idx][0]) * 0)  # placeholder
            # recompute t for progress:
            _, _, _, t = _project_point_to_segment_with_t(lat, lng, route_coords[best_idx], route_coords[best_idx + 1])
            prog = cum[best_idx] + t * seg_lens[best_idx]
            out.append((sid, best_pt[0], best_pt[1], best_dist, "projected", prog))
            cursor = best_idx
        else:
            out.append((sid, lat, lng, 0.0, "canonical_fallback", None))
    return out


def fetch_geometry(order):
    stops = [StopOut.model_validate(rs.stop) for rs in order]
    coords = _thin_waypoints(stops)
    if len(coords) < 2:
        return None, False
    bearings = _bearings_for(coords)
    radiuses = [WAYPOINT_SNAP_RADIUS_M] * len(coords)
    try:
        g = get_route_geometry(coords, bearings=bearings, radiuses=radiuses)
        return geojson_linestring_to_coords(g["geometry"]), True
    except OSRMError:
        try:
            g = get_route_geometry(coords)
            return geojson_linestring_to_coords(g["geometry"]), False
        except OSRMError:
            return None, False


def main():
    db = SessionLocal()
    routes = list(queries.get_active_routes(db))
    db.close()

    recs = []
    for route in routes:
        db = SessionLocal()
        try:
            rs_list = list(queries.get_route_stops(db, route.route_id))
        finally:
            db.close()
        if len(rs_list) < 2:
            continue
        seq_by_id = {r.stop_id: r.sequence_no for r in rs_list}
        for direction in (["forward"] + (["reverse"] if route.is_bidirectional else [])):
            order = list(reversed(rs_list)) if direction == "reverse" else list(rs_list)
            coords, _ = fetch_geometry(order)
            if coords is None:
                continue
            stops = [(r.stop_id, r.stop.lat, r.stop.lng) for r in order]
            result = old_matcher(coords, stops)
            prev = None
            for (sid, lat, lng, d, src, prog) in result:
                backward = False
                if src == "projected" and prog is not None and prev is not None and prog + 1.0 < prev:
                    backward = True
                recs.append(Rec(route.route_id, direction, seq_by_id[sid], sid, src, d, prog, backward))
                if prog is not None:
                    prev = prog

    proj = [r for r in recs if r.source == "projected"]
    bw = [r for r in recs if r.backward]
    print(f"Old algorithm over {len(recs)} placements: {len(bw)} backward projections")
    for r in sorted(bw, key=lambda r: (r.route_id, r.direction, r.seq))[:40]:
        print(f"  {r.route_id} {r.direction} seq={r.seq} {r.stop_id}: offset={r.offset_m:.1f}m")
    print(f"  ... total {len(bw)} backward, {len(proj)} projected total")


if __name__ == "__main__":
    main()