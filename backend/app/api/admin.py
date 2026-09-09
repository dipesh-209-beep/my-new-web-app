"""Write endpoints for populating the network (data entry / ETL use).

Every route here requires valid admin credentials (shared
X-Admin-Api-Key header, or a per-admin bearer JWT from POST
/admin/login) plus a role check on the JWT path -- see
app/core/security.py's require_admin / require_role for the mechanism,
and require_role's docstring there for the editor/admin role split.
Each route below is gated to the narrowest role that still covers its
actual blast radius:

  editor: create_stop, create_route, add_route_stop, remove_route_stop,
    reorder_route_stops -- additive/structural dataset changes, easy to
    undo if wrong (re-insert the row / restore the order).
  admin:  update_route_status, reload_graph_cache -- these can
    immediately change what /route-finder returns to real users.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.response_cache import invalidate as invalidate_cache
from app.core.security import require_role, ROLE_ADMIN, ROLE_EDITOR
from app.db.id_generator import next_route_id, next_stop_id
from app.db.queries import apply_stop_sequence_change, bump_graph_version, sync_route_total_stops
from app.db.session import get_db
from app.routing.graph_builder import get_cached_graph
from app.models import Route as RouteORM
from app.models import RouteStop as RouteStopORM
from app.models import Stop as StopORM

from app.schemas import (
    RouteCreate,
    RouteNameUpdate,
    RouteOut,
    RouteStatusUpdate,
    RouteStopCreate,
    RouteStopRef,
    RouteStopReorder,
    StopCreate,
    StopNameUpdate,
    StopOut,
)

router = APIRouter()

# editor and admin can both do routine data entry; admin can additionally
# do anything editor can, so it's listed on every gate below rather than
# implying a role hierarchy the code would otherwise have to compute.
_EDIT = Depends(require_role(ROLE_EDITOR, ROLE_ADMIN))
_ADMIN_ONLY = Depends(require_role(ROLE_ADMIN))

# Re-keyed cache namespaces that a route_stops write can change (see the
# individual endpoints for why each one matters):
#   - "routes" / "route_detail": route listing + single-route responses
#     embed total_stops, which add/remove/reorder all change.
#   - "route_stops": the ordered stop list itself.
#   - "route_geometry": geometry is keyed on route_id; a changed stop
#     order invalidates a previously-cached geometry.
#   - "stops": GET /stops/{id}/routes returns routes, and individual stop
#     pages don't embed this, but route browsing feeds stop pages -- kept
#     for parity with the original re-key set (cheap, conservative).
_ROUTE_STOP_CACHE_KEYS = ("routes", "route_detail", "route_stops", "route_geometry", "stops")


@router.post("/stops", response_model=StopOut, status_code=status.HTTP_201_CREATED, dependencies=[_EDIT])
def create_stop(payload: StopCreate, db: Session = Depends(get_db)) -> StopOut:
    row = StopORM(
        stop_id=next_stop_id(db),
        stop_name=payload.stop_name,
        aliases=payload.aliases,
        lat=payload.lat,
        lng=payload.lng,
        zone=payload.zone,
        district=payload.district,
        ward=payload.ward,
        landmark=payload.landmark,
        is_major_stop=payload.is_major_stop,
        has_shelter=payload.has_shelter,
        has_ticket_counter=payload.has_ticket_counter,
        is_interchange=payload.is_interchange,
        wheelchair_access=payload.wheelchair_access,
        audio_support=payload.audio_support,
    )
    # geom is populated by the trg_stops_set_geom trigger from lat/lng --
    # do not set it here.
    db.add(row)
    bump_graph_version(db)
    db.commit()
    db.refresh(row)
    invalidate_cache("stops")
    return StopOut.model_validate(row)


@router.patch("/stops/{stop_id}", response_model=StopOut, dependencies=[_EDIT])
def update_stop_name(stop_id: str, payload: StopNameUpdate, db: Session = Depends(get_db)) -> StopOut:
    """Direct (editorial) stop-name correction -- the admin path for what
    public users can suggest via POST /suggestions (stop_name_change).
    Only touches stop_name; nothing else about the stop changes.

    No graph-version bump: the routing graph is keyed by stop_id and the
    route-finder response builds StopOut from the DB (app/api/routing.py),
    so a name change never needs a graph rebuild -- just a response-cache
    re-key. Stop names are embedded in both the /stops and /routes/{id}/stops
    responses, so both namespaces are invalidated."""
    row = db.get(StopORM, stop_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop {stop_id} not found.")

    row.stop_name = payload.stop_name
    db.commit()
    db.refresh(row)
    invalidate_cache("stops")
    invalidate_cache("route_stops")
    return StopOut.model_validate(row)


@router.post("/routes", response_model=RouteOut, status_code=status.HTTP_201_CREATED, dependencies=[_EDIT])
def create_route(payload: RouteCreate, db: Session = Depends(get_db)) -> RouteOut:
    if db.get(StopORM, payload.start_stop_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop {payload.start_stop_id} not found.")
    if db.get(StopORM, payload.end_stop_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop {payload.end_stop_id} not found.")

    row = RouteORM(
        route_id=next_route_id(db),
        route_name=payload.route_name,
        short_name=payload.short_name,
        vehicle_type=payload.vehicle_type,
        route_type=payload.route_type,
        operator=payload.operator,
        operator_id=payload.operator_id,
        start_stop_id=payload.start_stop_id,
        end_stop_id=payload.end_stop_id,
        total_stops=payload.total_stops,
        is_bidirectional=payload.is_bidirectional,
        has_ac=payload.has_ac,
        is_express=payload.is_express,
    )
    db.add(row)
    bump_graph_version(db)
    db.commit()
    db.refresh(row)
    invalidate_cache("routes")
    return RouteOut.model_validate(row)


@router.patch("/routes/{route_id}", response_model=RouteOut, dependencies=[_EDIT])
def update_route_name(route_id: str, payload: RouteNameUpdate, db: Session = Depends(get_db)) -> RouteOut:
    """Direct (editorial) route-name correction -- the admin path for what
    public users can suggest via POST /suggestions (route_name_change).
    Only touches route_name; nothing else about the route changes.

    No graph-version bump: the routing graph is keyed by (stop_id,
    route_id, sequence_no) triples and route-finder labels legs from the
    DB (app/api/routing.py), so a name change never needs a rebuild --
    just a response-cache re-key on the namespaces that embed route_name
    (the browser listing and the single-route detail response)."""
    row = db.get(RouteORM, route_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route {route_id} not found.")

    row.route_name = payload.route_name
    db.commit()
    db.refresh(row)
    invalidate_cache("routes")
    invalidate_cache("route_detail")
    return RouteOut.model_validate(row)


@router.post("/routes/{route_id}/stops", status_code=status.HTTP_201_CREATED, dependencies=[_EDIT])
def add_route_stop(route_id: str, payload: RouteStopCreate, db: Session = Depends(get_db)) -> RouteStopRef:
    if db.get(RouteORM, route_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route {route_id} not found.")
    if db.get(StopORM, payload.stop_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop {payload.stop_id} not found.")

    # Validate sequence_no is contiguous (no gaps). Allow appending at the end
    # or inserting at an existing position (which would shift later stops via DB
    # but we don't support shifting here, so reject if not max+1).
    max_seq = db.execute(
        text("SELECT COALESCE(MAX(sequence_no), 0) FROM route_stops WHERE route_id = :rid"),
        {"rid": route_id},
    ).scalar_one()
    if payload.sequence_no > max_seq + 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"sequence_no {payload.sequence_no} would create a gap. "
                f"Max existing sequence_no is {max_seq}. "
                f"Must be <= {max_seq + 1}."
            ),
        )

    row = RouteStopORM(route_id=route_id, stop_id=payload.stop_id, sequence_no=payload.sequence_no)
    db.add(row)
    try:
        bump_graph_version(db)
        sync_route_total_stops(db, route_id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Route {route_id} already has a stop at sequence_no {payload.sequence_no}.",
        ) from exc

    for key in _ROUTE_STOP_CACHE_KEYS:
        invalidate_cache(key)

    return RouteStopRef(route_id=route_id, stop_id=payload.stop_id, sequence_no=payload.sequence_no)


@router.delete("/routes/{route_id}/stops/{sequence_no}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_EDIT])
def remove_route_stop(route_id: str, sequence_no: int, db: Session = Depends(get_db)) -> None:
    """Remove one stop from a route and re-sequence the remaining entries
    so sequence_no stays contiguous (1..N). Only touches route_stops --
    the physical stop row is untouched."""
    if db.get(RouteORM, route_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route {route_id} not found.")

    row = db.get(RouteStopORM, (route_id, sequence_no))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Route {route_id} has no stop at sequence_no {sequence_no}.",
        )

    db.delete(row)
    # Flush the DELETE before resequencing: otherwise SQLAlchemy's queued
    # delete would run *after* the UPDATE below and match a different row
    # (the guard is the row's sequence_no, which the UPDATE has already
    # shifted), silently removing the wrong stop.
    db.flush()
    # Close the gap: re-sequence every higher row down by one so the
    # sequence stays contiguous (add_route_stop relies on that invariant).
    db.execute(
        text(
            "UPDATE route_stops SET sequence_no = sequence_no - 1 "
            "WHERE route_id = :rid AND sequence_no > :seq"
        ),
        {"rid": route_id, "seq": sequence_no},
    )
    bump_graph_version(db)
    sync_route_total_stops(db, route_id)
    db.commit()

    for key in _ROUTE_STOP_CACHE_KEYS:
        invalidate_cache(key)


@router.patch("/routes/{route_id}/stops/order", response_model=list[RouteStopRef], dependencies=[_EDIT])
def reorder_route_stops(route_id: str, payload: RouteStopReorder, db: Session = Depends(get_db)) -> list[RouteStopRef]:
    """Reorder a route's stops in one call. The payload is the route's
    *current* sequence_no values arranged in the desired new order (a
    permutation of 1..N) -- see RouteStopReorder in app/schemas.py for
    why it's sequence numbers and not stop_ids.

    The permutation validation + re-numbering + graph bump + total_stops
    sync live in app/db/queries.py::apply_stop_sequence_change (also used
    by the suggestion auto-apply path); this endpoint owns the HTTP
    concerns: 404 for an unknown route, 400 for a non-permutation, and
    response-cache invalidation after the commit."""
    if db.get(RouteORM, route_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route {route_id} not found.")

    try:
        rows = apply_stop_sequence_change(db, route_id, payload.sequence)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.commit()

    rows = sorted(rows, key=lambda row: row.sequence_no)
    for key in _ROUTE_STOP_CACHE_KEYS:
        invalidate_cache(key)

    return [
        RouteStopRef(route_id=row.route_id, stop_id=row.stop_id, sequence_no=row.sequence_no)
        for row in rows
    ]

@router.patch("/routes/{route_id}/status", response_model=RouteOut, dependencies=[_ADMIN_ONLY])
def update_route_status(route_id: str, payload: RouteStatusUpdate, db: Session = Depends(get_db)) -> RouteOut:
    row = db.get(RouteORM, route_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route {route_id} not found.")

    row.status = payload.status
    bump_graph_version(db)
    db.commit()
    db.refresh(row)

    # Refresh this process's own cache immediately so the admin sees it
    # reflected right away. Other workers will notice on their next request
    # via the version bump.
    get_cached_graph(db, refresh=True)
    invalidate_cache("routes")
    # A status flip (e.g. active -> inactive) changes which routes
    # /route-finder considers, and route_geometry responses are keyed by
    # route_id -- an inactive route's last-cached geometry would otherwise
    # keep being served as if it were still a valid option.
    invalidate_cache("route_geometry")

    return RouteOut.model_validate(row)

@router.post("/graph/reload", status_code=status.HTTP_200_OK, dependencies=[_ADMIN_ONLY])
def reload_graph_cache(db: Session = Depends(get_db)) -> dict:
    """Rebuild the in-memory routing graph from the current DB state.

    Call this after adding stops/routes/route_stops -- the graph is
    cached (see app/routing/graph_builder.py) so writes don't show up in
    /api/route/find until this runs.
    """
    graph = get_cached_graph(db, refresh=True)
    return {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}
