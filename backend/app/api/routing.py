import math
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from typing import Optional

from app.routing.osrm_client import get_route_geometry, OSRMError
from app.routing.graph_builder import haversine_distance_m
from app.routing.time_buckets import day_and_bucket_for, now_in_nepal
from app.db.session import get_db, SessionLocal
from app.db import queries
from app.routing.pathfinder import (
    find_shortest_path,
    find_route_via_stops,
    NoRouteFoundError,
    PathSegment,
    RouteAlternative as PFRouteAlternative,
)
from app.schemas import FareOut, RouteAlternative, RouteFinderResult, RouteLeg, StopOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["route-finder"])

# Stops closer together than this are treated as effectively the same
# point for OSRM waypoint purposes. Forcing OSRM through every single
# stored stop — including ones only a few dozen metres apart — can push
# it into small loops on one-way streets just to legally hit each
# waypoint in order, which shows up as visible zigzagging on the map.
# Thinning keeps the route recognisable while giving OSRM room to pick
# a sane path between waypoints that are meaningfully far apart.
MIN_WAYPOINT_SPACING_M = 80

# How far OSRM is allowed to look, per waypoint, for a road segment
# matching the bearing hint below. Keeps a heading constraint from ever
# pushing a snap out to some distant edge that just happens to match --
# if nothing satisfying both is within range, OSRM errors, which
# _attach_road_geometry already handles by falling back (see its
# `except OSRMError: pass`), so tightening this can only fail safely.
#
# Tuned against the full live dataset: stops in Kathmandu routinely sit
# 50-80m from the road centreline OSRM routes on (real measured offsets
# on 113 routes / 396 stops), and OSRM rejects the whole constrained
# request with NoSegment as soon as ANY waypoint exceeds the radius.
# At 50m that rejected ~85% of route directions outright (measured:
# 222/223) -- silently discarding the bearing protection for the entire
# direction. At 150m the same constrained call succeeds for ~94% of
# directions (209/223) while the post-projection trust bound
# (MAX_STOP_OFFSET_M=100m in app/routing/stop_positioning.py) still
# governs whether any individual projection is actually shown. The
# remaining ~6% fall through to the unconstrained retry as before.
WAYPOINT_SNAP_RADIUS_M = 150

# Tolerance either side of the computed heading. Wide enough to allow a
# gently curving road, tight enough to reject the opposite-direction
# carriageway of a divided road (roughly 180 degrees off).
BEARING_RANGE_DEG = 30


def _thin_waypoints(stops: list[StopOut]) -> list[tuple[float, float]]:
    if len(stops) < 2:
        return [(s.lat, s.lng) for s in stops]

    thinned = [(stops[0].lat, stops[0].lng)]
    for stop in stops[1:-1]:
        last_lat, last_lng = thinned[-1]
        if haversine_distance_m(last_lat, last_lng, stop.lat, stop.lng) >= MIN_WAYPOINT_SPACING_M:
            thinned.append((stop.lat, stop.lng))
    # Always keep the true final stop, even if it's close to the last
    # kept waypoint — it's the actual alight point, not optional.
    last = stops[-1]
    if thinned[-1] != (last.lat, last.lng):
        thinned.append((last.lat, last.lng))
    return thinned


def _bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Forward azimuth in degrees (0-360, clockwise from true north) from
    point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lng2 - lng1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _bearings_for(
    coords: list[tuple[float, float]]
) -> list[tuple[float, int] | None] | None:
    """Per-waypoint (bearing, range) hints for OSRM's `bearings` param,
    derived purely from the waypoints' own travel order in `coords` --
    no stored heading or extra survey data needed. Each waypoint is
    constrained to road segments travelling in roughly its direction of
    travel, so on a divided road (the two carriageways mapped as
    separate OSM ways) OSRM snaps to the one actually being travelled
    instead of whichever happens to be geometrically nearest.

    On an undivided road there's only one edge to snap to either way,
    so this never changes anything there -- it only ever resolves an
    ambiguity that existed, never introduces one.

    Returns None for a single-coordinate input (nothing to compute a
    direction from); a leg that short never reaches OSRM anyway (see
    the `len(coords) < 2` guard in _attach_road_geometry).
    """
    if len(coords) < 2:
        return None
    bearings: list[tuple[float, int] | None] = []
    for i, (lat, lng) in enumerate(coords):
        if i < len(coords) - 1:
            b = _bearing_deg(lat, lng, *coords[i + 1])
        else:
            # Last waypoint has no "next" point to aim at -- reuse the
            # heading of the final approach instead of leaving it
            # unconstrained.
            b = _bearing_deg(*coords[i - 1], lat, lng)
        bearings.append((b, BEARING_RANGE_DEG))
    return bearings


def _attach_road_geometry(legs: list[RouteLeg]) -> None:
    for leg in legs:
        # Walking transfers get the foot profile; bus rides get the
        # (default) driving profile. Both are real OSRM road/path
        # distances -- neither is the straight-line haversine figure
        # used for graph edge weights during pathfinding.
        profile = "foot" if leg.route_id == "TRANSFER" else "driving"

        coords = _thin_waypoints(leg.stops)
        if len(coords) < 2:
            continue

        # Bearing/radius constraints are scoped to the driving profile:
        # the divided-road wrong-carriageway problem is a vehicle-road
        # thing, foot paths aren't generally split into directional
        # ways, and these are short walking transfers where there's
        # little to gain from narrowing the snap.
        if profile == "driving":
            bearings = _bearings_for(coords)
            radiuses = [WAYPOINT_SNAP_RADIUS_M] * len(coords)
        else:
            bearings = None
            radiuses = None

        try:
            leg.road_geometry = get_route_geometry(
                coords, profile=profile, bearings=bearings, radiuses=radiuses
            )
            continue
        except OSRMError as exc:
            if bearings is None:
                # Unconstrained request already failed -- nothing looser
                # left to try (genuine OSRM/network failure, or these
                # coordinates truly have no matchable road nearby).
                logger.warning(
                    "OSRM road_geometry failed for leg %s (%s -> %s), "
                    "falling back to a straight line: %s",
                    leg.route_id, leg.board_stop.stop_id, leg.alight_stop.stop_id, exc,
                )
                continue
            logger.info(
                "OSRM road_geometry failed under bearing/radius constraints for "
                "leg %s (%s -> %s), retrying unconstrained: %s",
                leg.route_id, leg.board_stop.stop_id, leg.alight_stop.stop_id, exc,
            )

        # Constrained attempt failed -- most often this means the
        # WAYPOINT_SNAP_RADIUS_M=50m radius didn't have a bearing-
        # matching edge nearby, not that no route exists at all. A
        # straight-line fallback for the whole leg is a worse outcome
        # than the rare case this constraint exists to prevent (snapping
        # to the wrong carriageway of a divided road), so retry once
        # fully unconstrained before giving up.
        try:
            leg.road_geometry = get_route_geometry(coords, profile=profile)
        except OSRMError as exc:
            logger.warning(
                "OSRM road_geometry failed for leg %s (%s -> %s) even "
                "unconstrained, falling back to a straight line: %s",
                leg.route_id, leg.board_stop.stop_id, leg.alight_stop.stop_id, exc,
            )


def _total_road_distance_m(legs: list[RouteLeg]) -> float | None:
    """Sums each leg's real OSRM road_geometry distance. Returns None
    (rather than a partial figure) if any leg is missing road_geometry
    -- e.g. OSRM was briefly unreachable -- so callers can fall back to
    the graph's haversine-based total_distance_m instead of silently
    under-reporting."""
    total = 0.0
    for leg in legs:
        if leg.road_geometry is None:
            return None
        total += leg.road_geometry["distance_m"]
    return total


def _record_leg_congestion(legs: list[RouteLeg]) -> None:
    """Runs after the response is already sent (see BackgroundTasks in
    find_route below), so this never adds latency to a user's search. Each
    ride leg with real OSRM road_geometry becomes one upsert into
    segment_congestion_stats, bucketed by "now" in Nepal time -- an
    approximation of when the underlying trip would actually happen, good
    enough for an aggregate stat that only needs day-of-week/3-hour
    resolution.

    Opens its own DB session rather than reusing the request's, since the
    request's session is closed by the get_db dependency once the response
    finishes -- background tasks run after that.
    """
    day_of_week, hour_bucket = day_and_bucket_for(now_in_nepal())
    db = SessionLocal()
    try:
        for leg in legs:
            if leg.route_id == "TRANSFER" or leg.road_geometry is None:
                continue
            queries.record_congestion_sample(
                db,
                route_id=leg.route_id,
                from_stop_id=leg.board_stop.stop_id,
                to_stop_id=leg.alight_stop.stop_id,
                day_of_week=day_of_week,
                hour_bucket=hour_bucket,
                duration_s=leg.road_geometry["duration_s"],
                distance_m=leg.road_geometry["distance_m"],
            )
        db.commit()
    finally:
        db.close()


def _build_legs(
    segments: list[PathSegment],
    route_names: dict[str, str],
    stop_map: dict[str, StopOut],
) -> list[RouteLeg]:
    """Shared segment -> leg consolidation, used for both the primary
    result and each alternative. All stops are pre-fetched in stop_map
    so no DB access is needed here."""

    legs: list[RouteLeg] = []
    for seg in segments:
        seg_route_id = seg.route_id or "TRANSFER"
        to_stop = stop_map[seg.to_stop_id]
        if legs and legs[-1].route_id == seg_route_id:
            legs[-1].alight_stop = to_stop
            legs[-1].num_ride_segments += 1
            legs[-1].stops.append(to_stop)
        else:
            from_stop = stop_map[seg.from_stop_id]
            legs.append(
                RouteLeg(
                    route_id=seg_route_id,
                    route_name=(
                        "Transfer (walk)"
                        if seg.is_transfer
                        else route_names.get(seg_route_id, seg_route_id)
                    ),
                    board_stop=from_stop,
                    alight_stop=to_stop,
                    num_ride_segments=1,
                    stops=[from_stop, to_stop],
                )
            )
    return legs


def _all_route_ids(
    primary_segments: list[PathSegment],
    alternatives: list[PFRouteAlternative],
) -> list[str]:
    ids: set[str] = set()
    for seg in primary_segments:
        if seg.route_id:
            ids.add(seg.route_id)
    for alt in alternatives:
        for seg in alt.segments:
            if seg.route_id:
                ids.add(seg.route_id)
    return list(ids)


@router.get("/walking-route")
def walking_route(
    from_lat: float = Query(..., ge=-90, le=90),
    from_lng: float = Query(..., ge=-180, le=180),
    to_lat: float = Query(..., ge=-90, le=90),
    to_lng: float = Query(..., ge=-180, le=180),
):
    """Foot-profile OSRM route from an arbitrary point (e.g. the user's
    detected location) to a stop. Separate from route-finder's driving-profile
    _attach_road_geometry since this is a single pedestrian leg, not a ride.
    """
    try:
        geometry = get_route_geometry([(from_lat, from_lng), (to_lat, to_lng)], profile="foot")
    except OSRMError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't compute walking route: {exc}")
    return geometry


@router.get("/route-finder", response_model=RouteFinderResult)
def find_route(
    background_tasks: BackgroundTasks,
    origin: str = Query(..., description="Origin stop_id, e.g. S0198"),
    destination: str = Query(..., description="Destination stop_id, e.g. S0021"),
    avoid_congestion: bool = Query(
        False,
        description=(
            "When true, the transfer-search fallback (used only if no direct "
            "route exists) weights ride segments by current historical "
            "congestion instead of raw distance alone. Direct routes are "
            "unaffected -- a direct route always wins either way."
        ),
    ),
    include_alternatives: bool = Query(
        False,
        description=(
            "When true, up to 2 additional options are returned in "
            "`alternatives` alongside the primary result -- see "
            "RouteAlternative's docstring in app/schemas.py for what each "
            "label means. Each alternative is a genuinely different "
            "physical path (deduplicated by stop sequence, not just by "
            "route_id -- two route numbers covering the identical "
            "corridor don't count as separate options) and now carries "
            "its own real OSRM road_geometry, same as the primary "
            "result. Ignored (alternatives are always empty) when `via` "
            "is given -- see `via`'s description."
        ),
    ),
    max_transfers: Optional[int] = Query(
        None,
        ge=0,
        le=10,
        description=(
            "Maximum number of transfers (bus changes) allowed. "
            "0 = direct route only, 1 = one transfer, etc. "
            "Only applies when no direct route exists and Dijkstra "
            "fallback is used. Default is unlimited."
        ),
    ),
    via: list[str] = Query(
        [],
        description=(
            "Zero or more intermediate stop_ids the trip must pass "
            "through, in order, between origin and destination (e.g. "
            "?via=S0042&via=S0107). Each origin->via->...->destination "
            "leg is solved independently and chained together, the same "
            "way an \"add a stop\" feature works in a driving-directions "
            "app. `include_alternatives` is ignored when this is set -- "
            "chaining per-leg alternatives would combinatorially explode "
            "into a confusing number of whole-trip options."
        ),
    ),
    db: Session = Depends(get_db),
):
    # --- STEP 1: All DB work inside the session ---
    try:
        if via:
            result = find_route_via_stops(
                db, [origin, *via, destination], avoid_congestion=avoid_congestion, max_transfers=max_transfers
            )
        else:
            result = find_shortest_path(
                db,
                origin,
                destination,
                avoid_congestion=avoid_congestion,
                include_alternatives=include_alternatives,
                max_transfers=max_transfers,
            )
    except NoRouteFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Collect all stop_ids needed for this result + alternatives
    all_route_ids = _all_route_ids(result.segments, result.alternatives)
    route_names = queries.get_route_names(db, all_route_ids)

    # Build stop_map with all unique stops referenced by primary + alternatives
    needed_stop_ids: set[str] = set()
    for seg in result.segments:
        needed_stop_ids.add(seg.from_stop_id)
        needed_stop_ids.add(seg.to_stop_id)
    for alt in result.alternatives:
        for seg in alt.segments:
            needed_stop_ids.add(seg.from_stop_id)
            needed_stop_ids.add(seg.to_stop_id)

    stop_map: dict[str, StopOut] = {}
    for stop_id in needed_stop_ids:
        stop = queries.get_stop(db, stop_id)
        if stop is not None:
            stop_map[stop_id] = StopOut.model_validate(stop)

    # Build legs with pre-fetched stops (no DB access in _build_legs)
    legs = _build_legs(result.segments, route_names, stop_map)

    # --- STEP 2: Session closes here (FastAPI dependency) ---
    # --- STEP 3: OSRM calls outside DB session ---
    _attach_road_geometry(legs)
    background_tasks.add_task(_record_leg_congestion, legs)

    # Prefer the real road distance OSRM returned for these legs over
    # result.total_distance_m, which is a sum of straight-line
    # (haversine) edge weights from the routing graph -- fine for
    # ranking candidate paths during search, but a systematic
    # under-estimate of how far the bus/walk actually travels, since
    # roads curve and rarely follow the great-circle line between two
    # stops. Only used when every leg has road_geometry; otherwise
    # fall back so a single OSRM hiccup doesn't blank out the figure.
    total_distance_m = _total_road_distance_m(legs)
    if total_distance_m is None:
        total_distance_m = result.total_distance_m

    alternatives: list[RouteAlternative] = []
    for alt in result.alternatives:
        alt_legs = _build_legs(alt.segments, route_names, stop_map)
        _attach_road_geometry(alt_legs)
        alternatives.append(
            RouteAlternative(
                label=alt.label,
                total_cost=alt.total_distance_m,
                transfer_count=alt.transfer_count,
                legs=alt_legs,
            )
        )

    fare_rule = queries.fare_for_distance(db, total_distance_m / 1000)
    fare = FareOut.model_validate(fare_rule) if fare_rule is not None else None

    return RouteFinderResult(
        origin_stop_id=origin,
        destination_stop_id=destination,
        via_stop_ids=via,
        total_cost=total_distance_m,
        transfer_count=result.transfer_count,
        legs=legs,
        fare=fare,
        alternatives=alternatives,
    )