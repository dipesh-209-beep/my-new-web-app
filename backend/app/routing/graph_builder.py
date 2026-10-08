"""
routing/graph_builder.py

Builds the NetworkX directed graph used for shortest-path route finding.

Ride nodes are sequence-aware:
    (stop_id, route_id, sequence_no)

This is important for loop routes where the same physical stop occurs
multiple times in one route, e.g. NY-03.
"""

import math
import threading

import networkx as nx
from sqlalchemy.orm import Session

from app.db.queries import get_active_routes, get_graph_version
from app.routing.constants import EARTH_RADIUS, INTERCHANGE_DISTANCE, TRANSFER_PENALTY


_graph_cache: nx.DiGraph | None = None
_cached_version: int | None = None
# Guards rebuilds of the two globals above. FastAPI runs sync routes (all
# of ours) in a threadpool, so two requests in the same worker process can
# genuinely run concurrently -- without this, two threads that both see a
# stale/missing cache right after a version bump would both call
# build_graph() at once. Harmless before (just duplicated work, no
# corruption -- reference reassignment is GIL-atomic), but cheap enough to
# just not do twice.
_graph_lock = threading.Lock()


def haversine_distance_m(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
) -> float:
    """Great-circle distance in meters between two lat/lng points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(dlambda / 2) ** 2
    )

    return 2 * EARTH_RADIUS * math.asin(math.sqrt(a))


def _ride_node(
    stop_id: str,
    route_id: str,
    sequence_no: int,
) -> tuple[str, str, int]:
    """
    Identify a particular occurrence of a stop on a route.

    Example:

        (S0185, R-NY-03, 4)
        (S0185, R-NY-03, 12)

    are deliberately different nodes.
    """
    return (stop_id, route_id, sequence_no)


def build_graph(session: Session) -> nx.DiGraph:
    graph = nx.DiGraph()

    routes = get_active_routes(session)

    # Physical stop coordinates.
    stop_coords: dict[str, tuple[float, float]] = {}

    for route in routes:
        ordered = [
            rs
            for rs in sorted(
                route.route_stops,
                key=lambda rs: rs.sequence_no,
            )
            if rs.stop is not None
        ]

        # ------------------------------------------------------------
        # Create physical + sequence-aware ride nodes.
        # ------------------------------------------------------------
        for rs in ordered:
            stop = rs.stop

            if stop.stop_id not in graph:
                graph.add_node(
                    stop.stop_id,
                    lat=stop.lat,
                    lng=stop.lng,
                    stop_name=stop.stop_name,
                    kind="physical",
                )

                stop_coords[stop.stop_id] = (
                    stop.lat,
                    stop.lng,
                )

            ride_node = _ride_node(
                rs.stop_id,
                route.route_id,
                rs.sequence_no,
            )

            graph.add_node(
                ride_node,
                lat=stop.lat,
                lng=stop.lng,
                stop_name=stop.stop_name,
                kind="ride",
                route_id=route.route_id,
                sequence_no=rs.sequence_no,
            )

            # Boarding this route.
            graph.add_edge(
                stop.stop_id,
                ride_node,
                weight=TRANSFER_PENALTY,
                distance_m=0,
                route_id=route.route_id,
                kind="board",
                is_transfer=False,
            )

            # Getting off the bus.
            graph.add_edge(
                ride_node,
                stop.stop_id,
                weight=0,
                distance_m=0,
                route_id=route.route_id,
                kind="alight",
                is_transfer=False,
            )

        # ------------------------------------------------------------
        # Ordered ride edges.
        #
        # Because sequence_no is part of the node, repeated stops
        # remain distinct occurrences.
        # ------------------------------------------------------------
        for a, b in zip(ordered, ordered[1:]):
            dist = haversine_distance_m(
                a.stop.lat,
                a.stop.lng,
                b.stop.lat,
                b.stop.lng,
            )

            node_a = _ride_node(
                a.stop_id,
                route.route_id,
                a.sequence_no,
            )

            node_b = _ride_node(
                b.stop_id,
                route.route_id,
                b.sequence_no,
            )

            graph.add_edge(
                node_a,
                node_b,
                weight=dist,
                distance_m=dist,
                route_id=route.route_id,
                kind="ride",
                is_transfer=False,
            )

            # Only routes explicitly marked bidirectional get reverse
            # ride edges.
            if route.is_bidirectional:
                graph.add_edge(
                    node_b,
                    node_a,
                    weight=dist,
                    distance_m=dist,
                    route_id=route.route_id,
                    kind="ride",
                    is_transfer=False,
                )

    # ------------------------------------------------------------
    # Walking/interchange edges between different physical stops.
    #
    # Bucket stops into a coarse lat/lng grid and only compare each stop
    # against others sharing its cell or an adjacent one. This replaces
    # an all-pairs O(n^2) scan (a bounding-box check made each individual
    # comparison cheap, but there were still n*(n-1)/2 of them) with an
    # approach that's linear in stop count as long as stops are spread
    # roughly evenly across the valley -- which they are, since transit
    # stops don't cluster into a single tiny area. Cell size (0.01 deg,
    # ~1.1km at this latitude) is chosen so INTERCHANGE_DISTANCE (200m)
    # can never span more than one adjacent cell in either direction, so
    # checking the 3x3 neighborhood around a stop's own cell can't miss a
    # true interchange pair.
    # ------------------------------------------------------------
    GRID_CELL_DEG = 0.01
    stop_ids = list(stop_coords.keys())

    def _grid_key(lat: float, lng: float) -> tuple[int, int]:
        return (int(lat // GRID_CELL_DEG), int(lng // GRID_CELL_DEG))

    grid: dict[tuple[int, int], list[str]] = {}
    for sid in stop_ids:
        lat, lng = stop_coords[sid]
        grid.setdefault(_grid_key(lat, lng), []).append(sid)

    seen_pairs: set[tuple[str, str]] = set()

    for sid_a in stop_ids:
        lat_a, lng_a = stop_coords[sid_a]
        gx, gy = _grid_key(lat_a, lng_a)

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for sid_b in grid.get((gx + dx, gy + dy), ()):
                    if sid_b == sid_a:
                        continue
                    pair = (sid_a, sid_b) if sid_a < sid_b else (sid_b, sid_a)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    lat_b, lng_b = stop_coords[sid_b]
                    dist = haversine_distance_m(lat_a, lng_a, lat_b, lng_b)

                    if dist > INTERCHANGE_DISTANCE:
                        continue

                    graph.add_edge(
                        sid_a,
                        sid_b,
                        weight=dist,
                        distance_m=dist,
                        route_id=None,
                        kind="walk",
                        is_transfer=True,
                    )

                    graph.add_edge(
                        sid_b,
                        sid_a,
                        weight=dist,
                        distance_m=dist,
                        route_id=None,
                        kind="walk",
                        is_transfer=True,
                    )

    return graph


def get_cached_graph(
    session: Session,
    refresh: bool = False,
) -> nx.DiGraph:
    """Rebuilds only when needed: either explicitly asked to (refresh=True,
    used by /admin/graph/reload as a manual escape hatch), or when the DB's
    graph_meta.version has moved past what this process last built from.

    The version check is what makes cache invalidation work across
    multiple worker processes/replicas -- refresh=True alone only ever
    fixed the *current* process, silently leaving every other worker on
    stale data. See app/models/graph_meta.py for the full rationale.

    session may be None in unit tests that monkeypatch get_active_routes
    to avoid touching a real DB (see tests/test_routing.py) -- version
    tracking is simply skipped in that case, falling back to the
    original refresh-flag-only behavior those tests already assume.
    """
    global _graph_cache, _cached_version

    current_version = None
    if session is not None:
        try:
            current_version = get_graph_version(session)
        except Exception:
            # Defensive: never let a version-check failure take down
            # route-finding entirely. Falls back to refresh-flag-only
            # behavior for this call; the next successful call recovers
            # normal version tracking.
            current_version = None

    version_is_stale = current_version is not None and current_version != _cached_version

    if _graph_cache is None or refresh or version_is_stale:
        with _graph_lock:
            # Re-check inside the lock: another thread may have already
            # rebuilt while this one was waiting to acquire it.
            version_is_stale = current_version is not None and current_version != _cached_version
            if _graph_cache is None or refresh or version_is_stale:
                _graph_cache = build_graph(session)
                _cached_version = current_version

    return _graph_cache


def invalidate_graph_cache() -> None:
    global _graph_cache, _cached_version
    _graph_cache = None
    _cached_version = None
