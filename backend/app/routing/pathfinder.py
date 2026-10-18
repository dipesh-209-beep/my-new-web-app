"""
routing/pathfinder.py

Route finding logic:

1. Enumerate every active route containing both origin and destination
   (direct candidates) and the multi-transfer path the NetworkX
   shortest-path search finds.
2. Score every candidate on the SAME basis -- the graph's straight-line
   (haversine) stop-to-stop distance plus a fixed per-boarding transfer
   penalty, with congestion inflation only when avoid_congestion=True
   (see app/routing/ranking.py for the heuristic and its limits; the
   distance is not OSRM road distance) -- and return the lowest-scoring
   one. A viable direct route usually wins (it boards fewer buses) but is
   no longer preferred by fiat, so a much shorter or less-congested
   transfer can take the top spot.
3. Each returned result carries a machine-readable rank_reason
   explaining which factor decided the choice.

Loop routes are handled correctly even when the same physical stop occurs
multiple times in the same route.
"""

from dataclasses import dataclass, field, replace
from typing import List, Optional
import heapq

import networkx as nx
from sqlalchemy.orm import Session

from app.db.queries import get_congestion_ratios
from app.routing import graph_builder as gb
from app.routing import congestion_zones
from app.routing import ranking
from app.routing.constants import (
    BOARD_WAIT_S,
    BUS_AVG_SPEED_MPS,
    CONGESTION_LAMBDA,
    TRANSFER_PENALTY,
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
    # The blended organic/zone congestion ratio this ride edge was ranked
    # with, when the search opted into congestion (otherwise 1.0). Carried
    # so the API can put the *displayed* leg/total durations on the same
    # congestion-aware basis the ranking used -- see
    # _annotate_segment_congestion. Not part of the public response.
    congestion_ratio: float = 1.0


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
    # Why this primary/result was chosen over the other candidates, as a
    # machine-readable code (see app/routing/ranking.py). None when no
    # comparison was made (e.g. a same-stop request).
    rank_reason: Optional[str] = None


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
def _congestion_aware_cost(
    graph: nx.DiGraph,
    zones: list,
    congestion_lookup: dict[tuple, float],
    result: RouteFinderResult,
) -> float:
    """Total cost of a route candidate on the SAME basis the
    congestion-aware Dijkstra fallback minimizes, so a direct candidate
    and a transfer candidate can be compared head-to-head.

    For each ride edge the walked/ridden distance is inflated by the
    blended organic/zone ratio, exactly as _congestion_weight_fn does for
    a graph path; walk legs contribute their raw distance; and
    TRANSFER_PENALTY is added once per boarding (a path with
    transfer_count transfers has transfer_count + 1 boardings). A direct
    candidate therefore pays the same one-boarding penalty a graph path
    from its origin would pay via a "board" edge -- the penalty is
    per boarded bus, not per transfer, so neither candidate type gets a
    free pass.

    A candidate that boards no bus at all -- e.g. a pure walk between two
    stops close enough to be an interchange -- incurs no boarding penalty:
    transfer_count is clamped at 0 and cannot distinguish "no boardings"
    from "one boarding", so the presence of a ride segment is used as the
    marker instead (a bus boarding always produces at least one ride edge).

    With a free-flow or empty lookup every ride edge's inflation factor
    is 1.0 and the score collapses to the plain distance-plus-boarding
    cost the default Dijkstra ranks by, which is what keeps route choice
    stable when congestion data provides no distinction. That collapse is
    exactly app/routing/ranking.py:static_ranking_score, the documented
    ranking policy every candidate is scored on.
    """
    total = TRANSFER_PENALTY * ranking.boardings(result)
    for segment in result.segments:
        if segment.route_id is None:
            total += segment.weight  # walk/interchange leg
            continue
        ratio = _ride_congestion_ratio(
            graph,
            zones,
            congestion_lookup,
            segment.from_stop_id,
            segment.to_stop_id,
            {"kind": "ride", "route_id": segment.route_id},
        )
        if ratio > 1:
            total += segment.weight * (1 + CONGESTION_LAMBDA * (ratio - 1))
        else:
            total += segment.weight
    return total


def _annotate_segment_congestion(
    graph: nx.DiGraph,
    zones: list,
    congestion_lookup: dict[tuple, float],
    result,
):
    """Return `result` (a RouteFinderResult or RouteAlternative) with each
    ride segment's `congestion_ratio` set to the same blended organic/zone
    ratio the congestion-aware edge weighting ranked it with.

    Ranking already accounts for congestion (see _congestion_weight_fn /
    _duration_weight_fn), but the API computes the durations it *displays*
    later, from segment distances alone. Without this the
    "fastest_estimated" alternative can be ranked on congestion-inflated
    times yet shown with a smaller congestion-free time than the option it
    beat. Carrying the per-segment ratio lets the API scale the displayed
    durations onto the same basis.
    """
    ratio_by_key: dict[tuple, float] = {}
    for u, v, data in graph.edges(data=True):
        if data.get("kind") != "ride":
            continue
        key = (_physical_stop_id(u), _physical_stop_id(v), data.get("route_id"))
        if key not in ratio_by_key:
            ratio_by_key[key] = _ride_congestion_ratio(
                graph, zones, congestion_lookup, u, v, data
            )

    annotated = [
        seg
        if seg.route_id is None
        else replace(
            seg,
            congestion_ratio=ratio_by_key.get(
                (seg.from_stop_id, seg.to_stop_id, seg.route_id), 1.0
            ),
        )
        for seg in result.segments
    ]
    return replace(result, segments=annotated)


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


def _count_transfers(segments: list[PathSegment]) -> int:
    """Transfers in an already-spliced segment sequence, using the same
    definition as _path_to_result: transfer_count = boardings - 1, where
    each boarding is one ride run. A ride run is a maximal stretch of ride
    segments on the same route; a walk ends the current run (the rider is
    off the bus) and a route change starts a new one, so a walk between
    two ride runs is a transfer while a walk before the first ride or
    after the last one is an approach/egress walk and is not. The graph
    path _path_to_result sees carries explicit board edges, which the
    concatenated segments do not, so the same rule is applied here by
    counting runs instead of board edges.
    """
    ride_runs = 0
    current_route: Optional[str] = None
    for seg in segments:
        if seg.route_id is None:
            current_route = None
            continue
        if seg.route_id != current_route:
            ride_runs += 1
            current_route = seg.route_id
    return max(ride_runs - 1, 0)


def _find_with_dijkstra(
    graph: nx.DiGraph,
    origin_stop_id: str,
    destination_stop_id: str,
    weight="weight",
    max_transfers: Optional[int] = None,
) -> RouteFinderResult:
    """NetworkX shortest-path search used to find transfer routes.

    Called as a fallback when no viable direct route exists (the default
    behavior), and also under avoid_congestion=True even when a direct route
    exists, so its congestion-aware cost can be compared against direct
    candidates.

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

    A waypoint is a required intermediate stop, not a forced bus change.
    transfer_count is derived from the spliced path exactly as
    _path_to_result derives it for a single leg (boardings - 1, see
    _count_transfers): a new ride run on a different route, or one
    reached by walking, is one transfer, while the same route continuing
    straight through a waypoint is one uninterrupted ride and counts
    zero. A walk before the first ride or after the last one is an
    approach/egress walk, not a bus transfer, so transfer_count, the
    built legs, the summed fare and the boarding waits all agree (see
    api/routing.py:_build_legs / _journey_fare /
    _total_estimated_duration_s, which merge by route_id and charge once
    per ride leg).
    """

    if len(stop_ids) < 2:
        raise ValueError("find_route_via_stops needs at least an origin and a destination")

    segments: list[PathSegment] = []
    stop_sequence: list[str] = []
    total_distance_m = 0.0

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
        # Derived from the whole spliced path, not summed per leg or per
        # via waypoint: a leading/trailing approach or egress walk is not
        # a bus transfer, and the same route continuing through a waypoint
        # is one ride run. See _count_transfers.
        transfer_count=_count_transfers(segments),
        stop_sequence=stop_sequence,
        # A via search always honours the rider's explicitly requested
        # intermediate stops; that, not a distance/transfer comparison,
        # is why this result is shaped the way it is.
        rank_reason=ranking.VIA_STOPS,
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
    Ranking: see app/routing/ranking.py for the full policy. Every
    eligible candidate -- each active route connecting the stops directly,
    and the multi-transfer path the Dijkstra search finds -- is scored on
    the SAME basis: distance plus TRANSFER_PENALTY per bus boarded. The
    lowest score wins, so a viable direct route is compared against
    transfer options rather than winning unconditionally; it usually still
    wins, because it boards fewer buses, but a much shorter or
    less-congested transfer can take the top spot. Direct-only was the
    historical behavior and is no longer applied by fiat.

    avoid_congestion: when True, the shared score is additionally
    congestion-aware -- each ride edge's distance is inflated by the
    blended organic/zone ratio (see _congestion_weight_fn and
    _congestion_aware_cost) -- so a heavily congested direct route can lose
    to a less-congested transfer even when it is shorter. Defaults to
    False, which scores every candidate congestion-free (every ratio is
    1.0); congestion never influences ranking unless the caller asks.

    include_alternatives: when True, populates the returned result's
    `.alternatives` with up to 2 additional options -- see
    RouteAlternative's docstring for what each label means and how
    they're computed. Defaults to False so existing callers get exactly
    today's response shape (an empty list) unless they opt in; computing
    alternatives is extra Dijkstra work this skips when unused.

    max_transfers: maximum number of transfers allowed (0 = direct only,
    1 = one transfer, etc.). Applied to the Dijkstra transfer search, which
    now always runs so the transfer option is available for comparison. It
    does not cap a single direct ride, and a failed (over-capped) transfer
    search never removes a viable direct route. Defaults to None (no
    limit).
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
    # STEP 1: ENUMERATE EVERY ELIGIBLE CANDIDATE
    #
    # Every direct route connecting the stops, PLUS the multi-transfer
    # path (if any) the Dijkstra search finds. Both kinds are then scored
    # on one basis (app/routing/ranking.py), so a direct route is
    # compared against transfers instead of winning by fiat.
    # ------------------------------------------------------------
    direct_candidates = _find_direct_route_candidates(
        session,
        graph,
        origin_stop_id,
        destination_stop_id,
    )

    # Congestion is opt-in. Without it every ratio is 1.0 and the score
    # collapses to plain distance plus a penalty per boarding.
    congestion_lookup = None
    if avoid_congestion:
        day_of_week, hour_bucket = day_and_bucket_for(now_in_nepal())
        congestion_lookup = get_congestion_ratios(
            session, day_of_week=day_of_week, hour_bucket=hour_bucket
        )
    zones = congestion_zones.load_zones() if avoid_congestion else []
    ranking_lookup = congestion_lookup if congestion_lookup is not None else {}

    weight_arg = (
        _congestion_weight_fn(graph, congestion_lookup)
        if congestion_lookup is not None
        else "weight"
    )

    try:
        transfer = _find_with_dijkstra(
            graph,
            origin_stop_id,
            destination_stop_id,
            weight=weight_arg,
            max_transfers=max_transfers,
        )
    except NoRouteFoundError:
        # No transfer path (disconnected, or the max_transfers cap is
        # tighter than the network requires). That is only fatal when no
        # direct route exists either -- a viable direct route must not
        # disappear because the transfer search failed.
        if not direct_candidates:
            raise
        transfer = None

    # Deduplicate by physical stop sequence: a transfer path that resolves
    # to the exact same stop-for-stop corridor as a direct route is the
    # same option to a rider, not two.
    candidates: list[RouteFinderResult] = []
    seen_sequences: set[tuple] = set()
    for candidate in [
        *direct_candidates,
        *([transfer] if transfer is not None else []),
    ]:
        if not candidate.segments:
            continue
        key = tuple(candidate.stop_sequence)
        if key in seen_sequences:
            continue
        seen_sequences.add(key)
        candidates.append(candidate)

    if not candidates:
        raise NoRouteFoundError(
            f"No route found between '{origin_stop_id}' "
            f"and '{destination_stop_id}'"
        )

    def _score(candidate: RouteFinderResult) -> float:
        return _congestion_aware_cost(graph, zones, ranking_lookup, candidate)

    def _ordered(items: list[RouteFinderResult]) -> list[RouteFinderResult]:
        # Deterministic: score, then fewer transfers, then shorter
        # distance, then the stop sequence itself, so two otherwise
        # identical options always resolve the same way.
        return sorted(
            items,
            key=lambda candidate: (
                _score(candidate),
                candidate.transfer_count,
                candidate.total_distance_m,
                tuple(candidate.stop_sequence),
            ),
        )

    ordered = _ordered(candidates)
    primary = ordered[0]
    runner = ordered[1] if len(ordered) > 1 else None

    # ------------------------------------------------------------
    # STEP 2: ALTERNATIVES
    #
    # Computed before the explanation so the reason can account for them.
    # On a transfer-only origin/destination the ranked candidate set holds
    # just the single weight-optimal transfer path, yet this pass can
    # still surface genuinely different routes. The primary is not the
    # "only option" when the response tells the rider about others, so
    # the reason must see them.
    # ------------------------------------------------------------
    alternatives: list[RouteAlternative] = []
    if include_alternatives:
        primary_is_direct = any(
            primary is candidate for candidate in direct_candidates
        )
        if primary_is_direct:
            # The primary is a direct route; the other direct routes are
            # its alternatives. Reorder so the chosen one leads (it is
            # not necessarily the shortest-by-distance direct candidate
            # once congestion is in play).
            alternatives = _alternatives_from_direct_candidates(
                [primary]
                + [c for c in direct_candidates if c is not primary]
            )
        elif direct_candidates:
            # The primary is a transfer but direct routes exist (they
            # lost the fair comparison). Surface them as direct
            # alternatives so a real direct bus is never hidden just
            # because a shorter transfer took the top spot.
            alternatives = _alternatives_from_direct_candidates(
                [primary] + direct_candidates
            )
        else:
            alternatives = _alternatives_from_dijkstra(
                graph,
                origin_stop_id,
                destination_stop_id,
                primary,
                congestion_lookup=congestion_lookup,
                max_transfers=max_transfers,
            )

    # The reason compares the primary against the next genuinely
    # different option the response describes. The next-best ranked
    # candidate is the natural runner-up; when the ranking only ever
    # considered one path (transfer-only), the best alternative stands in
    # so "only_option" is said only when the response really shows a
    # single option.
    explanation_runner = runner
    if explanation_runner is None and alternatives:
        explanation_runner = min(alternatives, key=_score)

    # Did congestion actually change the winner vs the congestion-free
    # basis? Only meaningful when the caller opted in AND there was a
    # choice; drives the "avoids_congestion" explanation. The comparison
    # pool is whatever the runner-up came from, so a reason that compares
    # against an alternative is checked on the same static basis.
    congestion_flipped = False
    if explanation_runner is not None and congestion_lookup is not None:
        pool = candidates if runner is not None else [primary, explanation_runner]
        static_ordered = sorted(
            pool,
            key=lambda candidate: (
                ranking.static_ranking_score(candidate),
                candidate.transfer_count,
                candidate.total_distance_m,
                tuple(candidate.stop_sequence),
            ),
        )
        congestion_flipped = static_ordered[0] is not primary

    rank_reason = ranking.explain_primary(
        primary,
        explanation_runner,
        congestion_aware=congestion_lookup is not None,
        congestion_flipped=congestion_flipped,
    )

    # Carry each ride edge's congestion ratio onto the segments the API will
    # turn into displayed durations, so ranking and display agree when the
    # caller opted into congestion (AUD-021). Done after all ranking and the
    # rank explanation, which don't read the annotation.
    if congestion_lookup is not None:
        primary = _annotate_segment_congestion(
            graph, zones, congestion_lookup, primary
        )
        alternatives = [
            _annotate_segment_congestion(graph, zones, congestion_lookup, alt)
            for alt in alternatives
        ]

    return RouteFinderResult(
        segments=primary.segments,
        total_distance_m=primary.total_distance_m,
        transfer_count=primary.transfer_count,
        stop_sequence=primary.stop_sequence,
        alternatives=alternatives,
        rank_reason=rank_reason,
    )
