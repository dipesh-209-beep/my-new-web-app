"""
Adversarial regression tests for app/routing/stop_positioning.py.

These pin down failure modes found while auditing stop positioning
against the real Kathmandu dataset (113 routes / 396 stops):

  - same-segment reversal: two stops on ONE road segment, recorded with
    the later stop physically BEHIND the earlier one -- must never place
    the second stop backwards along the road (falls back to canonical
    instead of regressing), while sub-tolerance reversals (GPS noise,
    e.g. the S0028 family at ~3m) must still project.
  - closed-loop routes (ring road): the first and last stops are the
    same physical point; the first stop must anchor at the START of the
    geometry, not the equally-near END, or the progress cursor jumps to
    the far end and every later stop falls back (measured: 94% of a
    real 36-stop loop).
  - hairpin / return-leg, perpendicular cross streets, dense clusters,
    repeated physical locations, and reverse-direction monotonicity.

No database or OSRM instance required -- `route_coords` stands in for a
decoded get_route_geometry(...)["geometry"] LineString (see the module
docstring in test_stop_positioning.py).
"""
import pytest

from app.routing.stop_positioning import (
    FIRST_STOP_TIE_EPS_M,
    MAX_STOP_OFFSET_M,
    MONOTONIC_POSITION_TOLERANCE_M,
    compute_adjusted_stop_positions,
)


def test_same_segment_backward_stop_falls_back_rather_than_regressing():
    """Two stops on ONE straight segment. Stop A at 80% along the
    segment is recorded first; stop B at 20% is recorded second -- i.e.
    B's recorded position is physically BEHIND A. The old matcher
    projected B behind A on the same road (the bug that moved the real
    R-SAJHA-01/02 S0437, R3071402 S0030 and R3297196 S0152 markers
    backwards); the monotonic cursor must refuse to place B behind A
    instead. B keeps its canonical position -- it is data we can't
    trust to place on the road at or after the cursor."""
    route_coords = [(27.7000, 85.3000), (27.7100, 85.3000)]  # one ~1111m segment
    stops = [
        ("A", 27.7080, 85.30010),  # 80% along the segment
        ("B", 27.7020, 85.30010),  # 20% along the segment -- behind A
    ]

    a_pos, b_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert a_pos.source == "projected"
    assert a_pos.progress_m == pytest.approx(0.8 * 1111, abs=30)
    assert b_pos.source == "canonical_fallback"  # not a backward projection
    assert b_pos.progress_m is None
    assert (b_pos.lat, b_pos.lng) == (27.7020, 85.30010)  # canonical, untouched


def test_sub_tolerance_backward_gps_noise_still_projects():
    """A reversal smaller than MONOTONIC_POSITION_TOLERANCE_M (5m) is
    GPS noise (the real S0028 family on 9 routes sits ~3m out of travel
    order) and must keep projecting rather than falling back."""
    route_coords = [(27.7000, 85.3000), (27.7100, 85.3000)]
    stops = [
        ("A", 27.70800, 85.30010),  # 80.0%
        ("B", 27.70797, 85.30010),  # ~3.3m behind A
    ]

    a_pos, b_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert a_pos.source == b_pos.source == "projected"
    assert b_pos.progress_m + MONOTONIC_POSITION_TOLERANCE_M >= a_pos.progress_m


def test_hairpin_u_turn_return_leg_wins_for_later_stop():
    """A U-turn / hairpin: outbound leg east, short connector north,
    return leg west. A stop near the return leg must land on the return
    leg -- even though the (already-passed) outbound leg is closer to
    some such stops -- or its straight-line offset to the outbound road
    would wrongly pull it back to the very start of the journey."""
    route_coords = [
        (27.7000, 85.3000),  # start
        (27.7000, 85.3010),  # outbound east (~980m)
        (27.7010, 85.3010),  # connector north (~111m)
        (27.7010, 85.3000),  # return west
    ]
    stops = [
        ("A", 27.70000, 85.30005),  # outbound leg, near start
        ("B", 27.70000, 85.30095),  # outbound leg, near the connector
        ("C", 27.70095, 85.30010),  # ~5.5m south of the return leg
    ]

    a_pos, b_pos, c_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert a_pos.source == b_pos.source == c_pos.source == "projected"
    # C is ~5.5m from the return leg but ~105m from the outbound leg, so
    # monotonicity plus the offset bound must place it on the return leg.
    assert c_pos.lat == pytest.approx(27.7010, abs=1e-4)
    assert b_pos.progress_m <= c_pos.progress_m + MONOTONIC_POSITION_TOLERANCE_M


def test_cross_street_intersection_continues_forward_not_onto_passed_road():
    """Route travels south along a N-S road, then turns east along an
    east-west cross street at its south end. A stop just past the turn
    sits near the perpendicular at the corner: it must join the east-west
    continuation (AT or after the cursor) and never ride the already-
    traversed N-S leg -- no marker may snap backwards across the
    intersection even if the passed leg's road axis is nearby."""
    route_coords = [
        (27.7004, 85.3000),  # north end, heading south
        (27.6994, 85.3000),  # bottom of the N-S road
        (27.6994, 85.3006),  # turn east
        (27.6994, 85.3012),  # east along the cross street
    ]
    stops = [
        ("A", 27.7000, 85.30010),  # mid-way down the N-S road
        ("B", 27.6993, 85.30070),  # just past the corner, near the E-W leg
    ]

    a_pos, b_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert a_pos.source == b_pos.source == "projected"
    assert b_pos.progress_m >= a_pos.progress_m - MONOTONIC_POSITION_TOLERANCE_M
    # B landed on the east-west continuation (lat 27.6994), not the N-S road axis.
    assert b_pos.lat == pytest.approx(27.6994, abs=1e-4)


def test_closed_loop_first_stop_anchors_at_loop_start_and_closing_stop_at_end():
    """Ring-road-shaped route: first and last stops are the same
    physical point and the geometry's end nearly touches its start.
    Without the FIRST_STOP_TIE_EPS_M anchor the first stop (equidistant
    from loop start and loop end) would snap to the END, the cursor
    would jump ~100x its travel position, and every later stop would
    fall back. Regression for real R3351751 (was 34/36 canonical)."""
    route_coords = [
        (0.0000, 0.0000),
        (0.0010, 0.0000),  # north leg
        (0.0010, 0.0010),  # east leg
        (0.0000, 0.0010),  # south leg
        (0.00001, 0.00001),  # closing segment back near the start
    ]
    stops = [
        ("GATE_A", 0.00001, 0.00001),  # first visit to the loop point
        ("MID_E", 0.0010, 0.00005),  # on the north leg
        ("MID_N", 0.00005, 0.0010),  # on the east leg
        ("GATE_B", 0.00001, 0.00001),  # second visit -- closes the loop
    ]

    gate_a, mid_e, mid_n, gate_b = compute_adjusted_stop_positions(route_coords, stops)

    assert [p.source for p in (gate_a, mid_e, mid_n, gate_b)] == ["projected"] * 4
    # A anchors near the START of the loop (~0m), not the far end (~420m).
    assert gate_a.progress_m < FIRST_STOP_TIE_EPS_M * 2
    # progress must climb monotonically around the loop...
    assert mid_e.progress_m > gate_a.progress_m
    assert mid_n.progress_m > mid_e.progress_m
    # ...and the closing (second) visit to the loop point lands at the END.
    assert gate_b.progress_m > mid_n.progress_m
    assert gate_b.progress_m > 300.0


def test_duplicate_physical_location_both_project_monotonically():
    """Two DIFFERENT stop_ids whose canonical coordinates coincide.
    Both must project (each is within range of the road) and the second
    must not be placed behind the first."""
    route_coords = [(27.7000, 85.3000), (27.7020, 85.3000)]
    stops = [
        ("A", 27.7010, 85.30010),
        ("A_DUP", 27.7010, 85.30010),
    ]

    a_pos, adup_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert a_pos.source == adup_pos.source == "projected"
    assert adup_pos.progress_m >= a_pos.progress_m - MONOTONIC_POSITION_TOLERANCE_M
    assert adup_pos.progress_m == pytest.approx(a_pos.progress_m, abs=MONOTONIC_POSITION_TOLERANCE_M * 2)


def test_dense_stops_at_a_corner_stay_monotonic():
    """Three stops clustered within ~60m of an L-corner (the real
    R3071402 S0030-cluster shape). All must project and stay monotonic
    through the turn: approach leg, then departure leg."""
    route_coords = [
        (27.7000, 85.3000),  # start, heading east
        (27.7000, 85.3006),  # corner
        (27.7004, 85.3006),  # departure heading north
    ]
    stops = [
        ("A", 27.70005, 85.30010),  # on the approach leg
        ("B", 27.70003, 85.30055),  # just before the corner
        ("C", 27.70030, 85.30060),  # on the departure leg
    ]

    a_pos, b_pos, c_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert [p.source for p in (a_pos, b_pos, c_pos)] == ["projected"] * 3
    assert b_pos.progress_m >= a_pos.progress_m - MONOTONIC_POSITION_TOLERANCE_M
    assert c_pos.progress_m >= b_pos.progress_m - MONOTONIC_POSITION_TOLERANCE_M
    # A and B ride the approach leg (lat ~27.7000); C rides the departure
    # leg (lng ~85.3006).
    assert a_pos.lat == pytest.approx(27.7000, abs=1e-4)
    assert c_pos.lng == pytest.approx(85.3006, abs=1e-4)


def test_reverse_direction_uses_reverse_carriageway_monotonically():
    """The same physical stops, projected against the reverse-direction
    geometry (different carriageway, opposite traversal), must land on
    the reverse carriageway and still be monotonically ordered in
    reverse travel order -- the fwd/rev distinction the real divided
    corridors expose (e.g. R3071402-type routes)."""
    fwd_geometry = [(27.7000, 85.30000), (27.7020, 85.30000), (27.7040, 85.30000)]
    rev_geometry = [(27.7040, 85.30020), (27.7020, 85.30020), (27.7000, 85.30020)]

    # Forward: A then B, travelling north along lng 85.3000.
    [a_fwd, b_fwd] = compute_adjusted_stop_positions(
        fwd_geometry, [("A", 27.7010, 85.30010), ("B", 27.7030, 85.30010)]
    )
    # Reverse: B then A, travelling south along lng 85.30020.
    [b_rev, a_rev] = compute_adjusted_stop_positions(
        rev_geometry, [("B", 27.7030, 85.30010), ("A", 27.7010, 85.30010)]
    )

    for p in (a_fwd, b_fwd, a_rev, b_rev):
        assert p.source == "projected"
    # carriageway is direction-specific...
    assert b_fwd.lng == pytest.approx(85.30000, abs=5e-5)
    assert b_rev.lng == pytest.approx(85.30020, abs=5e-5)
    assert b_fwd.lng != pytest.approx(b_rev.lng, abs=1e-6)
    # ...and reverse travel is monotonic in ITS order: B (first) before A.
    assert a_rev.progress_m >= b_rev.progress_m - MONOTONIC_POSITION_TOLERANCE_M


def test_long_detour_is_still_found_by_global_search():
    """Companion guard for the module's global-search contract: even a
    long detour between stops (straight-line ~111m, road ~9x that) must
    not strand the later stop. Mirrors the real route-finder gap that a
    windowed search would have missed."""
    route_coords = [
        (0.00000, 0.00000),
        (0.00000, 0.00100),
        (0.00050, 0.00100),
        (0.00050, 0.00000),  # long way round (return leg)
        (0.00100, 0.00000),
    ]
    stops = [
        ("A", 0.00000, 0.00000),
        ("B", 0.00000, 0.00100),
        ("C", 0.000500, 0.00060),  # near the return leg, ~500m of road later
    ]
    a_pos, b_pos, c_pos = compute_adjusted_stop_positions(route_coords, stops)

    assert [p.source for p in (a_pos, b_pos, c_pos)] == ["projected"] * 3
    assert c_pos.lat == pytest.approx(0.00050, abs=1e-4)