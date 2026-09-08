"""
routing/stop_positioning.py

Route-aware / dynamic stop positioning.

Distinct from the bearing/radius constraints in app/api/routing.py
(_bearings_for, WAYPOINT_SNAP_RADIUS_M): those only affect OSRM's own
snapping *inside a single /route call* -- they don't produce a reusable
"this stop, on this route, in this direction, displays here" position,
and they derive a heading from straight lines between raw stop
coordinates rather than from the route's actual road geometry. That's
enough to fix which carriageway a single OSRM leg snaps to, but it says
nothing about where a *stop marker* should sit on the map for a given
route + direction, or how it should behave near an intersection.

This module is a pure function of:
  - a route's own OSRM road_geometry (the same {"type": "LineString",
    "coordinates": [[lng, lat], ...]} dict returned by
    app/routing/osrm_client.py::get_route_geometry, already following
    the correct carriageway for whichever direction's stop order the
    bearings passed to that call were derived from -- see
    app/api/routing.py::_bearings_for), and
  - the route's stops, canonical (stop_id, lat, lng), in the same
    direction-of-travel order as that geometry,
and produces one adjusted (lat, lng) per stop: the point on that
specific LineString closest to the stop's canonical coordinate, found
by walking forward along the path rather than a global nearest-segment
search.

Walking forward (never backward) is what avoids the two failure modes a
naive nearest-point search falls into:
  - snapping to a different, closer road (a crossing street at an
    intersection, a parallel carriageway going the other way), and
  - snapping "backward" onto an earlier part of a route that loops
    back and passes close by again later.
Because the search cursor only ever advances, each stop is matched
against whichever segment of road the route is *actually on* at that
point in the journey -- which is what puts a stop on the correct
approach/departure leg at an intersection, and on the correct
carriageway of a divided road (the forward and reverse geometries are
different LineStrings to begin with, since they come from two separate
OSRM calls with opposite bearings -- see app/api/routes.py).

This module never reads canonical stop coordinates from anywhere other
than its own arguments, and never writes anything -- Stop.lat/lng in
the database is untouched by this file. Callers (app/api/routes.py)
decide what to do with the result; today that's "attach it to the API
response for the map to draw", computed fresh per request and riding on
get_route_geometry's own cache, not persisted to a new table.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.routing.graph_builder import haversine_distance_m

# A projected position further than this from the stop's canonical
# coordinate is treated as untrustworthy (the route geometry doesn't
# actually run near this stop -- bad/incomplete OSM data, a bad OSRM
# match, or a stop that's genuinely not right on the route's road) and
# the canonical coordinate is used unchanged instead. Increased from 60m
# to 100m to account for GPS accuracy in Kathmandu (10-30m) plus OSRM
# snap error. WAYPOINT_SNAP_RADIUS_M in app/api/routing.py is 50m for
# OSRM waypoint snapping; this is for post-hoc projection validation.
MAX_STOP_OFFSET_M = 100.0


@dataclass(frozen=True)
class AdjustedStopPosition:
    stop_id: str
    lat: float
    lng: float
    offset_m: float
    source: str  # "projected" | "canonical_fallback"


def geojson_linestring_to_coords(geometry: dict) -> list[tuple[float, float]]:
    """Convert a GeoJSON LineString (as returned in
    get_route_geometry(...)["geometry"], coordinates in [lng, lat] order
    per the GeoJSON spec) into a list of (lat, lng) tuples in path
    order, which is what this module and haversine_distance_m expect."""
    coordinates = geometry.get("coordinates") or []
    return [(lat, lng) for lng, lat in coordinates]


def _project_point_to_segment(
    lat: float, lng: float, seg_start: tuple[float, float], seg_end: tuple[float, float]
) -> tuple[float, float, float]:
    """
    Project (lat, lng) onto the segment [seg_start, seg_end], clamped to
    the segment (not the infinite line through it).

    Uses a local equirectangular approximation (flat-earth, longitude
    scaled by cos(latitude)) -- accurate to well under a meter of error
    at the segment lengths involved in stop-level positioning, and far
    cheaper than a true geodesic projection.

    Returns (proj_lat, proj_lng, distance_m), distance_m being the
    haversine distance from (lat, lng) to the projected point.
    """
    lat0 = seg_start[0]
    coslat = max(0.1, math.cos(math.radians(lat0)))

    def to_xy(pt_lat: float, pt_lng: float) -> tuple[float, float]:
        return (pt_lng - seg_start[1]) * coslat, pt_lat - seg_start[0]

    px, py = to_xy(lat, lng)
    ex, ey = to_xy(*seg_end)

    seg_len_sq = ex * ex + ey * ey
    if seg_len_sq == 0.0:
        proj_lat, proj_lng = seg_start
    else:
        t = max(0.0, min(1.0, (px * ex + py * ey) / seg_len_sq))
        proj_lng = seg_start[1] + (ex * t) / coslat
        proj_lat = seg_start[0] + ey * t

    return proj_lat, proj_lng, haversine_distance_m(lat, lng, proj_lat, proj_lng)


def compute_adjusted_stop_positions(
    route_coords: list[tuple[float, float]],
    stops: list[tuple[str, float, float]],
) -> list[AdjustedStopPosition]:
    """
    route_coords: the route's road geometry as (lat, lng) points in
    path order (see geojson_linestring_to_coords).
    stops: (stop_id, lat, lng) in the same direction-of-travel order as
    route_coords -- e.g. a route's stored forward sequence paired with
    its forward-direction geometry, or that same stop list reversed,
    paired with a *separately requested* reverse-direction geometry.
    Returns one AdjustedStopPosition per stop, same order as `stops`.

    Each stop is matched by a global nearest-point search over the
    entire geometry, not a windowed local one -- a fixed distance
    window (whether constant or scaled off straight-line stop spacing)
    can't reliably bound how far apart two consecutive stops end up
    along the actual road: one-way systems, medians, and loops can
    inflate the real path length well past what stop-to-stop straight-
    line distance would predict (e.g. a real ~215m gap between two
    stops on this router's test route needing ~644m of actual road to
    connect them). A global search finds the true nearest point
    regardless of that ratio.

    The cursor still only ever advances (this module never snaps a
    stop backward onto an earlier part of a route that loops back and
    passes close by again later, or onto a crossing street) -- but
    "ahead" is now determined by comparing the *found* global match's
    index against the cursor, rather than by only searching within
    some limited window ahead of it. A stop whose true nearest point
    is behind the cursor gets no match and falls back to canonical,
    same as before.
    """
    if len(route_coords) < 2:
        return [
            AdjustedStopPosition(stop_id, lat, lng, 0.0, "canonical_fallback")
            for stop_id, lat, lng in stops
        ]
    results: list[AdjustedStopPosition] = []
    cursor = 0
    n_segments = len(route_coords) - 1
    for stop_id, lat, lng in stops:
        best_dist = float("inf")
        best_point: tuple[float, float] | None = None
        best_index = cursor
        for i in range(cursor, n_segments):
            seg_start, seg_end = route_coords[i], route_coords[i + 1]
            proj_lat, proj_lng, dist_m = _project_point_to_segment(lat, lng, seg_start, seg_end)
            if dist_m < best_dist:
                best_dist, best_point, best_index = dist_m, (proj_lat, proj_lng), i
        if best_point is not None and best_dist <= MAX_STOP_OFFSET_M:
            results.append(
                AdjustedStopPosition(stop_id, best_point[0], best_point[1], best_dist, "projected")
            )
            cursor = best_index  # monotonic: never search backward for the next stop
        else:
            results.append(AdjustedStopPosition(stop_id, lat, lng, 0.0, "canonical_fallback"))
            # cursor deliberately left in place -- no confirmed progress for this stop
    return results
