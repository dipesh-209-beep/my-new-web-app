"""
tests/test_max_transfers.py

Unit tests for the max_transfers query parameter's routing behavior
(pathfinder.find_shortest_path / _find_with_dijkstra_limited). Previously
the transfer-limit search variant had no dedicated tests at all. Same
lightweight monkeypatch approach as test_routing.py -- no live database.

Network under test (three routes, two interchange hops):
    Route A: S1 -> S2 -> S3            (bidirectional)
    Route B: S4 -> S5 -> S6            (bidirectional, S5 ~3m from S2)
    Route C: S7 -> S8 -> S9            (bidirectional, S8 ~3m from S6)

S1 -> S9 necessarily needs 2 transfers: board A at S1, walk S2->S5, board
B, ride to S6, walk S6->S8, board C, ride to S9. Any coordinate pair must
be > INTERCHANGE_DISTANCE (200m) apart to avoid creating an unintended
shortcut walk edge.
"""
from types import SimpleNamespace

import pytest

from app.routing import graph_builder as gb
from app.routing.pathfinder import NoRouteFoundError, find_shortest_path, find_route_via_stops


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


@pytest.fixture(autouse=True)
def _reset_graph_cache():
    gb.invalidate_graph_cache()
    yield
    gb.invalidate_graph_cache()


@pytest.fixture
def three_route_network(monkeypatch):
    s1 = make_stop("S1", "A Start", 27.7050, 85.3145)
    s2 = make_stop("S2", "A Mid", 27.7010, 85.3130)
    s3 = make_stop("S3", "A End", 27.6950, 85.3120)
    route_a = make_route("R_A", [s1, s2, s3])

    s4 = make_stop("S4", "B Start", 27.6780, 85.3480)
    s5 = make_stop("S5", "B Mid", 27.70104, 85.31305)  # ~6m from S2
    s6 = make_stop("S6", "B End", 27.6935, 85.2800)
    route_b = make_route("R_B", [s4, s5, s6])

    s7 = make_stop("S7", "C Start", 27.7500, 85.2800)
    s8 = make_stop("S8", "C Mid", 27.69352, 85.28002)  # ~2m from S6
    s9 = make_stop("S9", "C End", 27.6400, 85.2800)
    route_c = make_route("R_C", [s7, s8, s9])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b, route_c])
    return {"route_a": route_a, "route_b": route_b, "route_c": route_c}


def test_two_transfer_path_found_without_limit(three_route_network):
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S9")
    assert result.transfer_count == 2
    assert result.stop_sequence == ["S1", "S2", "S5", "S6", "S8", "S9"]


def test_max_transfers_zero_blocks_transfers(three_route_network):
    with pytest.raises(NoRouteFoundError, match="max 0 transfers"):
        find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S9", max_transfers=0)


def test_max_transfers_below_requirement_raises(three_route_network):
    """S1 -> S9 needs exactly 2 transfers; capping at 1 must refuse the
    search rather than silently returning a worse path."""
    with pytest.raises(NoRouteFoundError, match="max 1 transfers"):
        find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S9", max_transfers=1)


def test_max_transfers_exactly_meets_requirement(three_route_network):
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S9", max_transfers=2)
    assert result.transfer_count == 2
    assert result.stop_sequence == ["S1", "S2", "S5", "S6", "S8", "S9"]


def test_max_transfers_zero_still_allows_direct_route(three_route_network):
    """A direct route (S1 -> S3 on a single route) involves zero boardings
    and must pass through even with max_transfers=0."""
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3", max_transfers=0)
    assert result.transfer_count == 0
    assert result.stop_sequence == ["S1", "S2", "S3"]


def test_max_transfers_highger_than_needed_is_fine(three_route_network):
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S9", max_transfers=5)
    assert result.transfer_count == 2


def test_via_respects_max_transfers_per_leg(three_route_network):
    """find_route_via_stops threads max_transfers through to each leg's
    own find_shortest_path call -- S1 -> S9 via S5 needs a leg S5 -> S9
    that itself takes 1 transfer, so a per-leg cap of 0 must refuse."""
    with pytest.raises(NoRouteFoundError, match="last stop and destination"):
        find_route_via_stops(session=None, stop_ids=["S1", "S5", "S9"], max_transfers=0)

    result = find_route_via_stops(session=None, stop_ids=["S1", "S5", "S9"], max_transfers=1)
    # S1->S5 is 0 transfers, S5->S9 is 1 transfer, plus the forced re-board
    # at the S5 boundary -> 2 total.
    assert result.transfer_count == 2