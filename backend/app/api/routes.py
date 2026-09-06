from typing import Literal
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session


from app.core.config import get_settings
from app.core.response_cache import cached_response
from app.db.session import get_db
from app.db import queries
from app.routing.osrm_client import get_route_geometry, OSRMError
from app.routing.stop_positioning import (
    compute_adjusted_stop_positions,
    geojson_linestring_to_coords,
)
from app.schemas import RouteListOut, RouteOut, RouteStopOut, StopOut

# Waypoint-thinning for OSRM requests is identical to what route-finder legs
# already do (see app/api/routing.py's docstring on MIN_WAYPOINT_SPACING_M
# for why: dense stops a few dozen metres apart can push OSRM into visible
# zigzagging just to legally hit each one in order). Reused rather than
# duplicated so the two endpoints can't drift out of sync.
from app.api.routing import _thin_waypoints, _bearings_for, WAYPOINT_SNAP_RADIUS_M

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routes", tags=["routes"])
settings = get_settings()


@router.get("", response_model=RouteListOut)
@cached_response(
    "routes", ttl_seconds=settings.ROUTES_CACHE_TTL_S, key_params=("q", "offset", "limit")
)
def list_routes(
    q: str | None = Query(None, description="Optional case-insensitive substring match on route_name"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Route browser listing -- lean (no stops payload); pair with
    GET /routes/{route_id}/stops to fetch a single route's ordered stops
    once the user picks one, rather than shipping every route's full stop
    list up front."""
    items = queries.list_routes(db, q=q, limit=limit, offset=offset)
    total = queries.count_routes(db, q=q)
    return RouteListOut(total=total, limit=limit, offset=offset, items=items)


@router.get("/{route_id}", response_model=RouteOut)
@cached_response("route_detail", ttl_seconds=settings.ROUTES_CACHE_TTL_S, key_params=("route_id",))
def read_route(route_id: str, db: Session = Depends(get_db)):
    route = queries.get_route(db, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Route '{route_id}' not found")
    return RouteOut.model_validate(route)


_DIRECTION_NOT_BIDIRECTIONAL_DETAIL = (
    "Route '{route_id}' is not bidirectional -- there is no reverse direction to show"
)


def _stops_in_travel_order(route_stops: list, direction: Literal["forward", "reverse"]) -> list:
    """route_stops (ascending sequence_no, as returned by
    queries.get_route_stops) reordered into actual travel order for
    `direction`. 'forward' is a no-op (that's what sequence_no already
    is); 'reverse' is the same physical stops, last-to-first -- the
    return leg of a bidirectional route."""
    return list(reversed(route_stops)) if direction == "reverse" else list(route_stops)


def _display_positions_for_direction(
    route_stops: list, direction: Literal["forward", "reverse"]
):
    """Route-aware adjusted (lat, lng) for each stop in `route_stops`,
    aligned 1:1 with `route_stops` in its given (ascending sequence_no)
    order regardless of `direction` -- callers don't need to re-flip
    anything to zip these back up with route_stops.

    Returns None (not a list of Nones) when no adjusted position could
    be computed for this direction at all -- fewer than 2 stops, or
    OSRM unreachable/erroring -- so callers can tell "nothing computed,
    fall back to canonical" apart from "computed, and it happens to
    fall back to canonical value for every stop" (the latter is a
    normal per-stop outcome, e.g. every stop hit CANONICAL_FALLBACK
    because MAX_STOP_OFFSET_M was exceeded).

    Uses each stop's *canonical* lat/lng as input (never a previously
    adjusted value) and never writes back to the Stop row -- this is a
    read-only, request-scoped computation, same as
    app/api/routing.py::_attach_road_geometry's road_geometry.
    """
    if len(route_stops) < 2:
        return None

    travel_order = _stops_in_travel_order(route_stops, direction)
    travel_stop_outs = [StopOut.model_validate(rs.stop) for rs in travel_order]

    coords = _thin_waypoints(travel_stop_outs)
    if len(coords) < 2:
        return None

    # Same resilience pattern as app/api/routing.py::_attach_road_geometry:
    # the bearing+radius constraint is deliberately tight
    # (WAYPOINT_SNAP_RADIUS_M), so it's expected to occasionally have no
    # matching edge within range even when an unconstrained request for
    # the same coordinates would succeed. Falling back to unconstrained
    # geometry here still gives correct *unadjusted-for-direction*
    # positions (better than nothing), rather than giving up on the whole
    # direction just because the tight constraint didn't find a match.
    bearings = _bearings_for(coords)
    radiuses = [WAYPOINT_SNAP_RADIUS_M] * len(coords)
    try:
        geometry = get_route_geometry(coords, bearings=bearings, radiuses=radiuses)
    except OSRMError as exc:
        logger.info(
            "stop_positioning: constrained OSRM call failed for route_stops "
            "(direction=%s), retrying unconstrained: %s", direction, exc,
        )
        try:
            geometry = get_route_geometry(coords)
        except OSRMError as exc2:
            logger.warning(
                "stop_positioning: OSRM call failed even unconstrained "
                "(direction=%s) -- no display positions computed, falling "
                "back to canonical stop coordinates: %s", direction, exc2,
            )
            return None

    route_coords = geojson_linestring_to_coords(geometry["geometry"])
    # Every physical stop is projected, not just the (possibly thinned)
    # OSRM waypoints -- the LineString is continuous, so a stop that got
    # thinned out of the waypoint list for OSRM's sake still projects
    # cleanly onto it.
    stops_for_projection = [(rs.stop_id, rs.stop.lat, rs.stop.lng) for rs in travel_order]
    positions_travel_order = compute_adjusted_stop_positions(route_coords, stops_for_projection)

    # Un-flip back to route_stops' own (ascending sequence_no) order.
    # Zipped by position, not stop_id, so a loop route revisiting the
    # same physical stop at two different sequence_nos (see
    # app/routing/graph_builder.py's NY-03 example) still gets two
    # independent positions rather than colliding on one.
    if direction == "reverse":
        positions_travel_order = list(reversed(positions_travel_order))
    return positions_travel_order


@router.get("/{route_id}/stops", response_model=list[RouteStopOut])
@cached_response(
    "route_stops", ttl_seconds=settings.ROUTES_CACHE_TTL_S, key_params=("route_id", "direction")
)
def read_route_stops(
    route_id: str,
    direction: Literal["forward", "reverse"] = Query(
        "forward",
        description=(
            "Which travel direction to compute each stop's route-aware "
            "display position for -- e.g. the correct side of a divided "
            "road, or the correct approach/departure leg at an "
            "intersection, for a vehicle travelling this way. 'reverse' "
            "is only valid for a bidirectional route (422 otherwise). "
            "This never changes the list's order (still ascending "
            "sequence_no) or `stop.lat/lng` (always the canonical "
            "coordinate) -- only `display_lat`/`display_lng`."
        ),
    ),
    db: Session = Depends(get_db),
):
    route = queries.get_route(db, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Route '{route_id}' not found")
    if direction == "reverse" and not route.is_bidirectional:
        raise HTTPException(
            status_code=422,
            detail=_DIRECTION_NOT_BIDIRECTIONAL_DETAIL.format(route_id=route_id),
        )

    route_stops = queries.get_route_stops(db, route_id)
    positions = _display_positions_for_direction(route_stops, direction)

    return [
        RouteStopOut(
            sequence_no=rs.sequence_no,
            stop=StopOut.model_validate(rs.stop),
            display_lat=(positions[i].lat if positions is not None else None),
            display_lng=(positions[i].lng if positions is not None else None),
        )
        for i, rs in enumerate(route_stops)
    ]


# Geometry is cached separately (own namespace, longer-lived) from the
# other two: it's a round trip to OSRM rather than a DB read, so a cache
# hit here saves far more than the DB-backed ones, and a route's stop
# sequence changes far less often than, say, its status -- there's no
# reason to invalidate it on the same schedule as read_route/read_route_stops.
@router.get("/{route_id}/geometry")
@cached_response(
    "route_geometry", ttl_seconds=settings.ROUTES_CACHE_TTL_S, key_params=("route_id", "direction")
)
def read_route_geometry(
    route_id: str,
    direction: Literal["forward", "reverse"] = Query(
        "forward",
        description=(
            "Which travel direction's road geometry to return. 'reverse' "
            "is only valid for a bidirectional route (422 otherwise) and "
            "is computed from a separate OSRM call over the reversed stop "
            "order -- not the forward geometry reversed -- so it can "
            "follow the correct carriageway of a divided road."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Road-following geometry for a route's full stop sequence, via OSRM
    driving directions through every stop in ride order (thinned the same
    way route-finder legs are). Used by the frontend's "browse a route on
    the map" overlay so it draws actual roads instead of straight lines
    between consecutive stops. Same response shape as GET /walking-route --
    a plain {geometry, distance_m, duration_s} dict, not a schema, since
    it's a direct passthrough of OSRM's own result."""
    route = queries.get_route(db, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Route '{route_id}' not found")
    if direction == "reverse" and not route.is_bidirectional:
        raise HTTPException(
            status_code=422,
            detail=_DIRECTION_NOT_BIDIRECTIONAL_DETAIL.format(route_id=route_id),
        )

    route_stops = queries.get_route_stops(db, route_id)
    if len(route_stops) < 2:
        raise HTTPException(
            status_code=422, detail=f"Route '{route_id}' has fewer than 2 stops"
        )

    travel_order = _stops_in_travel_order(route_stops, direction)
    stops = [StopOut.model_validate(rs.stop) for rs in travel_order]
    coords = _thin_waypoints(stops)

    try:
        return get_route_geometry(
            coords,
            bearings=_bearings_for(coords),
            radiuses=[WAYPOINT_SNAP_RADIUS_M] * len(coords),
        )
    except OSRMError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't compute route geometry: {exc}")