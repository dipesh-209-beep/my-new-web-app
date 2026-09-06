"""
api/stops.py

GET /stops                  — paginated stop listing
GET /stops/nearby            — nearest stops to a lat/lng, within a radius
GET /stops/{stop_id}         — a single stop by ID
GET /stops/{stop_id}/routes  — routes that pass through a stop
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.response_cache import cached_response
from app.db import queries
from app.db.session import get_db
from app.schemas import RouteOut, StopListOut, StopOut

router = APIRouter(tags=["stops"])
settings = get_settings()


@router.get("/stops/nearby", response_model=list[StopOut])
def stops_nearby(
    lat: float = Query(..., ge=-90, le=90, description="Latitude of the search point"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude of the search point"),
    radius_m: int = Query(
        settings.DEFAULT_NEARBY_RADIUS_M, gt=0, le=5000, description="Search radius in meters"
    ),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Registered before /stops so FastAPI doesn't try to match 'nearby' as a path param."""
    return queries.nearest_stops(db, lat=lat, lng=lng, radius_m=radius_m, limit=limit)


@router.get("/stops", response_model=StopListOut)
@cached_response(
    "stops", ttl_seconds=settings.STOPS_CACHE_TTL_S, key_params=("offset", "limit", "district")
)
def list_stops(
    offset: int = Query(0, ge=0),
    limit: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
    district: str | None = Query(None, description="Optional district filter"),
    db: Session = Depends(get_db),
):
    items = queries.list_stops(db, district=district, limit=limit, offset=offset)
    total = queries.count_stops(db)
    return StopListOut(total=total, limit=limit, offset=offset, items=items)


@router.get("/stops/{stop_id}", response_model=StopOut)
@cached_response("stops", ttl_seconds=settings.STOPS_CACHE_TTL_S, key_params=("stop_id",))
def read_stop(stop_id: str, db: Session = Depends(get_db)):
    """Registered after /stops and /stops/nearby -- FastAPI matches those
    static path segments first, so 'nearby' can never be mis-parced as a
    stop_id by this dynamic route."""
    stop = queries.get_stop(db, stop_id)
    if stop is None:
        raise HTTPException(status_code=404, detail=f"Stop '{stop_id}' not found")
    return StopOut.model_validate(stop)


@router.get("/stops/{stop_id}/routes", response_model=list[RouteOut])
def read_stop_routes(stop_id: str, db: Session = Depends(get_db)):
    """Every route that passes through this stop. 404s if the stop itself
    doesn't exist (distinct from a real stop with zero routes serving
    it, which returns an empty list)."""
    if queries.get_stop(db, stop_id) is None:
        raise HTTPException(status_code=404, detail=f"Stop '{stop_id}' not found")
    return queries.list_routes_by_stop(db, stop_id)