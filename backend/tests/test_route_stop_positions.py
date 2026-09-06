"""
Regression tests for the direction-aware display-position wiring added
to app/api/routes.py (_display_positions_for_direction,
_stops_in_travel_order). No live database required -- same
monkeypatch-the-collaborator approach as test_attach_road_geometry.py
and test_pathfinder_alternatives.py: fake RouteStop/Stop objects via
SimpleNamespace, and get_route_geometry monkeypatched at the call site
in app.api.routes.

API-level (HTTP status code / caching) behavior for the new `direction`
query param on GET /routes/{route_id}/stops and .../geometry is covered
separately in test_route_geometry_api.py-style tests, which need a live
DB (`docker compose up -d db`) -- see that file's own docstring for why.
"""
import os

# Same reasoning as test_attach_road_geometry.py: app.api.routes ->
# app.db.session validates required Settings fields at import time.
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from types import SimpleNamespace

import pytest

import app.api.routes as routes_api
from app.routing.osrm_client import OSRMError


def make_stop(stop_id: str, lat: float, lng: float):
    return SimpleNamespace(
        stop_id=stop_id,
        stop_name=stop_id,
        lat=lat,
        lng=lng,
        zone=None,
        district=None,
        is_major_stop=False,
        is_interchange=False,
        status="active",
    )


def make_route_stop(sequence_no: int, stop) -> SimpleNamespace:
    return SimpleNamespace(sequence_no=sequence_no, stop_id=stop.stop_id, stop=stop)


def fake_osrm(coords_to_geometry: dict):
    """coords_to_geometry maps a tuple of the (thinned) waypoint coords
    OSRM was called with -> the GeoJSON geometry dict to hand back, so a
    test can give the forward call and the reverse call different
    (direction-appropriate) geometries, the same way real OSRM would."""

    def _get(coords, profile="driving", bearings=None, radiuses=None):
        key = tuple(coords)
        if key not in coords_to_geometry:
            raise AssertionError(f"Unexpected OSRM call with coords={coords}")
        return {
            "geometry": coords_to_geometry[key],
            "distance_m": 100.0,
            "duration_s": 30.0,
        }

    return _get


def test_stops_in_travel_order_forward_is_identity():
    s1, s2 = make_stop("S1", 27.70, 85.30), make_stop("S2", 27.71, 85.31)
    route_stops = [make_route_stop(1, s1), make_route_stop(2, s2)]

    assert routes_api._stops_in_travel_order(route_stops, "forward") == route_stops


def test_stops_in_travel_order_reverse_flips_the_list():
    s1, s2 = make_stop("S1", 27.70, 85.30), make_stop("S2", 27.71, 85.31)
    route_stops = [make_route_stop(1, s1), make_route_stop(2, s2)]

    assert routes_api._stops_in_travel_order(route_stops, "reverse") == list(reversed(route_stops))


def test_display_positions_none_for_fewer_than_two_stops():
    s1 = make_stop("S1", 27.70, 85.30)
    assert routes_api._display_positions_for_direction([make_route_stop(1, s1)], "forward") is None


def test_display_positions_none_when_osrm_fails(monkeypatch):
    s1, s2 = make_stop("S1", 27.7000, 85.3000), make_stop("S2", 27.7010, 85.3000)
    route_stops = [make_route_stop(1, s1), make_route_stop(2, s2)]

    def always_fails(coords, profile="driving", bearings=None, radiuses=None):
        raise OSRMError("connection refused")

    monkeypatch.setattr(routes_api, "get_route_geometry", always_fails)

    assert routes_api._display_positions_for_direction(route_stops, "forward") is None


def test_display_positions_falls_back_to_unconstrained_when_constrained_fails(monkeypatch):
    """Same resilience pattern as _attach_road_geometry: the bearing+radius
    constraint is deliberately tight and is expected to occasionally have
    no matching edge within range. A constrained failure should retry
    unconstrained rather than immediately giving up on the whole
    direction -- this is the exact failure mode observed against the real
    Kathmandu dataset (WAYPOINT_SNAP_RADIUS_M=50m finding no match)."""
    s1 = make_stop("S1", 27.7000, 85.3000)
    s2 = make_stop("S2", 27.7010, 85.30010)
    route_stops = [make_route_stop(1, s1), make_route_stop(2, s2)]

    calls = []

    def constrained_fails_unconstrained_succeeds(coords, profile="driving", bearings=None, radiuses=None):
        calls.append({"bearings": bearings, "radiuses": radiuses})
        if bearings is not None or radiuses is not None:
            raise OSRMError("NoSegment")
        return {
            "geometry": {
                "type": "LineString",
                "coordinates": [[85.3000, 27.7000], [85.3000, 27.7010]],
            },
            "distance_m": 100.0,
            "duration_s": 30.0,
        }

    monkeypatch.setattr(routes_api, "get_route_geometry", constrained_fails_unconstrained_succeeds)

    positions = routes_api._display_positions_for_direction(route_stops, "forward")

    assert positions is not None
    assert len(positions) == 2
    assert len(calls) == 2  # constrained attempt, then unconstrained retry
    assert calls[0]["bearings"] is not None
    assert calls[1]["bearings"] is None and calls[1]["radiuses"] is None


def test_display_positions_forward_aligns_with_ascending_sequence_no(monkeypatch):
    s1 = make_stop("S1", 27.7000, 85.3000)
    s2 = make_stop("S2", 27.7010, 85.30010)  # slightly east of the road
    route_stops = [make_route_stop(1, s1), make_route_stop(2, s2)]

    forward_coords = ((27.7000, 85.3000), (27.7010, 85.30010))
    forward_geometry = {
        "type": "LineString",
        "coordinates": [[85.3000, 27.7000], [85.3000, 27.7010]],  # GeoJSON [lng, lat]
    }
    monkeypatch.setattr(
        routes_api, "get_route_geometry", fake_osrm({forward_coords: forward_geometry})
    )

    positions = routes_api._display_positions_for_direction(route_stops, "forward")

    assert positions is not None
    assert len(positions) == 2
    # Aligned with route_stops order: index 0 is S1, index 1 is S2.
    assert positions[0].stop_id == "S1"
    assert positions[1].stop_id == "S2"
    # S2 got pulled back onto the road (lng snapped toward 85.3000).
    assert positions[1].lng == pytest.approx(85.3000, abs=1e-4)

def test_display_positions_reverse_uses_reverse_geometry_and_realigns(monkeypatch):
    """The key wiring behavior: 'reverse' must (a) query OSRM with the
    stop order actually reversed, not just reverse the forward result,
    and (b) hand back positions aligned with route_stops' own ascending
    order, not the reversed travel order used internally."""
    s1 = make_stop("S1", 27.7000, 85.30000)
    s2 = make_stop("S2", 27.7010, 85.30000)
    route_stops = [make_route_stop(1, s1), make_route_stop(2, s2)]

    # Reverse travel order is [s2, s1] -- OSRM is called with that order,
    # and on a divided road the reverse carriageway is a different line
    # (offset east here) from the forward one.
    reverse_coords = ((27.7010, 85.30000), (27.7000, 85.30000))
    reverse_geometry = {
        "type": "LineString",
        "coordinates": [[85.30020, 27.7010], [85.30020, 27.7000]],
    }
    monkeypatch.setattr(
        routes_api, "get_route_geometry", fake_osrm({reverse_coords: reverse_geometry})
    )

    positions = routes_api._display_positions_for_direction(route_stops, "reverse")

    assert positions is not None
    # Realigned to route_stops' ascending order: index 0 is still S1, index 1 is still S2.
    assert positions[0].stop_id == "S1"
    assert positions[1].stop_id == "S2"
    # Both landed on the reverse carriageway (lng ~ 85.30020), not the
    # forward one (lng ~ 85.30000).
    assert positions[0].lng == pytest.approx(85.30020, abs=1e-4)
    assert positions[1].lng == pytest.approx(85.30020, abs=1e-4)


def test_display_positions_handles_loop_route_repeated_stop_id(monkeypatch):
    """A loop route can visit the same physical stop_id at two different
    sequence_nos (see app/routing/graph_builder.py's NY-03 example).
    Positions must align by list position, not collide on stop_id."""
    hub = make_stop("HUB", 27.7000, 85.30000)
    mid = make_stop("MID", 27.7010, 85.30000)
    # HUB appears twice: start/end of the loop.
    route_stops = [
        make_route_stop(1, hub),
        make_route_stop(2, mid),
        make_route_stop(3, hub),
    ]

    coords = ((27.7000, 85.30000), (27.7010, 85.30000), (27.7000, 85.30000))
    geometry = {
        "type": "LineString",
        "coordinates": [[85.3000, 27.7000], [85.3000, 27.7010], [85.3000, 27.7000]],
    }
    monkeypatch.setattr(routes_api, "get_route_geometry", fake_osrm({coords: geometry}))

    positions = routes_api._display_positions_for_direction(route_stops, "forward")

    assert positions is not None
    assert len(positions) == 3
    # Both HUB occurrences got their own independent result, not a single
    # shared one -- no crash/collision from the repeated stop_id.
    assert positions[0].stop_id == "HUB"
    assert positions[2].stop_id == "HUB"
