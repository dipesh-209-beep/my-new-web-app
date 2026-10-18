"""
Unit tests for app/api/routing.py's time/fare/distance helpers — pure
logic, no DB. Prove the route-finder presents time as a fixed-speed estimate
(never the OSRM driving-profile duration), computes fare on the distance
actually ridden (excluding walking transfers), and puts every distance-derived
figure on one path-wide basis rather than mixing OSRM and haversine per leg
(see _path_distance_source and DistanceSource).
"""

import math

import pytest

from app.api.routing import (
    _annotate_leg_estimated_durations,
    _leg_distance_m,
    _leg_estimated_duration_s,
    _path_distance_source,
    _ride_only_distance_m,
    _total_estimated_duration_s,
)
from app.routing.constants import (
    BOARD_WAIT_S,
    BUS_AVG_SPEED_MPS,
    CONGESTION_LAMBDA,
    WALK_SPEED_MPS,
)
from app.routing.graph_builder import haversine_distance_m
from app.schemas import RouteLeg, StopOut


def make_stop(stop_id: str, name: str, lat: float = 27.7, lng: float = 85.31) -> StopOut:
    return StopOut(
        stop_id=stop_id,
        stop_name=name,
        lat=lat,
        lng=lng,
        is_major_stop=False,
        is_interchange=False,
        status="active",
    )


def make_leg(
    route_id: str,
    board: StopOut,
    alight: StopOut,
    distance_m: float | None = None,
) -> RouteLeg:
    road_geometry = None
    if distance_m is not None:
        road_geometry = {
            "geometry": {
                "type": "LineString",
                "coordinates": [[board.lng, board.lat], [alight.lng, alight.lat]],
            },
            "distance_m": distance_m,
            "duration_s": 1,  # deliberately tiny: must NEVER be used for time
        }
    return RouteLeg(
        route_id=route_id,
        route_name="Some Route",
        board_stop=board,
        alight_stop=alight,
        num_ride_segments=1,
        stops=[board, alight],
        road_geometry=road_geometry,
    )


def straight_m(board: StopOut, alight: StopOut) -> float:
    return haversine_distance_m(board.lat, board.lng, alight.lat, alight.lng)


def test_ride_leg_uses_bus_speed_over_road_distance():
    leg = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    assert _leg_estimated_duration_s(leg, "osrm") == pytest.approx(
        3300 / BUS_AVG_SPEED_MPS
    )
    # 3300 m at ~3.3 m/s is ~1000 s, NOT the road_geometry.duration_s of 1.
    assert _leg_estimated_duration_s(leg, "osrm") > 100


def test_transfer_leg_uses_walking_speed():
    leg = make_leg(
        "TRANSFER", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=1300
    )
    assert _leg_estimated_duration_s(leg, "osrm") == pytest.approx(
        1300 / WALK_SPEED_MPS
    )


def test_haversine_fallback_when_no_road_geometry():
    # ~1.1 km apart at this latitude; no OSRM geometry -> straight line.
    leg = make_leg(
        "R1",
        make_stop("S1", "A", lat=27.7000, lng=85.3100),
        make_stop("S2", "B", lat=27.7100, lng=85.3100),
    )
    duration = _leg_estimated_duration_s(leg, "graph")
    assert duration == pytest.approx(
        (0.01 * math.pi / 180) * 6371000 / BUS_AVG_SPEED_MPS, rel=1e-2
    )


def test_total_adds_a_boarding_wait_per_ride():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    walk = make_leg(
        "TRANSFER", make_stop("S2", "B"), make_stop("S3", "C"), distance_m=1300
    )
    expected = (3300 / BUS_AVG_SPEED_MPS) + (1300 / WALK_SPEED_MPS) + BOARD_WAIT_S
    assert _total_estimated_duration_s([ride, walk], "osrm") == pytest.approx(expected)


def test_total_counts_two_rides_two_waits():
    ride1 = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    ride2 = make_leg("R2", make_stop("S3", "C"), make_stop("S4", "D"), distance_m=3300)
    assert _total_estimated_duration_s([ride1, ride2], "osrm") == pytest.approx(
        (3300 / BUS_AVG_SPEED_MPS) * 2 + BOARD_WAIT_S * 2
    )


def test_ride_only_distance_excludes_walking_transfers():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    walk = make_leg(
        "TRANSFER", make_stop("S2", "B"), make_stop("S3", "C"), distance_m=1300
    )
    assert _ride_only_distance_m([ride, walk], "osrm") == pytest.approx(3300)


def test_annotate_sets_every_leg_estimate():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    walk = make_leg(
        "TRANSFER", make_stop("S2", "B"), make_stop("S3", "C"), distance_m=1300
    )
    _annotate_leg_estimated_durations([ride, walk], "osrm")
    assert ride.estimated_duration_s == pytest.approx(3300 / BUS_AVG_SPEED_MPS)
    assert walk.estimated_duration_s == pytest.approx(1300 / WALK_SPEED_MPS)


# --- the path-wide, all-or-nothing source ---------------------------------


def test_path_source_is_osrm_only_when_every_leg_has_geometry():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    bare = make_leg("R2", make_stop("S2", "B"), make_stop("S3", "C"))
    assert _path_distance_source([ride]) == "osrm"
    assert _path_distance_source([ride, bare]) == "graph"
    assert _path_distance_source([bare]) == "graph"


def test_graph_basis_ignores_available_road_geometry():
    """On a "graph" path, a leg that happens to carry OSRM geometry is still
    measured as its haversine straight-line — so a single path can't mix the
    two bases. This is also why an OSRM duration can never leak into a
    "graph"-basis estimate."""
    board = make_stop("S1", "A", lat=27.7000, lng=85.3100)
    alight = make_stop("S2", "B", lat=27.7100, lng=85.3100)
    leg = make_leg("R1", board, alight, distance_m=3300)

    assert _leg_distance_m(leg, "graph") == pytest.approx(straight_m(board, alight))
    assert _leg_estimated_duration_s(leg, "graph") == pytest.approx(
        straight_m(board, alight) / BUS_AVG_SPEED_MPS
    )
    # The road distance is still what an "osrm" path would use.
    assert _leg_distance_m(leg, "osrm") == pytest.approx(3300)


def test_ride_only_distance_uses_path_basis_and_excludes_walk():
    b1 = make_stop("S1", "A", lat=27.7000, lng=85.3100)
    a1 = make_stop("S2", "B", lat=27.7100, lng=85.3100)
    ride = make_leg("R1", b1, a1, distance_m=3300)
    walk = make_leg(
        "TRANSFER", a1, make_stop("S3", "C", lat=27.7200, lng=85.3100), distance_m=1300
    )
    assert _ride_only_distance_m([ride, walk], "osrm") == pytest.approx(3300)
    assert _ride_only_distance_m([ride, walk], "graph") == pytest.approx(
        straight_m(b1, a1)
    )


def test_walking_only_trip_has_no_ride_distance_on_either_basis():
    walk = make_leg(
        "TRANSFER",
        make_stop("S1", "A", lat=27.7000, lng=85.3100),
        make_stop("S2", "B", lat=27.7100, lng=85.3100),
        distance_m=1300,
    )
    assert _ride_only_distance_m([walk], "osrm") == 0
    assert _ride_only_distance_m([walk], "graph") == 0


def test_ride_leg_duration_is_congestion_scaled():
    """When the search opted into congestion, a ride leg's displayed duration
    carries the same CONGESTION_LAMBDA inflation the ranking used -- so a leg
    ranked as slow can never display as fast (AUD-021)."""
    leg = make_leg(
        "R1",
        make_stop("S1", "A", lat=27.7000, lng=85.3100),
        make_stop("S2", "B", lat=27.7100, lng=85.3100),
        distance_m=1300,
    )
    base = _leg_estimated_duration_s(leg, "osrm")
    leg.congestion_ratio = 2.0
    scaled = _leg_estimated_duration_s(leg, "osrm")
    assert scaled == pytest.approx(base * (1 + CONGESTION_LAMBDA * (2.0 - 1)))


def test_transfer_leg_duration_ignores_congestion():
    """Congestion only ever inflates rides; a walking-transfer leg is never
    scaled, even if a ratio somehow lands on it."""
    leg = make_leg(
        "TRANSFER",
        make_stop("S1", "A", lat=27.7000, lng=85.3100),
        make_stop("S2", "B", lat=27.7100, lng=85.3100),
        distance_m=1300,
    )
    base = _leg_estimated_duration_s(leg, "osrm")
    leg.congestion_ratio = 5.0
    assert _leg_estimated_duration_s(leg, "osrm") == pytest.approx(base)


def test_congestion_ratio_is_excluded_from_response_payload():
    """congestion_ratio is an internal display-scaling input, not part of the
    public RouteLeg shape."""
    leg = make_leg(
        "R1",
        make_stop("S1", "A", lat=27.7000, lng=85.3100),
        make_stop("S2", "B", lat=27.7100, lng=85.3100),
        distance_m=1300,
    )
    leg.congestion_ratio = 3.0
    assert "congestion_ratio" not in leg.model_dump()
