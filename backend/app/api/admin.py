"""Write endpoints for populating the network (data entry / ETL use).

Every route here requires authentication (a per-admin bearer JWT from
POST /admin/login, or a scoped service credential via
`Authorization: SvcKey ...` -- see app/core/security.py) *and* an
explicit permission check, wired in through
`require_permissions(...)` as a route dependency. The single shared
X-Admin-Api-Key header no longer grants anything: it is disabled by
default and rejected outright in production (see
ALLOW_LEGACY_SHARED_ADMIN_KEY in app/core/config.py); the supported way
for automation is a scoped, revocable service credential created via
POST /admin/service-credentials.

Each route is gated to the narrowest permission set that still covers
its actual blast radius:

  stops:write          POST/PATCH /stops
  routes:write         POST/PATCH /routes
  route_stops:write    POST/DELETE/PATCH .../stops[...], .../stops/order
  routes:status        PATCH /routes/{id}/status
  graph:reload         POST /graph/reload

Those sets are what an `editor` AdminUser gets; an `admin` gets all of
them (app/core/security.py::ROLE_PERMISSIONS). A service credential
gets exactly the scopes it was minted with, so a data-import key cannot
flip a route's status or reload the graph.

Every mutation below also writes a row to admin_audit_log in the *same*
transaction as the data change (app/core/admin_audit.py::record_audit),
so the two commit or roll back together.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.admin_audit import record_audit
from app.core.response_cache import invalidate as invalidate_cache
from app.core.security import (
    PERM_GRAPH_RELOAD,
    PERM_ROUTES_STATUS,
    PERM_ROUTES_WRITE,
    PERM_ROUTE_STOPS_WRITE,
    PERM_STOPS_WRITE,
    Principal,
    require_permissions,
)
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

# No module-level gate constants here on purpose. A route's
# `dependencies=[...]` list and its parameter dependencies are separate
# FastAPI dependency instances, so putting a coarse check in the list
# *and* a narrow one on the parameter makes the route require the union
# of the two -- a credential scoped to just `stops:write` would be
# refused by POST /stops for also lacking `routes:write`. Each route
# therefore declares exactly one gate, as its `principal` parameter:
#
#     principal: Principal = Depends(require_permissions(PERM_STOPS_WRITE))
#
# which is both the authorization check and the handle the handler needs
# to attribute its audit row. The role-to-permission mapping lives in
# app/core/security.py (EDITOR_PERMISSIONS / ADMIN_ONLY_PERMISSIONS).

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


def _audit(
    db: Session,
    request: Request,
    principal: Principal,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict | None = None,
) -> None:
    """Stage an audit row for a successful mutation.

    Staged (not committed) on purpose: the caller commits it together
    with the data change a few lines later, so the two can't disagree.
    """
    record_audit(
        db,
        principal=principal,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        success=True,
        request_id=getattr(request.state, "request_id", None),
        client_ip=getattr(request.state, "client_ip", None),
        detail=detail,
    )


@router.post("/stops", response_model=StopOut, status_code=status.HTTP_201_CREATED)
def create_stop(
    payload: StopCreate,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_STOPS_WRITE)),
    db: Session = Depends(get_db),
) -> StopOut:
    # next_stop_id draws from a PostgreSQL sequence, so the id is known
    # before the INSERT and the audit row can name the resource without
    # a flush.
    stop_id = next_stop_id(db)
    row = StopORM(
        stop_id=stop_id,
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
    _audit(
        db,
        request,
        principal,
        action="stop.create",
        resource_type="stop",
        resource_id=stop_id,
        detail={"stop_name": payload.stop_name, "district": payload.district},
    )
    db.commit()
    db.refresh(row)
    invalidate_cache("stops")
    return StopOut.model_validate(row)


@router.patch("/stops/{stop_id}", response_model=StopOut)
def update_stop_name(
    stop_id: str,
    payload: StopNameUpdate,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_STOPS_WRITE)),
    db: Session = Depends(get_db),
) -> StopOut:
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

    previous_name = row.stop_name
    row.stop_name = payload.stop_name
    _audit(
        db,
        request,
        principal,
        action="stop.update",
        resource_type="stop",
        resource_id=stop_id,
        detail={"fields": ["stop_name"], "previous_stop_name": previous_name},
    )
    db.commit()
    db.refresh(row)
    invalidate_cache("stops")
    invalidate_cache("route_stops")
    return StopOut.model_validate(row)


@router.post("/routes", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
def create_route(
    payload: RouteCreate,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_ROUTES_WRITE)),
    db: Session = Depends(get_db),
) -> RouteOut:
    if db.get(StopORM, payload.start_stop_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop {payload.start_stop_id} not found.")
    if db.get(StopORM, payload.end_stop_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Stop {payload.end_stop_id} not found.")

    route_id = next_route_id(db)
    row = RouteORM(
        route_id=route_id,
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
    _audit(
        db,
        request,
        principal,
        action="route.create",
        resource_type="route",
        resource_id=row.route_id,
        detail={"route_name": payload.route_name, "vehicle_type": payload.vehicle_type},
    )
    db.commit()
    db.refresh(row)
    invalidate_cache("routes")
    return RouteOut.model_validate(row)


@router.patch("/routes/{route_id}", response_model=RouteOut)
def update_route_name(
    route_id: str,
    payload: RouteNameUpdate,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_ROUTES_WRITE)),
    db: Session = Depends(get_db),
) -> RouteOut:
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

    previous_name = row.route_name
    row.route_name = payload.route_name
    _audit(
        db,
        request,
        principal,
        action="route.update",
        resource_type="route",
        resource_id=route_id,
        detail={"fields": ["route_name"], "previous_route_name": previous_name},
    )
    db.commit()
    db.refresh(row)
    invalidate_cache("routes")
    invalidate_cache("route_detail")
    return RouteOut.model_validate(row)


@router.post("/routes/{route_id}/stops", status_code=status.HTTP_201_CREATED)
def add_route_stop(
    route_id: str,
    payload: RouteStopCreate,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_ROUTE_STOPS_WRITE)),
    db: Session = Depends(get_db),
) -> RouteStopRef:
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
        _audit(
            db,
            request,
            principal,
            action="route_stop.create",
            resource_type="route_stop",
            resource_id=f"{route_id}:{payload.sequence_no}",
            detail={"stop_id": payload.stop_id, "sequence_no": payload.sequence_no},
        )
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


@router.delete("/routes/{route_id}/stops/{sequence_no}", status_code=status.HTTP_204_NO_CONTENT)
def remove_route_stop(
    route_id: str,
    sequence_no: int,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_ROUTE_STOPS_WRITE)),
    db: Session = Depends(get_db),
) -> None:
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

    removed_stop_id = row.stop_id
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
    _audit(
        db,
        request,
        principal,
        action="route_stop.delete",
        resource_type="route_stop",
        resource_id=f"{route_id}:{sequence_no}",
        detail={"stop_id": removed_stop_id, "sequence_no": sequence_no, "resequenced": True},
    )
    db.commit()

    for key in _ROUTE_STOP_CACHE_KEYS:
        invalidate_cache(key)


@router.patch("/routes/{route_id}/stops/order", response_model=list[RouteStopRef])
def reorder_route_stops(
    route_id: str,
    payload: RouteStopReorder,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_ROUTE_STOPS_WRITE)),
    db: Session = Depends(get_db),
) -> list[RouteStopRef]:
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

    _audit(
        db,
        request,
        principal,
        action="route_stop.reorder",
        resource_type="route",
        resource_id=route_id,
        detail={"stop_count": len(rows), "new_sequence": list(payload.sequence)},
    )
    db.commit()

    rows = sorted(rows, key=lambda row: row.sequence_no)
    for key in _ROUTE_STOP_CACHE_KEYS:
        invalidate_cache(key)

    return [
        RouteStopRef(route_id=row.route_id, stop_id=row.stop_id, sequence_no=row.sequence_no)
        for row in rows
    ]


@router.patch("/routes/{route_id}/status", response_model=RouteOut)
def update_route_status(
    route_id: str,
    payload: RouteStatusUpdate,
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_ROUTES_STATUS)),
    db: Session = Depends(get_db),
) -> RouteOut:
    row = db.get(RouteORM, route_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Route {route_id} not found.")

    previous_status = row.status
    row.status = payload.status
    bump_graph_version(db)
    _audit(
        db,
        request,
        principal,
        action="route.status.update",
        resource_type="route",
        resource_id=route_id,
        detail={"from": previous_status, "to": payload.status},
    )
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
    # keep being served as if it were still a valid option. route_detail
    # must go too: GET /routes/{route_id} embeds status, and without this
    # the detail response keeps serving the old status for the TTL window.
    invalidate_cache("route_geometry")
    invalidate_cache("route_detail")

    return RouteOut.model_validate(row)


@router.post("/graph/reload", status_code=status.HTTP_200_OK)
def reload_graph_cache(
    request: Request,
    principal: Principal = Depends(require_permissions(PERM_GRAPH_RELOAD)),
    db: Session = Depends(get_db),
) -> dict:
    """Rebuild the in-memory routing graph from the current DB state.

    Call this after adding stops/routes/route_stops -- the graph is
    cached (see app/routing/graph_builder.py) so writes don't show up
    in /route-finder until this runs.
    """
    graph = get_cached_graph(db, refresh=True)
    # Committed on its own: a graph rebuild changes no rows in the
    # database, so there's no mutation to be atomic with, and the audit
    # row should survive even if the caller disconnects mid-response.
    record_audit(
        db,
        principal=principal,
        action="graph.reload",
        resource_type="graph",
        resource_id=None,
        success=True,
        request_id=getattr(request.state, "request_id", None),
        client_ip=getattr(request.state, "client_ip", None),
        detail={"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()},
    )
    db.commit()
    return {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}
