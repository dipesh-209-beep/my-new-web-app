"""
Unit tests for app/api/routing.py::_annotate_ride_legs — pure logic, no
DB required. Prove the operator/direction resolution rules the route
timeline relies on: which terminus a leg travels toward, when that can't
be determined, and that walking-transfer legs stay untouched.
"""

from app.api.routing import _annotate_ride_legs
from app.db.queries import RouteDisplayInfo
from app.schemas import RouteLeg, StopOut


def make_stop(stop_id: str, name: str) -> StopOut:
    return StopOut(
        stop_id=stop_id,
        stop_name=name,
        lat=27.7,
        lng=85.31,
        is_major_stop=False,
        is_interchange=False,
        status="active",
    )


def make_leg(route_id: str, board: StopOut, alight: StopOut) -> RouteLeg:
    return RouteLeg(
        route_id=route_id,
        route_name="Some Route",
        board_stop=board,
        alight_stop=alight,
        num_ride_segments=1,
        stops=[board, alight],
    )


def test_forward_leg_direction_towards_end_terminus():
    # Sequence: A(1) B(2) C(3) D(4); riding A -> C travels forward.
    info = RouteDisplayInfo(
        operator_name="Yeti Yatayat",
        start_stop_id="S1",
        start_stop_name="A",
        end_stop_id="S4",
        end_stop_name="D",
        stop_positions={"S1": 1, "S2": 2, "S3": 3, "S4": 4},
    )
    leg = make_leg("R1", make_stop("S1", "A"), make_stop("S3", "C"))
    _annotate_ride_legs([leg], {"R1": info})
    assert leg.operator_name == "Yeti Yatayat"
    assert leg.direction_towards == "D"


def test_reverse_leg_direction_towards_start_terminus():
    # Riding D -> B on the same route travels the reverse direction.
    info = RouteDisplayInfo(
        operator_name="Yeti Yatayat",
        start_stop_id="S1",
        start_stop_name="A",
        end_stop_id="S4",
        end_stop_name="D",
        stop_positions={"S1": 1, "S2": 2, "S3": 3, "S4": 4},
    )
    leg = make_leg("R1", make_stop("S4", "D"), make_stop("S2", "B"))
    _annotate_ride_legs([leg], {"R1": info})
    assert leg.operator_name == "Yeti Yatayat"
    assert leg.direction_towards == "A"


def test_transfer_leg_stays_unannotated():
    info = RouteDisplayInfo(
        operator_name="Yeti Yatayat",
        start_stop_id="S1",
        start_stop_name="A",
        end_stop_id="S4",
        end_stop_name="D",
        stop_positions={"S1": 1, "S4": 4},
    )
    leg = make_leg("TRANSFER", make_stop("S1", "A"), make_stop("S2", "B"))
    _annotate_ride_legs([leg], {"R1": info})
    assert leg.operator_name is None
    assert leg.direction_towards is None


def test_direction_omitted_when_a_stop_is_missing_from_sequence():
    # Board/alight not both present in the route's stop sequence -> the
    # direction genuinely can't be determined; operator still surfaces.
    info = RouteDisplayInfo(
        operator_name="Yeti Yatayat",
        end_stop_name="D",
        stop_positions={"S1": 1},
    )
    leg = make_leg("R1", make_stop("S1", "A"), make_stop("S9", "Unlisted"))
    _annotate_ride_legs([leg], {"R1": info})
    assert leg.operator_name == "Yeti Yatayat"
    assert leg.direction_towards is None


def test_route_absent_from_info_leaves_leg_untouched():
    leg = make_leg("R_UNKNOWN", make_stop("S1", "A"), make_stop("S2", "B"))
    _annotate_ride_legs([leg], {})
    assert leg.operator_name is None
    assert leg.direction_towards is None


def test_no_operator_keeps_operator_name_none():
    info = RouteDisplayInfo(stop_positions={"S1": 1, "S2": 2})
    leg = make_leg("R1", make_stop("S1", "A"), make_stop("S2", "B"))
    _annotate_ride_legs([leg], {"R1": info})
    assert leg.operator_name is None
    assert leg.direction_towards is None