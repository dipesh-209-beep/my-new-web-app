"""
Data-access helpers for the Kathmandu Bus Route Finder.

Spatial lookups go through PostGIS via GeoAlchemy2 against stops.geom.
Everything else is plain SQLAlchemy ORM. All functions take a live
Session so callers control transaction/connection lifecycle.
"""

from typing import List, Optional, Sequence

from geoalchemy2 import Geography
from geoalchemy2.functions import ST_Distance, ST_DWithin, ST_MakePoint, ST_SetSRID
from sqlalchemy import case, cast, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload


from ..models import FareRule, GraphMeta, Operator, Route, RouteOperator, RouteStop, Stop, SegmentCongestionStat


def nearest_stops(
    session: Session,
    lat: float,
    lng: float,
    radius_m: int = 500,
    limit: int = 10,
) -> Sequence[Stop]:
    """Stops within radius_m metres of (lat, lng), nearest first."""
    # Bind lat/lng as real query parameters (ST_MakePoint/ST_SetSRID) rather
    # than formatting them into a WKT string for ST_GeogFromText. The old
    # form was only safe because FastAPI's Query(..., ge=-90, le=90)
    # coerces these to real floats before they ever reach this function --
    # this version is safe regardless of caller.
    point = cast(ST_SetSRID(ST_MakePoint(lng, lat), 4326), Geography)
    stmt = (
        select(Stop)
        .where(ST_DWithin(Stop.geom, point, radius_m))
        .order_by(ST_Distance(Stop.geom, point))
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def get_stop(session: Session, stop_id: str) -> Optional[Stop]:
    return session.get(Stop, stop_id)


def get_route(session: Session, route_id: str) -> Optional[Route]:
    """Route with its ordered stops and operators eager-loaded."""
    stmt = (
        select(Route)
        .where(Route.route_id == route_id)
        .options(
            selectinload(Route.route_stops).selectinload(RouteStop.stop),
            selectinload(Route.route_operators).selectinload(RouteOperator.operator),
            selectinload(Route.operator_ref),
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_route_names(session: Session, route_ids: list[str]) -> dict[str, str]:
    """Single-column, single-query lookup of route_id -> route_name for a
    batch of IDs -- used to label route-finder legs without the overhead
    of get_route's full eager-loaded fetch (route_stops/operators aren't
    needed just to display a leg's route name)."""
    if not route_ids:
        return {}
    stmt = select(Route.route_id, Route.route_name).where(Route.route_id.in_(route_ids))
    return dict(session.execute(stmt).all())


def get_route_stops(session: Session, route_id: str) -> Sequence[RouteStop]:
    """Stops on a route, in sequence_no order, each with its Stop eager-loaded."""
    stmt = (
        select(RouteStop)
        .where(RouteStop.route_id == route_id)
        .options(selectinload(RouteStop.stop))
        .order_by(RouteStop.sequence_no)
    )
    return session.execute(stmt).scalars().all()


def get_operator(session: Session, operator_id: str) -> Optional[Operator]:
    return session.get(Operator, operator_id)


def list_routes_by_stop(session: Session, stop_id: str) -> Sequence[Route]:
    """All routes that pass through a given stop."""
    stmt = (
        select(Route)
        .join(RouteStop, RouteStop.route_id == Route.route_id)
        .where(RouteStop.stop_id == stop_id)
        .distinct()
    )
    return session.execute(stmt).scalars().all()


def list_stops(
    session: Session,
    district: Optional[str] = None,
    status: str = "active",
    limit: int = 100,
    offset: int = 0,
) -> Sequence[Stop]:
    """Paged stop listing, optionally filtered by district."""
    stmt = select(Stop).where(Stop.status == status)
    if district:
        stmt = stmt.where(Stop.district == district)
    stmt = stmt.order_by(Stop.stop_id).limit(limit).offset(offset)
    return session.execute(stmt).scalars().all()


def fare_for_distance(session: Session, distance_km: float) -> Optional[FareRule]:
    """Fare band whose [min, max) range covers distance_km."""
    stmt = (
        select(FareRule)
        .where(FareRule.min_distance_km <= distance_km)
        .where(FareRule.max_distance_km > distance_km)
    )
    return session.execute(stmt).scalar_one_or_none()


# --- Added for GET /stops pagination and the routing graph builder ---


def count_stops(session: Session, status: str = "active") -> int:
    """Total stops matching `status`, for pagination totals alongside list_stops()."""
    stmt = select(func.count()).select_from(Stop).where(Stop.status == status)
    return session.execute(stmt).scalar_one()


def get_active_routes(session: Session) -> Sequence[Route]:
    """
    Active routes with their ordered stops eager-loaded in one query.
    Used by app/routing/graph_builder.py to build the NetworkX graph —
    avoids one query per route + one per route_stop (N+1) while building it.
    """
    stmt = (
        select(Route)
        .where(Route.status == "active")
        .options(selectinload(Route.route_stops).selectinload(RouteStop.stop))
    )
    return session.execute(stmt).scalars().all()


def list_routes(
    session: Session,
    status: str = "active",
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Route]:
    """Paged route listing for the route browser (app/api/routes.py::list_routes
    endpoint), optionally filtered by a case-insensitive substring match on
    route_name. Deliberately lean -- no route_stops eager-loaded here, unlike
    get_active_routes/get_route, since the browser only needs stops for
    whichever single route the user expands (GET /routes/{route_id}/stops)."""
    stmt = select(Route).where(Route.status == status)
    if q:
        escaped_q = q.replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Route.route_name.ilike(f"%{escaped_q}%"))
    stmt = stmt.order_by(Route.route_name).limit(limit).offset(offset)
    return session.execute(stmt).scalars().all()


def count_routes(session: Session, status: str = "active", q: Optional[str] = None) -> int:
    """Total routes matching `status`/`q`, for pagination totals alongside list_routes()."""
    stmt = select(func.count()).select_from(Route).where(Route.status == status)
    if q:
        escaped_q = q.replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Route.route_name.ilike(f"%{escaped_q}%"))
    return session.execute(stmt).scalar_one()


# --- Historical congestion stats (app/api/congestion.py) ---
#
# segment_congestion_stats is an *aggregate* table (see the model
# docstring for the sizing math), upserted per sample rather than
# appended to, so record_congestion_sample and get_congestion_stats are
# the only two entry points that ever touch it.


# Weight given to each new real sample in the exponential moving average
# (see record_congestion_sample). Higher = the average tracks recent
# conditions faster but is noisier; lower = smoother but slower to react
# to a real change (new road, monsoon season, etc). 0.2 means one sample
# moves the average a fifth of the way from where it was to the new
# reading -- roughly a 5-sample "effective memory", tunable here only,
# every consumer just reads avg_duration_s off the row.
CONGESTION_EMA_ALPHA = 0.2


def record_congestion_sample(
    session: Session,
    *,
    route_id: str,
    from_stop_id: str,
    to_stop_id: str,
    day_of_week: int,
    hour_bucket: int,
    duration_s: float,
    distance_m: float,
) -> None:
    """Upsert one real (organic) OSRM observation into this segment/time-
    bucket's running average.

    Blends via an exponential moving average (see CONGESTION_EMA_ALPHA)
    rather than an unweighted lifetime mean -- a segment with hundreds of
    old samples still moves noticeably when recent conditions actually
    change, instead of the average being permanently diluted toward its
    long-run history.

    If the existing row is still just the synthetic seed written by
    scripts/seed_congestion_stats.py (is_seeded and sample_count <= 1),
    this sample REPLACES it outright rather than blending in -- so one
    guessed baseline doesn't bias the average at all once real traffic
    data starts arriving.
    """
    table = SegmentCongestionStat.__table__
    stmt = pg_insert(table).values(
        route_id=route_id,
        from_stop_id=from_stop_id,
        to_stop_id=to_stop_id,
        day_of_week=day_of_week,
        hour_bucket=hour_bucket,
        avg_duration_s=duration_s,
        avg_distance_m=distance_m,
        sample_count=1,
        is_seeded=False,
    )
    excluded = stmt.excluded
    replace_seed = table.c.is_seeded & (table.c.sample_count <= 1)
    alpha = CONGESTION_EMA_ALPHA

    stmt = stmt.on_conflict_do_update(
        constraint="uq_segment_congestion_key",
        set_={
            "avg_duration_s": case(
                (replace_seed, excluded.avg_duration_s),
                else_=(
                    table.c.avg_duration_s * (1 - alpha) + excluded.avg_duration_s * alpha
                ),
            ),
            "avg_distance_m": case(
                (replace_seed, excluded.avg_distance_m),
                else_=(
                    table.c.avg_distance_m * (1 - alpha) + excluded.avg_distance_m * alpha
                ),
            ),
            "sample_count": case(
                (replace_seed, 1),
                else_=table.c.sample_count + 1,
            ),
            "is_seeded": False,
            "updated_at": text("now()"),
        },
    )
    session.execute(stmt)
    # No commit here -- caller owns the transaction.
    # Background task in _record_leg_congestion commits after all samples.


def seed_congestion_baseline(
    session: Session,
    *,
    route_id: str,
    from_stop_id: str,
    to_stop_id: str,
    duration_s: float,
    distance_m: float,
) -> None:
    """Write a synthetic baseline into all 8 hour buckets for every day of
    the week, so the congestion map isn't empty before real traffic
    accumulates. Used by scripts/seed_congestion_stats.py. A no-op (does
    nothing, doesn't overwrite) for any bucket that already has organic
    data, so re-running the seed script is always safe.

    Also anchors free_flow_duration_s to this same OSRM duration -- at
    seed time this segment has zero recorded congestion by construction,
    so it's a genuine free-driving estimate, independent of whatever
    avg_duration_s drifts to later via real samples.
    """
    table = SegmentCongestionStat.__table__
    rows = [
        {
            "route_id": route_id,
            "from_stop_id": from_stop_id,
            "to_stop_id": to_stop_id,
            "day_of_week": dow,
            "hour_bucket": hour,
            "avg_duration_s": duration_s,
            "avg_distance_m": distance_m,
            "sample_count": 1,
            "is_seeded": True,
            "free_flow_duration_s": duration_s,
        }
        for dow in range(7)
        for hour in (0, 3, 6, 9, 12, 15, 18, 21)
    ]
    stmt = pg_insert(table).values(rows)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_segment_congestion_key")
    session.execute(stmt)
    session.commit()


def get_congestion_stats(
    session: Session, day_of_week: int, hour_bucket: int
) -> Sequence:
    """Every segment with data for this (day_of_week, hour_bucket), each
    row paired with that segment's free-flow baseline, so callers can
    turn it into a congestion ratio without a second round-trip. See
    app/api/congestion.py for how the ratio becomes free_flow/moderate/heavy.

    Prefers the segment's own stored free_flow_duration_s (anchored once
    at seed time to a genuine free-driving OSRM estimate -- see
    seed_congestion_baseline). Falls back to min(avg_duration_s) across
    the segment's own buckets only for rows written before that column
    existed and never re-seeded since -- a worse anchor (it can compress
    toward 1.0 if a segment is never truly free-flowing), kept only so
    old data still resolves to *something* instead of erroring.
    """
    T = SegmentCongestionStat
    fallback_baseline = (
        select(
            T.route_id.label("route_id"),
            T.from_stop_id.label("from_stop_id"),
            T.to_stop_id.label("to_stop_id"),
            func.min(T.avg_duration_s).label("free_flow_duration_s"),
        )
        .group_by(T.route_id, T.from_stop_id, T.to_stop_id)
        .subquery()
    )
    stmt = (
        select(T, func.coalesce(T.free_flow_duration_s, fallback_baseline.c.free_flow_duration_s))
        .join(
            fallback_baseline,
            (T.route_id == fallback_baseline.c.route_id)
            & (T.from_stop_id == fallback_baseline.c.from_stop_id)
            & (T.to_stop_id == fallback_baseline.c.to_stop_id),
        )
        .where(T.day_of_week == day_of_week, T.hour_bucket == hour_bucket)
    )
    return session.execute(stmt).all()


def get_congestion_ratios(
    session: Session, day_of_week: int, hour_bucket: int
) -> dict[tuple[str, str, str], float]:
    """(route_id, from_stop_id, to_stop_id) -> congestion_ratio for every
    segment with data at this (day_of_week, hour_bucket). Thin wrapper
    around get_congestion_stats for callers (routing/pathfinder.py) that
    just need the ratio to weight graph edges, not the full ORM row --
    used by find_shortest_path(..., avoid_congestion=True).
    """
    ratios: dict[tuple[str, str, str], float] = {}
    for stat, free_flow_duration_s in get_congestion_stats(
        session, day_of_week=day_of_week, hour_bucket=hour_bucket
    ):
        if not free_flow_duration_s or free_flow_duration_s <= 0:
            continue
        ratios[(stat.route_id, stat.from_stop_id, stat.to_stop_id)] = (
            stat.avg_duration_s / free_flow_duration_s
        )
    return ratios


def get_graph_version(session: Session) -> int:
    """Current graph version. Row is seeded by migration; never missing
    in a properly-migrated DB, but fall back to 1 defensively rather than
    raising, since this is called on the hot path of every route search."""
    row = session.get(GraphMeta, 1)
    return row.version if row is not None else 1


def bump_graph_version(session: Session) -> int:
    """Call at the end of any admin write that changes graph shape:
    add_route_stop, update_route_status, create_route/create_stop (the
    latter two are harmless no-ops for the graph until linked via
    add_route_stop, but bumping anyway is cheap and one less thing to
    reason about). Does NOT commit -- caller owns the transaction so
    mutation and version bump are atomic. Cache invalidation should
    happen after the commit succeeds."""
    session.execute(
        text("UPDATE graph_meta SET version = version + 1 WHERE id = 1")
    )
    return get_graph_version(session)
