"""
Unit tests for app/routing/stop_positioning.py -- route-aware, per-
direction stop positioning. No live database or OSRM instance required:
`route_coords` below stands in for whatever
get_route_geometry(...)["geometry"] would have decoded to for that
specific direction (see geojson_linestring_to_coords) -- what's under
test here is the projection/positioning logic downstream of that
geometry, not OSRM itself. See tests/test_route_stop_positions.py for
how this wires into the real /routes/{route_id}/stops and
/routes/{route_id}/geometry endpoints.
"""
import pytest

from app.routing.stop_positioning import (
    MAX_STOP_OFFSET_M,
    compute_adjusted_stop_positions,
    geojson_linestring_to_coords,
)


def test_geojson_linestring_to_coords_converts_lng_lat_to_lat_lng():
    geometry = {"type": "LineString", "coordinates": [[85.30, 27.70], [85.31, 27.71]]}
    assert geojson_linestring_to_coords(geometry) == [(27.70, 85.30), (27.71, 85.31)]


def test_straight_road_projects_onto_the_line():
    # A short straight north-south road.
    route_coords = [
        (27.7000, 85.3000),
        (27.7001, 85.3000),
        (27.7002, 85.3000),
        (27.7003, 85.3000),
    ]
    # Canonical stop sits a few meters east of the road.
    stops = [("S1", 27.70015, 85.30010)]

    [pos] = compute_adjusted_stop_positions(route_coords, stops)

    assert pos.source == "projected"
    assert pos.lng == pytest.approx(85.3000, abs=1e-5)  # snapped back onto the road
    assert pos.lat == pytest.approx(27.70015, abs=1e-4)  # stayed close to canonical
    assert 0 < pos.offset_m < MAX_STOP_OFFSET_M


def test_opposite_directions_use_different_carriageways():
    """The same canonical stop, projected against two direction-specific
    geometries standing in for the two carriageways of a divided road
    ~17m apart, must land on the carriageway that geometry represents --
    not collapse onto the same point regardless of direction."""
    forward_geometry = [(27.7000, 85.30000), (27.7010, 85.30000), (27.7020, 85.30000)]
    # Reverse carriageway: parallel, offset east, and -- like a real
    # reverse-direction OSRM call -- traversed in the opposite order.
    reverse_geometry = [(27.7020, 85.30020), (27.7010, 85.30020), (27.7000, 85.30020)]

    stop = ("S1", 27.7010, 85.30000)  # sits exactly on the forward carriageway

    [fwd] = compute_adjusted_stop_positions(forward_geometry, [stop])
    [rev] = compute_adjusted_stop_positions(reverse_geometry, [stop])

    assert fwd.source == rev.source == "projected"
    assert fwd.lng == pytest.approx(85.30000, abs=1e-5)
    assert rev.lng == pytest.approx(85.30020, abs=1e-5)
    assert fwd.lng != pytest.approx(rev.lng, abs=1e-6)
    assert fwd.offset_m < MAX_STOP_OFFSET_M
    assert rev.offset_m < MAX_STOP_OFFSET_M


def test_turn_places_stops_on_approach_and_departure_leg():
    """An L-shaped intersection: the approach leg heads east, the
    departure leg heads north from the corner. A stop just before the
    corner should land on the approach leg; a stop just after should
    land on the departure leg."""
    route_coords = [
        (27.70000, 85.30000),  # start of approach (heading east)
        (27.70000, 85.30010),  # corner
        (27.70010, 85.30010),  # end of departure (heading north)
    ]
    stops = [
        ("APPROACH", 27.70000, 85.30008),  # just before the corner
        ("DEPARTURE", 27.70005, 85.30011),  # just after the corner
    ]

    approach_pos, departure_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert approach_pos.source == departure_pos.source == "projected"
    assert approach_pos.lat == pytest.approx(27.70000, abs=1e-5)  # stayed on the east-west leg
    assert departure_pos.lng == pytest.approx(85.30010, abs=1e-5)  # stayed on the north-south leg


def test_does_not_snap_backward_to_a_closer_but_already_passed_segment():
    """Loop-shaped route: an outbound leg, a short connector, and a
    return leg running parallel to (and, for the second stop, closer
    than) the outbound leg. Once the cursor has advanced past the
    outbound leg, a later stop must not snap back onto it even though it
    is geometrically closer than the correct (later) segment -- this is
    the same failure mode that causes wrong-road/wrong-carriageway
    snapping at intersections."""
    route_coords = [
        (0.0000, 0.0000),
        (0.0000, 0.0010),  # end of outbound leg (heading east)
        (-0.0005, 0.0010),  # connector (heading south)
        (-0.0005, 0.0000),  # return leg (heading west), parallel to outbound
    ]
    stops = [
        # Matches the connector leg, advancing the cursor past the outbound leg.
        ("S1", -0.0003, 0.0010),
        # Closer to the (already-passed) outbound leg (~11m) than to the
        # correct, later return leg (~44m) -- must still pick the return leg.
        ("S2", -0.0001, 0.0003),
    ]

    s1_pos, s2_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert s1_pos.source == s2_pos.source == "projected"
    assert s2_pos.lat == pytest.approx(-0.0005, abs=1e-4)  # return leg, not outbound (lat ~ 0)


def test_falls_back_to_canonical_when_nothing_is_within_range():
    route_coords = [(0.0000, 0.0000), (0.0010, 0.0010)]
    stops = [("FAR", 5.0000, 5.0000)]

    [pos] = compute_adjusted_stop_positions(route_coords, stops)

    assert pos.source == "canonical_fallback"
    assert (pos.lat, pos.lng) == (5.0000, 5.0000)
    assert pos.offset_m == 0.0


def test_empty_or_single_point_geometry_falls_back_to_canonical():
    stops = [("S1", 27.7, 85.3)]
    assert compute_adjusted_stop_positions([], stops)[0].source == "canonical_fallback"
    assert compute_adjusted_stop_positions([(27.7, 85.3)], stops)[0].source == "canonical_fallback"


def test_long_detour_between_stops_handled_by_global_search():
    """
    A one-way system forces a long detour: two consecutive stops have a
    short straight-line distance (~215m) but the actual road path is
    ~644m because of a one-way loop. A windowed search with a fixed
    lookahead would miss the correct segment, but the global search from
    the cursor finds it regardless of the detour length.
    """
    # Build a route that goes out, loops around, and comes back parallel.
    # The straight-line distance between stop A and stop B is ~215m, but
    # the road distance is ~644m because of the one-way loop.
    route_coords = [
        (0.00000, 0.00000),   # start
        (0.00000, 0.00100),   # leg 1: east ~111m (vertical at lng=0.0)
        (0.00050, 0.00100),   # leg 2: south ~55m (horizontal at lat=0.001)
        (0.00050, 0.00000),   # leg 3: west ~111m (vertical at lng=0.001, return)
        (0.00100, 0.00000),   # leg 4: south ~55m
        (0.00100, 0.00100),   # leg 5: east ~111m
        (0.00150, 0.00100),   # leg 6: south ~55m
        (0.00150, 0.00000),   # leg 7: west ~111m
    ]
    # Total road distance from start to end of leg 7: ~644m

    # Stop A at start of route
    # Stop B at end of leg 1 (eastward leg) - straight-line ~111m from A
    # Stop C on the return leg (leg 3), clearly on lng=0.001 (leg 3's lng)
    # Straight-line from B to C: ~111m, but road from B to C via loop ~533m
    stops = [
        ("A", 0.00000, 0.00000),      # at start
        ("B", 0.00000, 0.00100),      # end of leg 1
        ("C", 0.00050, 0.00060),      # on return leg (leg 3, lng=0.001), lat ~0.00060
                                       # straight-line from B ~111m, but road from B to C ~533m
    ]

    a_pos, b_pos, c_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert a_pos.source == "projected"
    assert b_pos.source == "projected"
    assert c_pos.source == "projected"

    # C should project onto the return leg (leg 3, lat ~ 0.00050), not the
    # outbound leg (leg 1, lat ~ 0.00000). The cursor has advanced past
    # leg 1 by the time we process C (after processing B), so global
    # search from cursor correctly finds leg 3 even though C's
    # straight-line distance to leg 1 is similar.
    assert c_pos.lat == pytest.approx(0.00050, abs=1e-4), (
        f"C should project onto return leg (lat~0.00050), got {c_pos.lat}"
    )

    # B should project onto leg 1
    assert b_pos.lat == pytest.approx(0.00000, abs=1e-4)
