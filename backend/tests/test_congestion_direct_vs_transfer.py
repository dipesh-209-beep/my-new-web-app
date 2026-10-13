"""
tests/test_congestion_direct_vs_transfer.py

Unit tests (no live DB) for the congestion-aware direct-vs-transfer
selection in find_shortest_path: with avoid_congestion=True, direct and
transfer candidates are scored on the SAME cost basis
(_congestion_aware_cost -- congestion-inflated ride distance plus
TRANSFER_PENALTY per boarding) instead of a direct route winning
unconditionally. With avoid_congestion=False nothing changes: a direct
route still wins outright and there is no new reranking.

Same lightweight stand-in approach as tests/test_routing.py -- no DB, no
PostGIS: routes are SimpleNamespace doubles and get_active_routes is
monkeypatched, so everything runs against graphs build_graph produces in
memory. congestion_zones.load_zones is stubbed to [] so these tests
exercise the organic ratio signal deterministically.

The network used by most tests:

  Route A: S1 -> S2 -> S3   (direct S1..S3 exists)
  Route B: S2x -> S3        (S2x ~6m from S2 -- a real interchange)

  Direct path:  S1 --ride A--> S2 --ride A--> S3          (1 boarding)
  Transfer:     S1 --ride A--> S2 --walk--> S2x --ride B--> S3
                                                        (2 boardings)

S3 is deliberately ~8km from S2 so the transfer's extra boarding penalty
is small relative to the ridden distances and congestion can realistically
flip the decision.
"""

from types import SimpleNamespace

import pytest

from app.routing import graph_builder as gb
from app.routing import congestion_zones as cz
from app.routing import pathfinder as pf
from app.routing.constants import CONGESTION_LAMBDA, TRANSFER_PENALTY
from app.routing.pathfinder import (
    NoRouteFoundError,
    _build_direct_route_result,
    _congestion_aware_cost,
    _congestion_weight_fn,
    _path_to_result,
    find_shortest_path,
)


def make_stop(stop_id, name, lat, lng):
    return SimpleNamespace(stop_id=stop_id, stop_name=name, lat=lat, lng=lng)


def make_route(route_id, stops, bidirectional=True, status="active"):
    route_stops = [
        SimpleNamespace(stop_id=s.stop_id, sequence_no=i, stop=s)
        for i, s in enumerate(stops, start=1)
    ]
    return SimpleNamespace(
        route_id=route_id,
        route_stops=route_stops,
        is_bidirectional=bidirectional,
        status=status,
    )


def patch_congestion(monkeypatch, ratios):
    """Stub the organic congestion source (real call hits the DB) and
    disable static zones so tests see exactly the ratios they set."""
    monkeypatch.setattr(cz, "load_zones", lambda: [])
    monkeypatch.setattr(
        pf,
        "get_congestion_ratios",
        lambda session, day_of_week, hour_bucket: ratios,
    )


@pytest.fixture(autouse=True)
def _reset_graph_cache():
    gb.invalidate_graph_cache()
    yield
    gb.invalidate_graph_cache()


@pytest.fixture
def direct_plus_transfer_network(monkeypatch):
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    s3 = make_stop("S3", "Far Bazaar", 27.6300, 85.3050)
    route_a = make_route("R_A", [s1, s2, s3])

    s2x = make_stop("S2x", "Sundhara West", 27.70102, 85.31302)  # ~6m from S2
    route_b = make_route("R_B", [s2x, s3])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b])
    return {"s1": s1, "s2": s2, "s3": s3, "s2x": s2x, "route_a": route_a, "route_b": route_b}


@pytest.fixture
def direct_only_network(monkeypatch):
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    s3 = make_stop("S3", "Far Bazaar", 27.6300, 85.3050)
    route_a = make_route("R_A", [s1, s2, s3])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a])
    return {"s1": s1, "s2": s2, "s3": s3, "route_a": route_a}


@pytest.fixture
def transfer_only_network(monkeypatch):
    """No route contains both S1 and S3, so no direct candidate exists --
    the transfer across the S2/S2x interchange is the only way through."""
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    route_a = make_route("R_A", [s1, s2])

    s2x = make_stop("S2x", "Sundhara West", 27.70102, 85.31302)  # ~6m from S2
    s3 = make_stop("S3", "Far Bazaar", 27.6300, 85.3050)
    route_b = make_route("R_B", [s2x, s3])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b])
    return {"s1": s1, "s2": s2, "s3": s3, "s2x": s2x, "route_a": route_a, "route_b": route_b}


@pytest.fixture
def two_direct_network(monkeypatch):
    """Two different routes both connect S1 -> S3 directly: R_A via S2
    (shorter by raw distance), R_C via a far detour stop S7 (longer, but
    free-flow). Congestion on R_A's S2->S3 leg must make R_C win under
    avoid_congestion=True while R_A keeps winning by default."""
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    s3 = make_stop("S3", "Far Bazaar", 27.6300, 85.3050)
    route_a = make_route("R_A", [s1, s2, s3])

    s7 = make_stop("S7", "Detour", 27.6500, 85.3800)
    route_c = make_route("R_C", [s1, s7, s3])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_c])
    return {"s1": s1, "s2": s2, "s3": s3, "s7": s7, "route_a": route_a, "route_c": route_c}


# --- avoidance disabled: historical behavior preserved ---


def test_direct_route_still_wins_by_default_despite_congested_transfer(direct_plus_transfer_network):
    """avoid_congestion=False (the default): a direct route wins
    outright -- no congestion-based reranking, even though the transfer
    would be cheaper if congestion were considered (that is what the
    congested tests below prove)."""
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    assert result.stop_sequence == ["S1", "S2", "S3"]
    assert result.transfer_count == 0


def test_default_mode_returns_shortest_direct_candidate(two_direct_network):
    """Even when the longer direct route is free-flowing, default mode
    ranks direct candidates by raw distance -- R_A (short) wins."""
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    assert result.stop_sequence == ["S1", "S2", "S3"]
    assert result.transfer_count == 0


# --- avoidance enabled: congested direct vs less-congested transfer ---


def test_congested_direct_loses_to_free_flow_transfer(direct_plus_transfer_network, monkeypatch):
    """R_A's S2->S3 leg is 3x congested. The transfer across the S2/S2x
    interchange avoids it, and despite its extra boarding penalty its
    comparable cost is lower -- so the transfer must be the primary."""
    patch_congestion(
        monkeypatch,
        {
            ("R_A", "S1", "S2"): 1.0,
            ("R_A", "S2", "S3"): 3.0,
            ("R_B", "S2x", "S3"): 1.0,
        },
    )
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
    )
    assert result.stop_sequence == ["S1", "S2", "S2x", "S3"]
    assert result.transfer_count == 1


def test_direct_wins_when_congestion_provides_no_distinction(direct_plus_transfer_network, monkeypatch):
    """All ratios at free flow: direct (1 boarding, ~8.5km) beats the
    transfer (2 boardings + a walk) on the plain distance-plus-boarding
    basis -- stable, sensible routing, not a tie-dependent flip."""
    patch_congestion(
        monkeypatch,
        {
            ("R_A", "S1", "S2"): 1.0,
            ("R_A", "S2", "S3"): 1.0,
            ("R_B", "S2x", "S3"): 1.0,
        },
    )
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
    )
    assert result.stop_sequence == ["S1", "S2", "S3"]
    assert result.transfer_count == 0


# --- missing / partial congestion data ---


def test_direct_route_stays_available_with_no_congestion_data(direct_only_network, monkeypatch):
    """Empty lookup: no invented penalties, no exceptions -- the direct
    route is the only viable path and is returned in full."""
    patch_congestion(monkeypatch, {})
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
    )
    assert result.stop_sequence == ["S1", "S2", "S3"]
    assert result.transfer_count == 0


def test_missing_data_does_not_hide_the_only_transfer(transfer_only_network, monkeypatch):
    """No direct route exists and only part of the transfer has data: the
    transfer still resolves, missing segments treated as free-flow."""
    patch_congestion(monkeypatch, {("R_B", "S2x", "S3"): 1.5})
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
    )
    assert result.stop_sequence == ["S1", "S2", "S2x", "S3"]
    assert result.transfer_count == 1


def test_partial_congestion_data_completes_for_direct(two_direct_network, monkeypatch):
    """Only R_A's first leg has a ratio; the congested-to-be leg is
    missing so it degrades to free flow -- routing completes and the
    cheaper-by-raw-distance direct still wins."""
    patch_congestion(monkeypatch, {("R_A", "S1", "S2"): 2.0})
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
    )
    assert result.stop_sequence == ["S1", "S2", "S3"]


# --- multiple direct candidates: congestion picks the winner ---


def test_congestion_selects_between_multiple_direct_routes(two_direct_network, monkeypatch):
    """R_A is shorter by raw distance but its S2->S3 leg is 3x congested;
    R_C is longer but free-flow. avoid_congestion=True must prefer R_C
    while default mode prefers R_A."""
    ratios = {
        ("R_A", "S1", "S2"): 1.0,
        ("R_A", "S2", "S3"): 3.0,
        ("R_C", "S1", "S7"): 1.0,
        ("R_C", "S7", "S3"): 1.0,
    }
    patch_congestion(monkeypatch, ratios)
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
    )
    assert result.stop_sequence == ["S1", "S7", "S3"]
    assert result.transfer_count == 0
    assert result.segments[0].route_id == "R_C"


def test_congested_direct_winner_alternatives_stay_consistent(two_direct_network, monkeypatch):
    """When the congestion-aware winner is NOT the shortest-by-distance
    direct candidate, alternatives must still come from the other direct
    routes (not accidentally promote the winner itself)."""
    patch_congestion(
        monkeypatch,
        {
            ("R_A", "S1", "S2"): 1.0,
            ("R_A", "S2", "S3"): 3.0,
            ("R_C", "S1", "S7"): 1.0,
            ("R_C", "S7", "S3"): 1.0,
        },
    )
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
        include_alternatives=True,
    )
    assert result.stop_sequence == ["S1", "S7", "S3"]
    labels = [alt.label for alt in result.alternatives]
    assert labels == ["alternate_direct_route"]
    assert result.alternatives[0].stop_sequence == ["S1", "S2", "S3"]


# --- transfer constraints still hold ---


def test_max_transfers_cap_still_blocks_over_transfers(direct_plus_transfer_network, monkeypatch):
    """max_transfers=0 forbids the transfer (it needs one): the search
    must stay on a zero-transfer path even though congestion would
    otherwise prefer the transfer."""
    patch_congestion(
        monkeypatch,
        {
            ("R_A", "S1", "S2"): 1.0,
            ("R_A", "S2", "S3"): 3.0,
            ("R_B", "S2x", "S3"): 1.0,
        },
    )
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
        max_transfers=0,
    )
    assert result.transfer_count == 0
    assert result.stop_sequence == ["S1", "S2", "S3"]


def test_max_transfers_zero_raises_when_no_direct(transfer_only_network, monkeypatch):
    """No direct route + cap 0 == unrouteable, with the original error
    message naming the cap (not a swallowed generic one)."""
    patch_congestion(monkeypatch, {})
    with pytest.raises(NoRouteFoundError, match="with max 0 transfers"):
        find_shortest_path(
            session=None,
            origin_stop_id="S1",
            destination_stop_id="S3",
            avoid_congestion=True,
            max_transfers=0,
        )


def test_max_transfers_one_allows_exactly_the_needed_transfer(transfer_only_network, monkeypatch):
    patch_congestion(monkeypatch, {})
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        avoid_congestion=True,
        max_transfers=1,
    )
    assert result.transfer_count == 1


# --- cost-basis comparability ---


def test_direct_score_matches_graph_edge_scoring(direct_plus_transfer_network, monkeypatch):
    """A direct candidate's _congestion_aware_cost equals the same
    physical route scored edge-by-edge through the graph's congestion
    weight function (board 3000 + inflated rides), which is exactly what
    the transfer Dijkstra minimizes -- proving the two are comparable."""
    net = direct_plus_transfer_network
    ratios = {
        ("R_A", "S1", "S2"): 1.0,
        ("R_A", "S2", "S3"): 3.0,
        ("R_B", "S2x", "S3"): 1.0,
    }
    patch_congestion(monkeypatch, ratios)
    graph = gb.build_graph(session=None)
    weight_fn = _congestion_weight_fn(graph, ratios)
    direct = _build_direct_route_result(graph, net["route_a"], "S1", "S3")

    path = ["S1", ("S1", "R_A", 1), ("S2", "R_A", 2), ("S3", "R_A", 3), "S3"]
    graph_cost = sum(weight_fn(u, v, graph[u][v]) for u, v in zip(path, path[1:]))

    cost = _congestion_aware_cost(graph, [], ratios, direct)
    assert cost == pytest.approx(graph_cost)

    d = gb.haversine_distance_m
    s1, s2, s3 = net["s1"], net["s2"], net["s3"]
    expected = (
        TRANSFER_PENALTY
        + d(s1.lat, s1.lng, s2.lat, s2.lng)
        + d(s2.lat, s2.lng, s3.lat, s3.lng) * (1 + CONGESTION_LAMBDA * (3.0 - 1))
    )
    assert cost == pytest.approx(expected)


def test_direct_score_with_no_data_is_plain_distance_plus_one_boarding(direct_only_network, monkeypatch):
    net = direct_only_network
    graph = gb.build_graph(session=None)
    direct = _build_direct_route_result(graph, net["route_a"], "S1", "S3")
    d = gb.haversine_distance_m
    s1, s2, s3 = net["s1"], net["s2"], net["s3"]
    expected = (
        TRANSFER_PENALTY
        + d(s1.lat, s1.lng, s2.lat, s2.lng)
        + d(s2.lat, s2.lng, s3.lat, s3.lng)
    )
    cost = _congestion_aware_cost(graph, [], {}, direct)
    assert cost == pytest.approx(expected)


def test_pure_walk_path_is_not_charged_a_boarding_penalty(direct_plus_transfer_network):
    """A walk-only journey makes zero bus boardings, so it must score as its
    raw walking distance -- no TRANSFER_PENALTY.

    S2 and S2x are ~6m apart, so build_graph adds a genuine walk edge between
    them (kind="walk", route_id=None) and _path_to_result turns the raw node
    path [S2, S2x] into a walk-only candidate: board_count == 0, so
    transfer_count is clamped to 0 -- indistinguishable, on transfer_count
    alone, from a one-boarding direct route. The old scoring charged
    TRANSFER_PENALTY * (0 + 1) anyway, which is the documented edge case.
    """
    net = direct_plus_transfer_network
    graph = gb.build_graph(session=None)

    path = ["S2", "S2x"]
    assert graph["S2"]["S2x"]["kind"] == "walk"

    result = _path_to_result(graph, path)
    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == [None], "expected a walk-only candidate"

    # The graph's own cost for this exact path is just the walk edge weight.
    graph_cost = sum(graph[u][v]["weight"] for u, v in zip(path, path[1:]))
    s2, s2x = net["s2"], net["s2x"]
    raw_walk = gb.haversine_distance_m(s2.lat, s2.lng, s2x.lat, s2x.lng)

    cost = _congestion_aware_cost(graph, [], {}, result)

    assert cost == pytest.approx(graph_cost)
    assert cost == pytest.approx(raw_walk)
    # The regression: this used to be raw_walk + TRANSFER_PENALTY.
    assert cost < TRANSFER_PENALTY
    assert cost == pytest.approx(raw_walk)

    # And a path that actually boards a bus still pays the boarding penalty,
    # so the fix cannot have simply dropped the penalty everywhere.
    direct = _build_direct_route_result(graph, net["route_a"], "S1", "S3")
    d = gb.haversine_distance_m
    s1, _s2, s3 = net["s1"], net["s2"], net["s3"]
    raw_ride = d(s1.lat, s1.lng, s2.lat, s2.lng) + d(s2.lat, s2.lng, s3.lat, s3.lng)
    assert _congestion_aware_cost(graph, [], {}, direct) == pytest.approx(
        TRANSFER_PENALTY + raw_ride
    )