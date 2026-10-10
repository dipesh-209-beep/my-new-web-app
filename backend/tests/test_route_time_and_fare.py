"""
Unit tests for app/api/routing.py's time/fare helpers — pure logic, no DB.
Prove the route-finder presents time as a fixed-speed estimate (never the
OSRM driving-profile duration) and computes fare on the distance actually
ridden, excluding walking transfers.
"""

import math

import pytest

from app.api.routing import (
    _annotate_leg_estimated_durations,
    _leg_estimated_duration_s,
    _ride_only_distance_m,
    _total_estimated_duration_s,
)
from app.routing.constants import BOARD_WAIT_S, BUS_AVG_SPEED_MPS, WALK_SPEED_MPS
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


def test_ride_leg_uses_bus_speed_over_road_distance():
    leg = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    assert _leg_estimated_duration_s(leg) == pytest.approx(3300 / BUS_AVG_SPEED_MPS)
    # 3300 m at ~3.3 m/s is ~1000 s, NOT the road_geometry.duration_s of 1.
    assert _leg_estimated_duration_s(leg) > 100


def test_transfer_leg_uses_walking_speed():
    leg = make_leg("TRANSFER", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=1300)
    assert _leg_estimated_duration_s(leg) == pytest.approx(1300 / WALK_SPEED_MPS)


def test_haversine_fallback_when_no_road_geometry():
    # ~1.1 km apart at this latitude; no OSRM geometry -> straight line.
    leg = make_leg(
        "R1",
        make_stop("S1", "A", lat=27.7000, lng=85.3100),
        make_stop("S2", "B", lat=27.7100, lng=85.3100),
    )
    duration = _leg_estimated_duration_s(leg)
    assert duration == pytest.approx(
        (0.01 * math.pi / 180) * 6371000 / BUS_AVG_SPEED_MPS, rel=1e-2
    )


def test_total_adds_a_boarding_wait_per_ride():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    walk = make_leg("TRANSFER", make_stop("S2", "B"), make_stop("S3", "C"), distance_m=1300)
    expected = (3300 / BUS_AVG_SPEED_MPS) + (1300 / WALK_SPEED_MPS) + BOARD_WAIT_S
    assert _total_estimated_duration_s([ride, walk]) == pytest.approx(expected)


def test_total_counts_two_rides_two_waits():
    ride1 = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    ride2 = make_leg("R2", make_stop("S3", "C"), make_stop("S4", "D"), distance_m=3300)
    assert _total_estimated_duration_s([ride1, ride2]) == pytest.approx(
        (3300 / BUS_AVG_SPEED_MPS) * 2 + BOARD_WAIT_S * 2
    )


def test_ride_only_distance_excludes_walking_transfers():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    walk = make_leg("TRANSFER", make_stop("S2", "B"), make_stop("S3", "C"), distance_m=1300)
    assert _ride_only_distance_m([ride, walk]) == pytest.approx(3300)


def test_annotate_sets_every_leg_estimate():
    ride = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"), distance_m=3300)
    walk = make_leg("TRANSFER", make_stop("S2", "B"), make_stop("S3", "C"), distance_m=1300)
    _annotate_leg_estimated_durations([ride, walk])
    assert ride.estimated_duration_s == pytest.approx(3300 / BUS_AVG_SPEED_MPS)
    assert walk.estimated_duration_s == pytest.approx(1300 / WALK_SPEED_MPS)
