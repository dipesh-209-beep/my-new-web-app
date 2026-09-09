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

# Grace applied to the monotonic-route-progress invariant
# progress(stop[i+1]) >= progress(stop[i]) - tolerance.  Letting the
# cursor tolerate a few metres of backwardness absorbs GPS noise and
# stops recorded a couple of metres out of strict road order along an
# otherwise-straight road, without ever allowing a true same-segment
# backstep (those are typically tens to hundreds of metres).
MONOTONIC_POSITION_TOLERANCE_M = 5.0

# Distance (m) within which two projection candidates for the FIRST stop
# are treated as equally good, so the one closest to the start of the
# route geometry wins. OSRM always begins a route's geometry at its first
# waypoint, so a normal route has exactly one candidate near progress ~0
# for its first stop. A tie within this epsilon is thus the fingerprint
# of a closed loop -- a route whose geometry end and start coincide at
# (approximately) the same physical point (e.g. the ring road, which
# records the same stop as both first and last). Without the tie-break,
# the nearest candidate is equally the loop's end, and the progress
# cursor jumps straight to the far end of the polyline, stranding every
# later stop behind it (measured: 94% of a 36-stop ring-road loop fell
# back to canonical). Anchoring the first stop at the loop's start keeps
# the cursor at the true beginning of travel; the loop-closing repeated
# stop then naturally projects at the far end on its own re-appearance.
FIRST_STOP_TIE_EPS_M = 10.0


@dataclass(frozen=True)
class AdjustedStopPosition:
    stop_id: str
    lat: float
    lng: float
    offset_m: float
    source: str  # "projected" | "canonical_fallback"
    # Cumulative route distance (m) from the start of the direction's
    # geometry to the accepted projection point. None for canonical
    # fallbacks (the canonical coordinate isn't on the geometry). Set by
    # the matcher, not read by any caller today -- but it makes the
    # monotonic-progress invariant auditable (used by validation scripts
    # and the test suite) without re-deriving the projection.
    progress_m: float | None = None


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
    proj_lat, proj_lng, _, distance_m = _project_point_to_segment_with_t(
        lat, lng, seg_start, seg_end
    )
    return proj_lat, proj_lng, distance_m


def _project_point_to_segment_with_t(
    lat: float, lng: float, seg_start: tuple[float, float], seg_end: tuple[float, float]
) -> tuple[float, float, float, float]:
    """
    Like _project_point_to_segment, but also returns the clamped
    interpolation fraction t in [0, 1] along the segment, which callers
    need to combine with per-segment cumulative distances to compute a
    monotonic route-progress value.
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
        t = 0.0
    else:
        t = max(0.0, min(1.0, (px * ex + py * ey) / seg_len_sq))
        proj_lng = seg_start[1] + (ex * t) / coslat
        proj_lat = seg_start[0] + ey * t

    return proj_lat, proj_lng, haversine_distance_m(lat, lng, proj_lat, proj_lng), t


def _cumulative_route_metrics(
    route_coords: list[tuple[float, float]],
) -> tuple[list[float], list[float]]:
    """Precompute per-segment haversine lengths and the cumulative distance
    at each vertex, so callers can turn (segment_index, t) into an absolute
    route-progress distance in O(1).

    Returns (segment_lengths, vertex_cumulative) where
    vertex_cumulative[i] is the distance from the route start to vertex i,
    length n_segments+1, and segment_lengths[i] is the length of the
    segment between vertices i and i+1."""
    n_segments = len(route_coords) - 1
    segment_lengths: list[float] = []
    vertex_cumulative = [0.0] * (n_segments + 1)
    for i in range(n_segments):
        (lat0, lng0) = route_coords[i]
        (lat1, lng1) = route_coords[i + 1]
        d = haversine_distance_m(lat0, lng0, lat1, lng1)
        segment_lengths.append(d)
        vertex_cumulative[i + 1] = vertex_cumulative[i] + d
    return segment_lengths, vertex_cumulative


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

    Monotonicity is enforced with a route-progress cursor, not a
    segment-index one. A segment index alone cannot distinguish two
    projections that both fall on the same segment: stop A at 80% along
    segment N and stop B at 20% along that same segment N would both
    resolve "at or past" a segment-index cursor of N -- yet B is
    physically BEHIND A on the road. Instead, the cursor tracks the
    cumulative route distance (segment length once, precomputed) of the
    most recently accepted projection, and a candidate for the next
    stop is only eligible if its cumulative progress is at least
    (cursor - MONOTONIC_POSITION_TOLERANCE_M). This guarantees
    progress(stop[i+1]) >= progress(stop[i]) - tolerance for stops in
    travel order, which is what actually keeps markers from walking
    backward along a road, across an intersection, or onto a repeated/
    looped section of the route.

    A stop whose nearest eligible point is still within
    MAX_STOP_OFFSET_M gets projected and advances the cursor; one whose
    nearest eligible point is too far away (route doesn't actually pass
    near it at or after the cursor) falls back to its canonical
    coordinate and leaves the cursor untouched.
    """
    if len(route_coords) < 2:
        return [
            AdjustedStopPosition(stop_id, lat, lng, 0.0, "canonical_fallback")
            for stop_id, lat, lng in stops
        ]

    segment_lengths, vertex_cumulative = _cumulative_route_metrics(route_coords)
    n_segments = len(route_coords) - 1

    results: list[AdjustedStopPosition] = []
    cursor_m = 0.0  # cumulative route distance of the last accepted projection
    first_stop = True
    for stop_id, lat, lng in stops:
        best_dist = float("inf")
        best_point: tuple[float, float] | None = None
        best_progress = 0.0
        for i in range(n_segments):
            seg_start, seg_end = route_coords[i], route_coords[i + 1]
            proj_lat, proj_lng, dist_m, t = _project_point_to_segment_with_t(
                lat, lng, seg_start, seg_end
            )
            progress_m = vertex_cumulative[i] + t * segment_lengths[i]
            if progress_m < cursor_m - MONOTONIC_POSITION_TOLERANCE_M:
                continue  # behind the last accepted projection -- not eligible
            if dist_m < best_dist - FIRST_STOP_TIE_EPS_M:
                best_dist, best_point, best_progress = (
                    dist_m,
                    (proj_lat, proj_lng),
                    progress_m,
                )
            elif (
                first_stop
                and best_point is not None
                and abs(dist_m - best_dist) <= FIRST_STOP_TIE_EPS_M
                and progress_m < best_progress
            ):
                # Within epsilon of the best distance -- keep the candidate
                # closest to the start of the route geometry (see the
                # FIRST_STOP_TIE_EPS_M comment about closed loops).
                best_point, best_progress = (proj_lat, proj_lng), progress_m
        if best_point is not None and best_dist <= MAX_STOP_OFFSET_M:
            results.append(
                AdjustedStopPosition(
                    stop_id,
                    best_point[0],
                    best_point[1],
                    best_dist,
                    "projected",
                    best_progress,
                )
            )
            cursor_m = best_progress
        else:
            results.append(AdjustedStopPosition(stop_id, lat, lng, 0.0, "canonical_fallback"))
            # cursor deliberately left in place -- no confirmed progress for this stop
        first_stop = False
    return results
