"""
Regression tests for the bearing/radius waypoint constraints added to
app/api/routing.py (_bearing_deg, _bearings_for) and the corresponding
params-building in app/routing/osrm_client.py::get_route_geometry.

No live database or OSRM instance required -- these test pure
computation and request-building, not actual routing results. See
backend/scripts/check_bearing_snapping.py for a live-OSRM comparison
against real route data.
"""
import pytest

from app.api.routing import _bearing_deg, _bearings_for, BEARING_RANGE_DEG
from app.routing.osrm_client import get_route_geometry


def test_bearing_due_north():
    # Same longitude, higher latitude -> heading 0 (true north).
    b = _bearing_deg(27.70, 85.30, 27.71, 85.30)
    assert b == pytest.approx(0.0, abs=0.5)


def test_bearing_due_east():
    # Same latitude, higher longitude -> heading 90 (true east).
    b = _bearing_deg(27.70, 85.30, 27.70, 85.31)
    assert b == pytest.approx(90.0, abs=0.5)


def test_bearing_reverse_direction_is_opposite():
    a = (27.70, 85.30)
    b = (27.72, 85.34)
    forward = _bearing_deg(*a, *b)
    reverse = _bearing_deg(*b, *a)
    # Reverse of a heading is 180 degrees off, modulo wraparound.
    assert abs((forward - reverse) % 360 - 180) < 0.5


def test_bearings_for_single_coordinate_returns_none():
    assert _bearings_for([(27.70, 85.30)]) is None


def test_bearings_for_uses_incoming_heading_for_last_point():
    coords = [(27.70, 85.30), (27.71, 85.30), (27.72, 85.30)]
    bearings = _bearings_for(coords)
    assert len(bearings) == 3
    # All northward in a straight line -> every heading should be ~0,
    # including the last point, which has no "next" point to aim at.
    for b, r in bearings:
        assert b == pytest.approx(0.0, abs=0.5)
        assert r == BEARING_RANGE_DEG


def test_bearings_for_matches_actual_travel_direction_either_way():
    """The whole point of deriving bearings from `coords` order rather
    than a stored/global direction flag: reversing the waypoint list
    (as happens for a bidirectional route's return leg) should reverse
    every computed heading too, with no extra bookkeeping."""
    forward_coords = [(27.70, 85.30), (27.71, 85.31), (27.72, 85.32)]
    reverse_coords = list(reversed(forward_coords))

    forward_bearings = [b for b, _ in _bearings_for(forward_coords)]
    reverse_bearings = [b for b, _ in _bearings_for(reverse_coords)]

    # Point-for-point, travelling the same physical road in the other
    # direction should give a heading ~180 degrees apart.
    for fb, rb in zip(forward_bearings, reversed(reverse_bearings)):
        assert abs((fb - rb) % 360 - 180) < 1.0


def test_get_route_geometry_rejects_mismatched_bearings_length(monkeypatch):
    with pytest.raises(ValueError):
        get_route_geometry(
            [(27.70, 85.30), (27.71, 85.31)],
            bearings=[(0.0, 30)],  # only one entry for two coordinates
        )


def test_get_route_geometry_rejects_mismatched_radiuses_length(monkeypatch):
    with pytest.raises(ValueError):
        get_route_geometry(
            [(27.70, 85.30), (27.71, 85.31)],
            radiuses=[50],  # only one entry for two coordinates
        )


def test_get_route_geometry_builds_bearings_and_radiuses_params(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "code": "Ok",
                "routes": [
                    {
                        "geometry": {"type": "LineString", "coordinates": []},
                        "distance": 100.0,
                        "duration": 20.0,
                    }
                ],
            }

    def _fake_get(self, url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr("httpx.Client.get", _fake_get)

    coords = [(27.70, 85.30), (27.71, 85.31), (27.72, 85.32)]
    bearings = _bearings_for(coords)
    radiuses = [50, 50, 50]

    get_route_geometry(coords, bearings=bearings, radiuses=radiuses)

    assert captured["params"]["bearings"].count(";") == 2  # 3 waypoints
    assert captured["params"]["radiuses"] == "50;50;50"


def test_get_route_geometry_omits_bearings_and_radiuses_when_not_given(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "code": "Ok",
                "routes": [
                    {
                        "geometry": {"type": "LineString", "coordinates": []},
                        "distance": 100.0,
                        "duration": 20.0,
                    }
                ],
            }

    def _fake_get(self, url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr("httpx.Client.get", _fake_get)

    # Reproduces the exact previous call shape -- no bearings/radiuses.
    get_route_geometry([(27.70, 85.30), (27.71, 85.31)])

    assert "bearings" not in captured["params"]
    assert "radiuses" not in captured["params"]
