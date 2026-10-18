"""
tests/test_duration_weight_fn.py

Unit tests for pathfinder._duration_weight_fn -- the per-request
edge-weight function used to rank the "fastest_estimated" alternative
by estimated travel TIME instead of raw distance.

Historically this ignored congestion entirely, which meant a rider
could be shown a "fastest" alternative that routes straight through
the exact segment avoid_congestion's primary search was built to
avoid. It now optionally accepts (graph, congestion_lookup) and, when
both are given, scales a congested ride edge's duration by the same
organic/zone ratio _congestion_weight_fn applies to distance (see
_ride_congestion_ratio, the helper shared between the two). Calling it
with no arguments reproduces the exact original congestion-blind
behavior, so existing callers are unaffected.
"""
import networkx as nx
import pytest

from app.routing import congestion_zones as cz
from app.routing.constants import (
    BOARD_WAIT_S,
    BUS_AVG_SPEED_MPS,
    CONGESTION_LAMBDA,
    WALK_SPEED_MPS,
)
from app.routing.pathfinder import (
    RouteFinderResult,
    PathSegment,
    _annotate_segment_congestion,
    _duration_weight_fn,
)


def _ride_edge(distance_m, route_id="R1"):
    return {"kind": "ride", "distance_m": distance_m, "weight": distance_m, "route_id": route_id}


def _make_graph(a_latlng, b_latlng):
    graph = nx.DiGraph()
    graph.add_node("A", lat=a_latlng[0], lng=a_latlng[1])
    graph.add_node("B", lat=b_latlng[0], lng=b_latlng[1])
    return graph


FAR_AWAY = (27.9000, 85.6000)  # outside every zone used in these tests


@pytest.fixture(autouse=True)
def _reset_cache():
    cz.reset_zones_cache()
    yield
    cz.reset_zones_cache()


def test_default_call_behaves_exactly_as_before():
    """No graph, no congestion_lookup -- the original congestion-blind
    behavior, unchanged for any existing caller that doesn't opt in."""
    weight = _duration_weight_fn()
    assert weight("A", "B", _ride_edge(1000)) == pytest.approx(1000 / BUS_AVG_SPEED_MPS)
    assert weight("A", "B", {"kind": "walk", "distance_m": 200}) == pytest.approx(200 / WALK_SPEED_MPS)
    assert weight("A", "B", {"kind": "board", "distance_m": 0}) == BOARD_WAIT_S
    assert weight("A", "B", {"kind": "alight", "distance_m": 0}) == 0.0


def test_congestion_lookup_none_ignores_zones_even_if_present(monkeypatch):
    """Passing a graph but leaving congestion_lookup=None must still be a
    complete no-op, even if static congestion zones exist and would
    otherwise apply -- None means "not opted in," not "no data yet"."""
    zone = cz.CongestionZone(stop_id="Z", name="Zone", lat=27.70, lng=85.31, radius_m=300, ratio=4.0)
    monkeypatch.setattr(cz, "load_zones", lambda: [zone])
    graph = _make_graph((27.70, 85.31), FAR_AWAY)  # node A sits inside the zone
    weight = _duration_weight_fn(graph, None)
    assert weight("A", "B", _ride_edge(1000)) == pytest.approx(1000 / BUS_AVG_SPEED_MPS)


def test_congested_ride_edge_duration_is_inflated(monkeypatch):
    monkeypatch.setattr(cz, "load_zones", lambda: [])
    graph = _make_graph((27.70, 85.31), FAR_AWAY)
    weight = _duration_weight_fn(graph, {("R1", "A", "B"): 2.0})
    base_duration = 1000 / BUS_AVG_SPEED_MPS
    expected = base_duration * (1 + CONGESTION_LAMBDA * (2.0 - 1))
    assert weight("A", "B", _ride_edge(1000)) == pytest.approx(expected)


def test_ratios_combine_via_max_zone_beats_organic(monkeypatch):
    """Same max()-not-sum blending as _congestion_weight_fn: a strong
    zone ratio should win outright over a weak organic ratio, not stack
    with it."""
    zone = cz.CongestionZone(stop_id="Z", name="Zone", lat=27.70, lng=85.31, radius_m=300, ratio=4.0)
    monkeypatch.setattr(cz, "load_zones", lambda: [zone])
    graph = _make_graph((27.70, 85.31), FAR_AWAY)
    weight = _duration_weight_fn(graph, {("R1", "A", "B"): 1.2})
    base_duration = 1000 / BUS_AVG_SPEED_MPS
    expected = base_duration * (1 + CONGESTION_LAMBDA * (4.0 - 1))  # zone (4.0) beats organic (1.2)
    assert weight("A", "B", _ride_edge(1000)) == pytest.approx(expected)


def test_ratios_combine_via_max_organic_beats_zone(monkeypatch):
    """Mirrored: a strong organic ratio should win over a weak zone
    signal."""
    zone = cz.CongestionZone(stop_id="Z", name="Zone", lat=27.70, lng=85.31, radius_m=300, ratio=1.3)
    monkeypatch.setattr(cz, "load_zones", lambda: [zone])
    graph = _make_graph((27.70, 85.31), FAR_AWAY)
    weight = _duration_weight_fn(graph, {("R1", "A", "B"): 5.0})
    base_duration = 1000 / BUS_AVG_SPEED_MPS
    expected = base_duration * (1 + CONGESTION_LAMBDA * (5.0 - 1))  # organic (5.0) beats zone (1.3)
    assert weight("A", "B", _ride_edge(1000)) == pytest.approx(expected)


def _congestion_graph():
    graph = nx.DiGraph()
    graph.add_node(("A", "R1", 1), lat=27.70, lng=85.31)
    graph.add_node(("B", "R1", 2), lat=27.71, lng=85.32)
    graph.add_edge(
        ("A", "R1", 1),
        ("B", "R1", 2),
        kind="ride",
        route_id="R1",
        distance_m=1000.0,
        weight=1000.0,
    )
    return graph


def test_annotation_carries_ranking_ratio_onto_segments(monkeypatch):
    """_annotate_segment_congestion stamps each ride segment with the same
    blended ratio the ranking used, so the API can scale the durations it
    displays onto that basis (AUD-021)."""
    monkeypatch.setattr(cz, "load_zones", lambda: [])
    result = RouteFinderResult(
        segments=[PathSegment("A", "B", "R1", False, 1000.0)],
        total_distance_m=1000.0,
        transfer_count=0,
        stop_sequence=["A", "B"],
    )
    annotated = _annotate_segment_congestion(
        _congestion_graph(), [], {("R1", "A", "B"): 2.0}, result
    )
    assert annotated.segments[0].congestion_ratio == pytest.approx(2.0)


def test_annotated_ratio_reproduces_ranking_duration(monkeypatch):
    """The displayed duration (distance/speed scaled by the annotated ratio)
    must equal the duration _duration_weight_fn ranked with -- this is the
    whole point of carrying the ratio, and what makes the "fastest_estimated"
    label honest."""
    monkeypatch.setattr(cz, "load_zones", lambda: [])
    graph = _congestion_graph()
    lookup = {("R1", "A", "B"): 2.0}
    result = RouteFinderResult(
        segments=[PathSegment("A", "B", "R1", False, 1000.0)],
        total_distance_m=1000.0,
        transfer_count=0,
        stop_sequence=["A", "B"],
    )
    annotated = _annotate_segment_congestion(graph, [], lookup, result)
    seg = annotated.segments[0]

    display = (
        seg.weight
        / BUS_AVG_SPEED_MPS
        * (1 + CONGESTION_LAMBDA * (seg.congestion_ratio - 1))
    )
    ranked = _duration_weight_fn(graph, lookup)(
        "A", "B", graph[("A", "R1", 1)][("B", "R1", 2)]
    )
    assert display == pytest.approx(ranked)


def test_walk_segment_ratio_is_left_at_one(monkeypatch):
    """A walking-transfer segment is not a ride -- its ratio stays 1.0 so a
    transfer never gets its time inflated."""
    monkeypatch.setattr(cz, "load_zones", lambda: [])
    result = RouteFinderResult(
        segments=[PathSegment("A", "B", None, True, 200.0)],
        total_distance_m=200.0,
        transfer_count=1,
        stop_sequence=["A", "B"],
    )
    annotated = _annotate_segment_congestion(
        _congestion_graph(), [], {("R1", "A", "B"): 5.0}, result
    )
    assert annotated.segments[0].congestion_ratio == 1.0


def test_walk_and_board_edges_stay_unaffected_by_congestion(monkeypatch):
    """Congestion only ever inflates "ride" edges -- walk/board timings
    stay fixed regardless of how bad congestion_lookup or the zones say
    things are."""
    zone = cz.CongestionZone(stop_id="Z", name="Zone", lat=27.70, lng=85.31, radius_m=300, ratio=4.0)
    monkeypatch.setattr(cz, "load_zones", lambda: [zone])
    graph = _make_graph((27.70, 85.31), FAR_AWAY)
    weight = _duration_weight_fn(graph, {("R1", "A", "B"): 5.0})
    assert weight("A", "B", {"kind": "walk", "distance_m": 200}) == pytest.approx(200 / WALK_SPEED_MPS)
    assert weight("A", "B", {"kind": "board", "distance_m": 0}) == BOARD_WAIT_S
