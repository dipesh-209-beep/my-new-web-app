"""
routing/pathfinder.py

Route finding logic:

1. Look for an active route containing both origin and destination.
2. If a direct route exists, use it directly.
3. If no direct route exists, use NetworkX shortest-path search.

Loop routes are handled correctly even when the same physical stop occurs
multiple times in the same route.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import heapq

import networkx as nx
from sqlalchemy.orm import Session

from app.db.queries import get_congestion_ratios
from app.routing import graph_builder as gb
from app.routing import congestion_zones
from app.routing.constants import (
    BOARD_WAIT_S,
    BUS_AVG_SPEED_MPS,
    CONGESTION_LAMBDA,
    WALK_SPEED_MPS,
)
from app.routing.time_buckets import day_and_bucket_for, now_in_nepal


class NoRouteFoundError(Exception):
    """Raised when no route connects the requested stops."""


@dataclass(frozen=True)
class PathSegment:
    from_stop_id: str
    to_stop_id: str
    route_id: Optional[str]
    is_transfer: bool
    weight: float


@dataclass(frozen=True)
class RouteAlternative:
    """A secondary option alongside the primary RouteFinderResult -- see
    schemas.RouteAlternative (the API-facing counterpart this maps onto)
    for what each label means."""

    label: str
    segments: List[PathSegment]
    total_distance_m: float
    transfer_count: int
    stop_sequence: List[str]


@dataclass(frozen=True)
class RouteFinderResult:
    segments: List[PathSegment]
    total_distance_m: float
    transfer_count: int
    stop_sequence: List[str]
    alternatives: List[RouteAlternative] = field(default_factory=list)


def _build_direct_route_result(
    graph: nx.DiGraph,
    route,
    origin_stop_id: str,
    destination_stop_id: str,
) -> Optional[RouteFinderResult]:
    """
    Build a direct result from one route.

    Unlike the old implementation, this checks ALL occurrences of the
    origin and destination. This is required for loop routes.
    """

    ordered = [
        rs
        for rs in sorted(
            route.route_stops,
            key=lambda rs: rs.sequence_no,
        )
        if rs.stop is not None
    ]

    if not ordered:
        return None

    origin_indices = [
        i
        for i, rs in enumerate(ordered)
        if rs.stop_id == origin_stop_id
    ]

    destination_indices = [
        i
        for i, rs in enumerate(ordered)
        if rs.stop_id == destination_stop_id
    ]

    if not origin_indices or not destination_indices:
        return None

    candidates: list[tuple[list, float]] = []

    # ------------------------------------------------------------
    # Normal route direction.
    #
    # Origin must occur before destination in the stored sequence.
    # ------------------------------------------------------------
    for origin_index in origin_indices:
        for destination_index in destination_indices:
            if origin_index >= destination_index:
                continue

            selected = ordered[
                origin_index : destination_index + 1
            ]

            distance = _sequence_distance(
                graph,
                route.route_id,
                selected,
            )

            if distance is not None:
                candidates.append(
                    (selected, distance)
                )

    # ------------------------------------------------------------
    # Reverse direction for explicitly bidirectional routes.
    # ------------------------------------------------------------
    if route.is_bidirectional:
        for origin_index in origin_indices:
            for destination_index in destination_indices:
                if origin_index <= destination_index:
                    continue

                forward_slice = ordered[
                    destination_index : origin_index + 1
                ]

                selected = list(
                    reversed(forward_slice)
                )

                distance = _sequence_distance(
                    graph,
                    route.route_id,
                    selected,
                )

                if distance is not None:
                    candidates.append(
                        (selected, distance)
                    )

    if not candidates:
        return None

    selected, total_distance_m = min(
        candidates,
        key=lambda item: item[1],
    )

    segments: list[PathSegment] = []

    stop_sequence = [
        rs.stop_id
        for rs in selected
    ]

    for a, b in zip(selected, selected[1:]):
        node_a = (
            a.stop_id,
            route.route_id,
            a.sequence_no,
        )

        node_b = (
            b.stop_id,
            route.route_id,
            b.sequence_no,
        )

        edge = graph.get_edge_data(
            node_a,
            node_b,
        )

        if edge is None:
            return None

        segments.append(
            PathSegment(
                from_stop_id=a.stop_id,
                to_stop_id=b.stop_id,
                route_id=route.route_id,
                is_transfer=False,
                weight=edge["distance_m"],
            )
        )

    return RouteFinderResult(
        segments=segments,
        total_distance_m=total_distance_m,
        transfer_count=0,
        stop_sequence=stop_sequence,
    )


def _sequence_distance(
    graph: nx.DiGraph,
    route_id: str,
    selected: list,
) -> Optional[float]:
    """
    Return total road-network approximation for a sequence of RouteStop
    occurrences.

    Returns None if one of the required sequence edges does not exist.
    """

    total = 0.0

    for a, b in zip(selected, selected[1:]):
        node_a = (
            a.stop_id,
            route_id,
            a.sequence_no,
        )

        node_b = (
            b.stop_id,
            route_id,
            b.sequence_no,
        )

        edge = graph.get_edge_data(
            node_a,
            node_b,
        )

        if edge is None:
            return None

        total += edge["distance_m"]

    return total


def _find_direct_route_candidates(
    session: Session,
    graph: nx.DiGraph,
    origin_stop_id: str,
    destination_stop_id: str,
) -> list[RouteFinderResult]:
    """Every active route that connects these two stops directly,
    sorted shortest-first. Reused by both the primary result path and by
    the alternatives feature, which needs the full candidate list
    instead of only ever seeing the winner.

    Calls gb.get_active_routes (not a separately-imported name) so this
    goes through the exact same binding graph_builder.build_graph uses --
    tests that monkeypatch gb.get_active_routes to stub out the DB cover
    this call too, instead of silently hitting the real DB through a
    second, independent import."""

    candidates: list[RouteFinderResult] = []

    for route in gb.get_active_routes(session):
        result = _build_direct_route_result(
            graph,
            route,
            origin_stop_id,
            destination_stop_id,
        )

        if result is not None:
            candidates.append(result)

    candidates.sort(key=lambda result: result.total_distance_m)
    return candidates


def _physical_stop_id(node) -> str:
    """
    Convert either a physical node or sequence-aware ride node to its
    underlying physical stop ID.
    """

    if isinstance(node, str):
        return node

    return node[0]


def _ride_congestion_ratio(
    graph: nx.DiGraph,
    zones: list,
    congestion_lookup: dict[tuple, float],
    u,
    v,
    edge_data: dict,
) -> float:
    """Shared ratio-blending logic used by both _congestion_weight_fn
    (distance) and _duration_weight_fn (time), so a congestion-aware
    "fastest_estimated" alternative sees exactly the same organic/zone
    signal, combined the same way (max, not additive), as the primary
    avoid_congestion search does. Returns 1.0 (free-flow / no-op) for
    anything that isn't a "ride" edge.
    """
    if edge_data.get("kind") != "ride":
        return 1.0
    organic_ratio = congestion_lookup.get(
        (edge_data.get("route_id"), _physical_stop_id(u), _physical_stop_id(v)),
        1.0,
    )
    zone_ratio = 1.0
    if zones:
        u_data = graph.nodes[u]
        v_data = graph.nodes[v]
        zone_ratio = congestion_zones.ratio_for_segment(
            u_data["lat"], u_data["lng"], v_data["lat"], v_data["lng"], zones=zones
        )
    return max(organic_ratio, zone_ratio)


def _congestion_weight_fn(graph: nx.DiGraph, congestion_lookup: dict[tuple, float]):
    """Build a per-request edge-weight function for nx.shortest_path that
    inflates "ride" edges by the current congestion ratio, leaving
    "board"/"alight"/"walk" edges untouched. See _ride_congestion_ratio
    for how the organic and geographic-zone signals are blended.
    Deliberately NOT baked into the cached graph itself (see
    graph_builder.get_cached_graph): congestion varies by time-of-day and
    the graph is shared/reused across concurrent requests, so mutating
    its static "weight" attribute would leak one request's time bucket
    into another's. A callable weight function keeps this entirely
    request-scoped -- the graph itself is never touched.
    """
    zones = congestion_zones.load_zones()
    def weight(u, v, edge_data: dict) -> float:
        base = edge_data.get("weight", edge_data.get("distance_m", 0))
        if edge_data.get("kind") != "ride":
            return base
        ratio = _ride_congestion_ratio(graph, zones, congestion_lookup, u, v, edge_data)
        if ratio <= 1:
            return base
        return base * (1 + CONGESTION_LAMBDA * (ratio - 1))
    return weight
def _duration_weight_fn(graph: nx.DiGraph = None, congestion_lookup: dict[tuple, float] = None):
    """Edge-weight function estimating travel TIME instead of distance,
    for ranking the "fastest_estimated" alternative -- see the
    BUS_AVG_SPEED_MPS/WALK_SPEED_MPS/BOARD_WAIT_S constants for the
    (labeled-as-approximate) assumptions this relies on. Same
    request-scoped-callable pattern as _congestion_weight_fn, for the
    same reason: never mutate the shared cached graph.

    graph/congestion_lookup are optional and both default to None,
    reproducing the exact original congestion-blind behavior for any
    existing caller that doesn't opt in. When both are given, a
    congested ride edge's estimated duration is scaled by the same
    organic/zone ratio _congestion_weight_fn applies to distance (see
    _ride_congestion_ratio) -- this is what makes "fastest_estimated"
    avoid the same segments avoid_congestion's primary search avoids,
    instead of routing straight through them.
    """
    zones = congestion_zones.load_zones() if congestion_lookup is not None else []
    def weight(u, v, edge_data: dict) -> float:
        kind = edge_data.get("kind")
        if kind == "ride":
            duration = edge_data.get("distance_m", 0) / BUS_AVG_SPEED_MPS
            if congestion_lookup is not None:
                ratio = _ride_congestion_ratio(graph, zones, congestion_lookup, u, v, edge_data)
                if ratio > 1:
                    duration *= (1 + CONGESTION_LAMBDA * (ratio - 1))
            return duration
        if kind == "walk":
            return edge_data.get("distance_m", 0) / WALK_SPEED_MPS
        if kind == "board":
            return BOARD_WAIT_S
        return 0.0  # alight -- instantaneous
    return weight
def _path_to_result(graph: nx.DiGraph, path: list) -> RouteFinderResult:
    """Shared conversion from a raw NetworkX path (a list of graph nodes)
    into a RouteFinderResult, regardless of which weight function chose
    that path. total_distance_m always sums real distance_m (not
    whatever weight ranked the path), so alternatives remain comparable
    to the primary result on the same axis even when they were found by
    minimizing something else (time, congestion-adjusted cost, etc)."""

    stop_sequence: list[str] = []
    for node in path:
        physical_id = _physical_stop_id(node)
        if not stop_sequence or stop_sequence[-1] != physical_id:
            stop_sequence.append(physical_id)

    segments: list[PathSegment] = []
    board_count = 0
    total_distance_m = 0.0

    for u, v in zip(path, path[1:]):
        edge = graph[u][v]
        kind = edge["kind"]
        total_distance_m += edge["distance_m"]

        if kind == "board":
            board_count += 1
        elif kind == "ride":
            segments.append(
                PathSegment(
                    from_stop_id=_physical_stop_id(u),
                    to_stop_id=_physical_stop_id(v),
                    route_id=edge["route_id"],
                    is_transfer=False,
                    weight=edge["distance_m"],
                )
            )
        elif kind == "walk":
            segments.append(
                PathSegment(
                    from_stop_id=u,
                    to_stop_id=v,
                    route_id=None,
                    is_transfer=True,
                    weight=edge["distance_m"],
                )
            )

    return RouteFinderResult(
        segments=segments,
        total_distance_m=total_distance_m,
        transfer_count=max(board_count - 1, 0),
        stop_sequence=stop_sequence,
    )


def _find_with_dijkstra(
    graph: nx.DiGraph,
    origin_stop_id: str,
    destination_stop_id: str,
    weight="weight",
    max_transfers: Optional[int] = None,
) -> RouteFinderResult:
    """Fallback search used only when no direct route exists.

    `weight` is passed straight through to nx.shortest_path: the default
    "weight" string uses each edge's static weight attribute (distance +
    a fixed per-board transfer penalty -- see graph_builder.build_graph);
    pass a callable (e.g. _congestion_weight_fn(...) or
    _duration_weight_fn()) or the "distance_m" string to rank by
    something else instead, as find_shortest_path's alternatives do.

    `max_transfers`: if set, limits the maximum number of transfers
    (boardings - 1) allowed in the path. Uses a custom Dijkstra that
    tracks transfer count as state.
    """

    if max_transfers is not None:
        return _find_with_dijkstra_limited(
            graph,
            origin_stop_id,
            destination_stop_id,
            weight=weight,
            max_transfers=max_transfers,
        )

    try:
        path = nx.shortest_path(
            graph,
            origin_stop_id,
            destination_stop_id,
            weight=weight,
        )
    except nx.NetworkXNoPath as exc:
        raise NoRouteFoundError(
            f"No route found between '{origin_stop_id}' "
            f"and '{destination_stop_id}'"
        ) from exc

    return _path_to_result(graph, path)


def _find_with_dijkstra_limited(
    graph: nx.DiGraph,
    origin_stop_id: str,
    destination_stop_id: str,
    weight,
    max_transfers: int,
) -> RouteFinderResult:
    """
    Custom Dijkstra that tracks transfer count (board edges) as state.

    Each state is (node, board_count). We only expand states where
    board_count - 1 <= max_transfers.
    """

    def get_edge_weight(u, v, edge_data):
        if callable(weight):
            return weight(u, v, edge_data)
        elif weight == "distance_m":
            return edge_data.get("distance_m", 0)
        else:
            return edge_data.get("weight", edge_data.get("distance_m", 0))

    # State: (total_weight, counter, node, board_count, path)
    # counter is a tiebreaker to avoid comparing path lists
    # board_count = number of "board" edges taken so far
    # transfers = max(board_count - 1, 0)
    counter = 0
    pq = [(0.0, counter, origin_stop_id, 0, [origin_stop_id])]
    # visited[(node, board_count)] = best_weight
    visited = {}

    while pq:
        dist, _, node, board_count, path = heapq.heappop(pq)

        if node == destination_stop_id:
            return _path_to_result(graph, path)

        state_key = (node, board_count)
        if state_key in visited and visited[state_key] <= dist:
            continue
        visited[state_key] = dist

        # Check transfer limit
        if board_count - 1 > max_transfers:
            continue

        for neighbor in graph.successors(node):
            edge_data = graph[node][neighbor]
            edge_weight = get_edge_weight(node, neighbor, edge_data)

            new_board_count = board_count
            if edge_data.get("kind") == "board":
                new_board_count = board_count + 1

            # Skip if this would exceed max transfers
            if new_board_count - 1 > max_transfers:
                continue

            new_dist = dist + edge_weight
            new_state = (neighbor, new_board_count)

            if new_state in visited and visited[new_state] <= new_dist:
                continue

            counter += 1
            new_path = path + [neighbor]
            heapq.heappush(pq, (new_dist, counter, neighbor, new_board_count, new_path))

    raise NoRouteFoundError(
        f"No route found between '{origin_stop_id}' "
        f"and '{destination_stop_id}' with max {max_transfers} transfers"
    )


def _alternatives_from_direct_candidates(
    candidates: list[RouteFinderResult],
    max_count: int = 2,
) -> list[RouteAlternative]:
    """candidates[0] is always the primary result (see the STEP 1/STEP 2
    handling in find_shortest_path); this turns candidates[1:] into up
    to max_count alternatives.

    Deduplicated by *physical stop sequence*, not by route_id: two
    different bus routes (different operators, different route_id) can
    still run the identical corridor between two stops -- same stops,
    same order, same road. That's not a meaningfully different option
    for a rider, even though it's technically a different route_id, so
    it's skipped just like a literal repeat would be. This also
    incidentally still catches the old case this replaced (a loop route
    matching twice under the same route_id), since that produces the
    same stop_sequence too."""

    seen_sequences = (
        {tuple(candidates[0].stop_sequence)} if candidates[0].segments else set()
    )
    alternatives: list[RouteAlternative] = []

    for candidate in candidates[1:]:
        if not candidate.segments:
            continue
        sequence = tuple(candidate.stop_sequence)
        if sequence in seen_sequences:
            continue
        seen_sequences.add(sequence)
        alternatives.append(
            RouteAlternative(
                label="alternate_direct_route",
                segments=candidate.segments,
                total_distance_m=candidate.total_distance_m,
                transfer_count=candidate.transfer_count,
                stop_sequence=candidate.stop_sequence,
            )
        )
        if len(alternatives) >= max_count:
            break

    return alternatives


def _alternatives_from_dijkstra(
    graph: nx.DiGraph,
    origin_stop_id: str,
    destination_stop_id: str,
    primary: RouteFinderResult,
    congestion_lookup: dict[tuple, float] = None,
    max_transfers: Optional[int] = None,
) -> list[RouteAlternative]:
    """Up to 2 alternatives to the primary (weight-ranked, i.e. distance
    plus a transfer penalty -- see graph_builder.build_graph) transfer
    path: one ranked by pure distance, one by estimated travel time. Each
    is skipped if it resolves to the exact same stop sequence as the
    primary or an alternative already added -- a "different weighting
    that happens to produce the same path" isn't a real alternative to
    show someone."""

    seen_sequences = {tuple(primary.stop_sequence)}
    alternatives: list[RouteAlternative] = []

    for label, weight in (
        ("shortest_distance", "distance_m"),
        ("fastest_estimated", _duration_weight_fn(graph, congestion_lookup)),
    ):
        try:
            candidate = _find_with_dijkstra(
                graph,
                origin_stop_id,
                destination_stop_id,
                weight=weight,
                max_transfers=max_transfers,
            )
        except NoRouteFoundError:
            continue

        sequence = tuple(candidate.stop_sequence)
        if sequence in seen_sequences:
            continue
        seen_sequences.add(sequence)

        alternatives.append(
            RouteAlternative(
                label=label,
                segments=candidate.segments,
                total_distance_m=candidate.total_distance_m,
                transfer_count=candidate.transfer_count,
                stop_sequence=candidate.stop_sequence,
            )
        )

    return alternatives


def find_route_via_stops(
    session: Session,
    stop_ids: List[str],
    avoid_congestion: bool = False,
    max_transfers: Optional[int] = None,
) -> RouteFinderResult:
    """Chain find_shortest_path across consecutive waypoints, so a rider
    can require the trip to pass through one or more intermediate stops
    in order (origin -> via_1 -> via_2 -> ... -> destination), the same
    way an "add a stop" feature works in a driving-directions app.

    `stop_ids` must have at least 2 entries (origin, ..., destination);
    each consecutive pair is solved independently with the existing
    direct-route-first-then-Dijkstra logic and the results are
    concatenated. No alternatives are computed for a via search -- with
    N legs already chained together, "up to 2 alternatives" per leg
    would combinatorially explode into a confusing number of whole-trip
    options, so callers should not pass include_alternatives-style
    behavior on top of this.

    transfer_count is the sum of each leg's own transfer_count, PLUS one
    per waypoint boundary: forcing the path through a specific stop is
    treated as always requiring the rider to at least re-board there,
    even in the edge case where the same bus happens to continue past
    it -- simpler and safer to over-count than to silently assume
    continuity that isn't guaranteed by the data.
    """

    if len(stop_ids) < 2:
        raise ValueError("find_route_via_stops needs at least an origin and a destination")

    segments: list[PathSegment] = []
    stop_sequence: list[str] = []
    total_distance_m = 0.0
    transfer_count = 0

    for i, (leg_origin, leg_destination) in enumerate(zip(stop_ids, stop_ids[1:])):
        if leg_origin == leg_destination:
            # Same physical stop requested twice in a row (e.g. a
            # duplicate via) -- nothing to route, just fold it in.
            if not stop_sequence:
                stop_sequence.append(leg_origin)
            continue

        try:
            leg_result = find_shortest_path(
                session, leg_origin, leg_destination, avoid_congestion=avoid_congestion, max_transfers=max_transfers
            )
        except NoRouteFoundError as exc:
            waypoint_desc = (
                "origin and first stop" if i == 0
                else "last stop and destination" if i == len(stop_ids) - 2
                else "two of the requested stops"
            )
            raise NoRouteFoundError(
                f"No route found between {waypoint_desc} "
                f"('{leg_origin}' -> '{leg_destination}')"
            ) from exc

        segments.extend(leg_result.segments)
        total_distance_m += leg_result.total_distance_m
        transfer_count += leg_result.transfer_count
        if i > 0:
            transfer_count += 1  # forced re-board at this waypoint, see docstring

        if not stop_sequence:
            stop_sequence.extend(leg_result.stop_sequence)
        else:
            # leg_result.stop_sequence[0] is leg_origin, already the last
            # entry in stop_sequence from the previous leg -- don't
            # duplicate it.
            stop_sequence.extend(leg_result.stop_sequence[1:])

    return RouteFinderResult(
        segments=segments,
        total_distance_m=total_distance_m,
        transfer_count=transfer_count,
        stop_sequence=stop_sequence,
    )


def find_shortest_path(
    session: Session,
    origin_stop_id: str,
    destination_stop_id: str,
    avoid_congestion: bool = False,
    include_alternatives: bool = False,
    max_transfers: Optional[int] = None,
) -> RouteFinderResult:
    """
    avoid_congestion: when True and no direct route exists, the Dijkstra
    transfer-search fallback weights "ride" edges by current congestion
    (see _find_with_dijkstra / _congestion_weight_fn) instead of raw
    distance alone. Direct routes are unaffected either way -- a direct
    route always wins regardless of congestion, per STEP 2 below;
    congestion only ever influences *which transfer path* is chosen when
    there's a real choice to make. Defaults to False so existing callers
    keep today's distance-only behavior unless they opt in.

    include_alternatives: when True, populates the returned result's
    `.alternatives` with up to 2 additional options -- see
    RouteAlternative's docstring for what each label means and how
    they're computed. Defaults to False so existing callers get exactly
    today's response shape (an empty list) unless they opt in; computing
    alternatives is extra Dijkstra work this skips when unused.

    max_transfers: maximum number of transfers allowed (0 = direct only,
    1 = one transfer, etc.). Only applies when no direct route exists
    and Dijkstra fallback is used. Defaults to None (no limit).
    """

    graph = gb.get_cached_graph(session)

    if origin_stop_id not in graph:
        raise NoRouteFoundError(
            f"Origin stop '{origin_stop_id}' "
            f"not found in routing graph"
        )

    if destination_stop_id not in graph:
        raise NoRouteFoundError(
            f"Destination stop '{destination_stop_id}' "
            f"not found in routing graph"
        )

    # Same physical stop.
    if origin_stop_id == destination_stop_id:
        return RouteFinderResult(
            segments=[],
            total_distance_m=0.0,
            transfer_count=0,
            stop_sequence=[origin_stop_id],
        )

    # ------------------------------------------------------------
    # STEP 1: DIRECT ROUTE
    # ------------------------------------------------------------
    direct_candidates = _find_direct_route_candidates(
        session,
        graph,
        origin_stop_id,
        destination_stop_id,
    )

    # ------------------------------------------------------------
    # STEP 2: DIRECT ROUTE ALWAYS WINS
    # ------------------------------------------------------------
    if direct_candidates:
        primary = direct_candidates[0]
        if include_alternatives:
            primary = RouteFinderResult(
                segments=primary.segments,
                total_distance_m=primary.total_distance_m,
                transfer_count=primary.transfer_count,
                stop_sequence=primary.stop_sequence,
                alternatives=_alternatives_from_direct_candidates(direct_candidates),
            )
        return primary

    # ------------------------------------------------------------
    # STEP 3: NO DIRECT ROUTE -> DIJKSTRA
    # ------------------------------------------------------------
    congestion_lookup = None
    if avoid_congestion:
        day_of_week, hour_bucket = day_and_bucket_for(now_in_nepal())
        congestion_lookup = get_congestion_ratios(
            session, day_of_week=day_of_week, hour_bucket=hour_bucket
        )

    weight_arg = (
        _congestion_weight_fn(graph, congestion_lookup)
        if congestion_lookup is not None
        else "weight"
    )
    primary = _find_with_dijkstra(
        graph,
        origin_stop_id,
        destination_stop_id,
        weight=weight_arg,
        max_transfers=max_transfers,
    )

    if include_alternatives:
        primary = RouteFinderResult(
            segments=primary.segments,
            total_distance_m=primary.total_distance_m,
            transfer_count=primary.transfer_count,
            stop_sequence=primary.stop_sequence,
            alternatives=_alternatives_from_dijkstra(
                graph, origin_stop_id, destination_stop_id, primary,
                congestion_lookup=congestion_lookup,
                max_transfers=max_transfers,
            ),
        )

    return primary
