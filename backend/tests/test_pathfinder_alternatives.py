"""
tests/test_pathfinder_alternatives.py

Regression tests for the include_alternatives feature added to
find_shortest_path. Same lightweight monkeypatch approach as
test_routing.py -- no live database required.
"""
from types import SimpleNamespace

import pytest

from app.routing import graph_builder as gb
from app.routing.pathfinder import find_shortest_path, find_route_via_stops, NoRouteFoundError


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


def test_alternatives_empty_by_default(monkeypatch):
    """include_alternatives defaults to False -- existing callers see
    exactly today's response shape (an empty list), not a behavior
    change they didn't ask for."""
    s1 = make_stop("S1", "A", 27.700, 85.310)
    s2 = make_stop("S2", "B", 27.701, 85.311)
    route = make_route("R_A", [s1, s2])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route])

    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S2")
    assert result.alternatives == []


def test_direct_route_alternatives_surface_a_second_real_route(monkeypatch):
    """Two different active routes both connect S1 -> S3 directly: the
    shorter one should win as primary, the longer one should appear as
    an 'alternate_direct_route' alternative -- real data, not estimated."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    s3 = make_stop("S3", "C", 27.7020, 85.3120)

    # Short: S1 -> S3 direct (skips S2 entirely).
    route_short = make_route("R_SHORT", [s1, s3])
    # Long: S1 -> S2 -> S3 (same endpoints, more distance).
    route_long = make_route("R_LONG", [s1, s2, s3])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_short, route_long])

    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        include_alternatives=True,
    )

    assert result.transfer_count == 0
    assert result.segments[0].route_id == "R_SHORT"

    assert len(result.alternatives) == 1
    alt = result.alternatives[0]
    assert alt.label == "alternate_direct_route"
    assert alt.segments[0].route_id == "R_LONG"
    assert alt.total_distance_m > result.total_distance_m


def test_direct_route_alternatives_dedupe_same_route_id(monkeypatch):
    """A loop route can match the same origin/destination pair twice
    (different occurrences) -- that should never produce two
    'alternatives' on the same route_id, since it isn't a meaningfully
    different option for a rider."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    s3 = make_stop("S3", "C", 27.7020, 85.3120)
    # Loop: S1 -> S2 -> S3 -> S1 (S1 occurs twice; not bidirectional so
    # the only forward path S1->S3 is via S2).
    loop_route = make_route("R_LOOP", [s1, s2, s3, s1], bidirectional=False)

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [loop_route])

    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        include_alternatives=True,
    )
    assert result.alternatives == []


def test_transfer_scenario_alternatives_dedupe_to_empty_when_only_one_path_exists(monkeypatch):
    """Mirrors test_routing.py's two_route_network transfer case: there's
    only one viable stop sequence between S1 and S6 (via the S2/S5
    interchange), so shortest_distance and fastest_estimated should both
    collapse (dedupe) onto the same primary path rather than appearing
    as fake 'alternatives' that aren't actually different."""
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    s3 = make_stop("S3", "Tripureshwor", 27.6950, 85.3120)
    route_a = make_route("R_A", [s1, s2, s3])

    s4 = make_stop("S4", "Koteshwor", 27.6780, 85.3480)
    s5 = make_stop("S5", "Sundhara West", 27.70104, 85.31305)  # ~6m from S2
    s6 = make_stop("S6", "Kalanki", 27.6935, 85.2800)
    route_b = make_route("R_B", [s4, s5, s6])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b])

    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S6",
        include_alternatives=True,
    )
    assert result.stop_sequence == ["S1", "S2", "S5", "S6"]
    assert result.alternatives == []


def test_direct_route_alternatives_dedupe_same_corridor_different_route_id(monkeypatch):
    """Two DIFFERENT route_ids that happen to run the exact same physical
    stop-for-stop corridor (e.g. two operators sharing a road) should
    collapse to one option, not appear as a fake second alternative --
    same as the loop-route case above, but keyed on the real difference
    that matters to a rider (the path), not on route_id."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    s3 = make_stop("S3", "C", 27.7020, 85.3120)
    # Same stops, same order, different route_id/operator.
    route_x = make_route("R_X", [s1, s2, s3])
    route_y = make_route("R_Y", [s1, s2, s3])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_x, route_y])

    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        include_alternatives=True,
    )
    assert result.alternatives == []


def test_find_route_via_stops_chains_legs_in_order(monkeypatch):
    """origin -> via -> destination should chain two independent
    find_shortest_path calls and concatenate them into one result,
    without duplicating the via stop in stop_sequence."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    s3 = make_stop("S3", "C", 27.7020, 85.3120)
    route = make_route("R_A", [s1, s2, s3])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route])

    result = find_route_via_stops(session=None, stop_ids=["S1", "S2", "S3"])

    assert result.stop_sequence == ["S1", "S2", "S3"]
    # R_A continues straight through the via stop (S2): the waypoint is a
    # required stop, not a forced bus change, so this is one uninterrupted
    # ride and counts zero transfers.
    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == ["R_A", "R_A"]
    assert len(result.segments) == 2


def test_find_route_via_stops_counts_a_real_route_change(monkeypatch):
    """A via stop where the rider must actually change routes is a real
    transfer, even though neither leg has a transfer inside it."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    s3 = make_stop("S3", "C", 27.7020, 85.3120)
    route_a = make_route("R_A", [s1, s2])
    route_b = make_route("R_B", [s2, s3])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b])

    result = find_route_via_stops(session=None, stop_ids=["S1", "S2", "S3"])

    assert result.stop_sequence == ["S1", "S2", "S3"]
    assert result.transfer_count == 1
    assert [seg.route_id for seg in result.segments] == ["R_A", "R_B"]


def test_find_route_via_stops_ignores_leading_duplicate_waypoint(monkeypatch):
    """A via equal to the origin produces a skipped zero-length leg before
    the first real leg; it must not be mistaken for a boundary and invent
    a transfer."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    route = make_route("R_A", [s1, s2])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route])

    result = find_route_via_stops(session=None, stop_ids=["S1", "S1", "S2"])

    assert result.stop_sequence == ["S1", "S2"]
    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == ["R_A"]


def test_find_route_via_stops_ignores_repeated_leading_duplicate_waypoints(
    monkeypatch,
):
    """Several leading duplicates in a row must behave the same as one: the
    first real leg is not a transfer just because earlier legs were
    skipped."""
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    route = make_route("R_A", [s1, s2])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route])

    result = find_route_via_stops(session=None, stop_ids=["S1", "S1", "S1", "S2"])

    assert result.stop_sequence == ["S1", "S2"]
    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == ["R_A"]


def test_find_route_via_stops_head_walk_is_not_a_transfer(monkeypatch):
    """A walk-only first leg (origin and via are neighbouring stops) then a
    ride boards a single bus, so it is zero transfers -- walking up to the
    first bus is not a transfer."""
    origin = make_stop("HW1", "A", 27.7000, 85.3000)
    far = make_stop("HWX", "FarX", 27.9000, 85.3000)
    via = make_stop("HW2", "B", 27.7010, 85.3000)  # ~111 m from HW1
    dest = make_stop("HW3", "C", 27.7200, 85.3000)
    routes = [make_route("R_STUB", [origin, far]), make_route("R_A", [via, dest])]
    monkeypatch.setattr(gb, "get_active_routes", lambda session: routes)

    result = find_route_via_stops(session=None, stop_ids=["HW1", "HW2", "HW3"])

    assert result.stop_sequence == ["HW1", "HW2", "HW3"]
    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == [None, "R_A"]


def test_find_route_via_stops_tail_walk_is_not_a_transfer(monkeypatch):
    """A ride then a walk-only final leg (via and destination are
    neighbouring stops) boards a single bus, so it is zero transfers."""
    origin = make_stop("TW1", "A", 27.7000, 85.3000)
    via = make_stop("TW2", "B", 27.7200, 85.3000)
    dest = make_stop("TW3", "C", 27.7210, 85.3000)  # ~111 m from TW2
    far = make_stop("TWY", "FarY", 27.9000, 85.3000)
    routes = [make_route("R_A", [origin, via]), make_route("R_STUB", [dest, far])]
    monkeypatch.setattr(gb, "get_active_routes", lambda session: routes)

    result = find_route_via_stops(session=None, stop_ids=["TW1", "TW2", "TW3"])

    assert result.stop_sequence == ["TW1", "TW2", "TW3"]
    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == ["R_A", None]


def test_find_route_via_stops_walk_ride_walk_is_zero_transfers(monkeypatch):
    """Approach walk -> one bus -> egress walk boards a single bus, so it is
    zero transfers."""
    w1 = make_stop("WRW1", "A", 27.7000, 85.3000)
    w1far = make_stop("WRWF1", "FarA", 27.9000, 85.3000)
    r1 = make_stop("WRW2", "B", 27.7010, 85.3000)  # ~111 m from WRW1
    r2 = make_stop("WRW3", "C", 27.7200, 85.3000)
    w2 = make_stop("WRW4", "D", 27.7210, 85.3000)  # ~111 m from WRW3
    w2far = make_stop("WRWF2", "FarD", 27.9000, 85.3100)
    routes = [
        make_route("R_S1", [w1, w1far]),
        make_route("R_A", [r1, r2]),
        make_route("R_S2", [w2, w2far]),
    ]
    monkeypatch.setattr(gb, "get_active_routes", lambda session: routes)

    result = find_route_via_stops(
        session=None, stop_ids=["WRW1", "WRW2", "WRW3", "WRW4"]
    )

    assert result.transfer_count == 0
    assert [seg.route_id for seg in result.segments] == [None, "R_A", None]


def test_find_route_via_stops_ride_walk_ride_counts_one_transfer(monkeypatch):
    """Two buses joined by an interchange walk is one transfer."""
    s1 = make_stop("RWR1", "A", 27.7000, 85.3000)
    s2 = make_stop("RWR2", "B", 27.7200, 85.3000)
    s2x = make_stop("RWR2X", "B2", 27.7200, 85.3010)  # ~100 m from RWR2
    s3 = make_stop("RWR3", "C", 27.7400, 85.3000)
    routes = [make_route("R_A", [s1, s2]), make_route("R_B", [s2x, s3])]
    monkeypatch.setattr(gb, "get_active_routes", lambda session: routes)

    result = find_route_via_stops(session=None, stop_ids=["RWR1", "RWR2", "RWR3"])

    assert result.transfer_count == 1
    assert [seg.route_id for seg in result.segments] == ["R_A", None, "R_B"]


def test_find_route_via_stops_requires_at_least_origin_and_destination():
    with pytest.raises(ValueError):
        find_route_via_stops(session=None, stop_ids=["S1"])


def test_find_route_via_stops_reports_which_leg_failed(monkeypatch):
    s1 = make_stop("S1", "A", 27.7000, 85.3100)
    s2 = make_stop("S2", "B", 27.7010, 85.3110)
    route = make_route("R_A", [s1, s2])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route])

    with pytest.raises(NoRouteFoundError, match="last stop and destination"):
        find_route_via_stops(session=None, stop_ids=["S1", "S2", "S_UNKNOWN"])
