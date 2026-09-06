from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session


from app.core.config import get_settings
from app.core.response_cache import cached_response
from app.db.session import get_db
from app.db import queries
from app.routing.osrm_client import get_route_geometry, OSRMError
from app.schemas import RouteListOut, RouteOut, RouteStopOut, StopOut

# Waypoint-thinning for OSRM requests is identical to what route-finder legs
# already do (see app/api/routing.py's docstring on MIN_WAYPOINT_SPACING_M
# for why: dense stops a few dozen metres apart can push OSRM into visible
# zigzagging just to legally hit each one in order). Reused rather than
# duplicated so the two endpoints can't drift out of sync.
from app.api.routing import _thin_waypoints, _bearings_for, WAYPOINT_SNAP_RADIUS_M

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


@router.get("/{route_id}/stops", response_model=list[RouteStopOut])
@cached_response("route_stops", ttl_seconds=settings.ROUTES_CACHE_TTL_S, key_params=("route_id",))
def read_route_stops(route_id: str, db: Session = Depends(get_db)):
    if queries.get_route(db, route_id) is None:
        raise HTTPException(status_code=404, detail=f"Route '{route_id}' not found")
    route_stops = queries.get_route_stops(db, route_id)
    return [RouteStopOut.model_validate(rs) for rs in route_stops]


# Geometry is cached separately (own namespace, longer-lived) from the
# other two: it's a round trip to OSRM rather than a DB read, so a cache
# hit here saves far more than the DB-backed ones, and a route's stop
# sequence changes far less often than, say, its status -- there's no
# reason to invalidate it on the same schedule as read_route/read_route_stops.
@router.get("/{route_id}/geometry")
@cached_response("route_geometry", ttl_seconds=settings.ROUTES_CACHE_TTL_S, key_params=("route_id",))
def read_route_geometry(route_id: str, db: Session = Depends(get_db)):
    """Road-following geometry for a route's full stop sequence, via OSRM
    driving directions through every stop in ride order (thinned the same
    way route-finder legs are). Used by the frontend's "browse a route on
    the map" overlay so it draws actual roads instead of straight lines
    between consecutive stops. Same response shape as GET /walking-route --
    a plain {geometry, distance_m, duration_s} dict, not a schema, since
    it's a direct passthrough of OSRM's own result."""
    if queries.get_route(db, route_id) is None:
        raise HTTPException(status_code=404, detail=f"Route '{route_id}' not found")

    route_stops = queries.get_route_stops(db, route_id)
    if len(route_stops) < 2:
        raise HTTPException(
            status_code=422, detail=f"Route '{route_id}' has fewer than 2 stops"
        )

    stops = [StopOut.model_validate(rs.stop) for rs in route_stops]
    coords = _thin_waypoints(stops)

    try:
        return get_route_geometry(
            coords,
            bearings=_bearings_for(coords),
            radiuses=[WAYPOINT_SNAP_RADIUS_M] * len(coords),
        )
    except OSRMError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't compute route geometry: {exc}")