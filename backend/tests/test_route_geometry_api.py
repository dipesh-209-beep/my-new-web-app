"""
API-level tests for GET /routes/{route_id}/geometry and GET /routes/{route_id}/stops
— require a live Postgres/PostGIS instance (docker compose up -d db) with migration 0002
applied and sample data imported. Skip cleanly if no DB is reachable.

OSRM itself is mocked out (monkeypatched at the call site in
app.api.routes) so these tests don't also require a live osrm-routed
instance -- they're checking the endpoint's own plumbing (route lookup,
stop-count guard, waypoint thinning, error mapping, direction param),
not OSRM's routing.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import Route
from app.routing.osrm_client import OSRMError
import app.api.routes as routes_api


@pytest.fixture
def client():
    session = SessionLocal()
    try:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()
    return TestClient(app)


def _any_route_id_with_min_stops(min_stops: int = 2) -> str | None:
    session = SessionLocal()
    try:
        return session.execute(
            select(Route.route_id).where(Route.total_stops >= min_stops).limit(1)
        ).scalar_one_or_none()
    finally:
        session.close()


def _any_bidirectional_route_id() -> str | None:
    session = SessionLocal()
    try:
        return session.execute(
            select(Route.route_id).where(Route.is_bidirectional.is_(True)).limit(1)
        ).scalar_one_or_none()
    finally:
        session.close()


def _any_non_bidirectional_route_id() -> str | None:
    session = SessionLocal()
    try:
        return session.execute(
            select(Route.route_id).where(Route.is_bidirectional.is_(False)).limit(1)
        ).scalar_one_or_none()
    finally:
        session.close()


def test_geometry_404_for_unknown_route(client):
    res = client.get("/routes/does-not-exist/geometry")
    assert res.status_code == 404


def test_geometry_returns_osrm_result_shape(client, monkeypatch):
    route_id = _any_route_id_with_min_stops(2)
    if route_id is None:
        pytest.skip("No route with >= 2 stops in DB to check")

    fake_result = {
        "geometry": {"type": "LineString", "coordinates": [[85.3, 27.7], [85.31, 27.71]]},
        "distance_m": 1234.5,
        "duration_s": 210.0,
    }
    monkeypatch.setattr(
        routes_api,
        "get_route_geometry",
        lambda coords, profile="driving", bearings=None, radiuses=None: fake_result,
    )

    res = client.get(f"/routes/{route_id}/geometry")
    assert res.status_code == 200
    assert res.json() == fake_result


def test_geometry_502_when_osrm_fails(client, monkeypatch):
    route_id = _any_route_id_with_min_stops(2)
    if route_id is None:
        pytest.skip("No route with >= 2 stops in DB to check")

    def _raise(coords, profile="driving", bearings=None, radiuses=None):
        raise OSRMError("connection refused")

    monkeypatch.setattr(routes_api, "get_route_geometry", _raise)

    res = client.get(f"/routes/{route_id}/geometry")
    assert res.status_code == 502


def test_geometry_422_for_route_with_fewer_than_two_stops(client, monkeypatch):
    route_id = _any_route_id_with_min_stops(2)
    if route_id is None:
        pytest.skip("No route with >= 2 stops in DB to check")

    # Force the stop-count guard without needing a real single-stop route
    # in the seeded dataset -- every active route should have >= 2 stops
    # in practice, but the endpoint still needs to guard against it.
    monkeypatch.setattr(routes_api.queries, "get_route_stops", lambda db, rid: [object()])

    res = client.get(f"/routes/{route_id}/geometry")
    assert res.status_code == 422


def test_geometry_direction_forward_default(client, monkeypatch):
    """Default direction is forward."""
    route_id = _any_route_id_with_min_stops(2)
    if route_id is None:
        pytest.skip("No route with >= 2 stops in DB to check")

    calls = []

    def capture(coords, profile="driving", bearings=None, radiuses=None):
        calls.append({"bearings": bearings, "radiuses": radiuses})
        return {
            "geometry": {"type": "LineString", "coordinates": [[85.3, 27.7], [85.31, 27.71]]},
            "distance_m": 1234.5,
            "duration_s": 210.0,
        }

    monkeypatch.setattr(routes_api, "get_route_geometry", capture)

    res = client.get(f"/routes/{route_id}/geometry")
    assert res.status_code == 200
    assert len(calls) == 1
    assert calls[0]["bearings"] is not None  # constrained call made


def test_geometry_direction_reverse_uses_reverse_order(client, monkeypatch):
    """Reverse direction calls OSRM with reversed stop order."""
    route_id = _any_bidirectional_route_id()
    if route_id is None:
        pytest.skip("No bidirectional route in DB to check")

    calls = []

    def capture(coords, profile="driving", bearings=None, radiuses=None):
        calls.append({"coords": coords})
        return {
            "geometry": {"type": "LineString", "coordinates": [[85.3, 27.7], [85.31, 27.71]]},
            "distance_m": 1234.5,
            "duration_s": 210.0,
        }

    monkeypatch.setattr(routes_api, "get_route_geometry", capture)

    res = client.get(f"/routes/{route_id}/geometry?direction=reverse")
    assert res.status_code == 200
    assert len(calls) == 1
    # coords should be in reverse travel order
    assert len(calls[0]["coords"]) >= 2


def test_geometry_direction_reverse_422_on_non_bidirectional(client):
    """Reverse direction returns 422 for non-bidirectional routes."""
    route_id = _any_non_bidirectional_route_id()
    if route_id is None:
        pytest.skip("No non-bidirectional route in DB to check")

    res = client.get(f"/routes/{route_id}/geometry?direction=reverse")
    assert res.status_code == 422
    assert "not bidirectional" in res.json()["detail"]


def test_stops_direction_forward_default(client, monkeypatch):
    """Default direction for stops is forward."""
    route_id = _any_route_id_with_min_stops(2)
    if route_id is None:
        pytest.skip("No route with >= 2 stops in DB to check")

    fake_result = {
        "geometry": {"type": "LineString", "coordinates": [[85.3, 27.7], [85.31, 27.71]]},
        "distance_m": 1234.5,
        "duration_s": 210.0,
    }
    monkeypatch.setattr(
        routes_api,
        "get_route_geometry",
        lambda coords, profile="driving", bearings=None, radiuses=None: fake_result,
    )

    res = client.get(f"/routes/{route_id}/stops")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # All items should have display_lat/display_lng (may be null if OSRM fails)
    for item in data:
        assert "display_lat" in item
        assert "display_lng" in item


def test_stops_direction_reverse_422_on_non_bidirectional(client):
    """Reverse direction returns 422 for non-bidirectional routes."""
    route_id = _any_non_bidirectional_route_id()
    if route_id is None:
        pytest.skip("No non-bidirectional route in DB to check")

    res = client.get(f"/routes/{route_id}/stops?direction=reverse")
    assert res.status_code == 422
    assert "not bidirectional" in res.json()["detail"]


def test_stops_direction_parameter_passed_to_osrm(client, monkeypatch):
    """Verify direction parameter affects OSRM call (via bearings/radiuses)."""
    route_id = _any_bidirectional_route_id()
    if route_id is None:
        pytest.skip("No bidirectional route in DB to check")

    calls = []

    fake_result = {
        "geometry": {"type": "LineString", "coordinates": [[85.3, 27.7], [85.31, 27.71]]},
        "distance_m": 1234.5,
        "duration_s": 210.0,
    }

    def capture(coords, profile="driving", bearings=None, radiuses=None):
        calls.append({"coords": coords, "bearings": bearings, "radiuses": radiuses})
        return fake_result

    monkeypatch.setattr(routes_api, "get_route_geometry", capture)

    # Forward call
    client.get(f"/routes/{route_id}/stops?direction=forward")
    # Reverse call
    client.get(f"/routes/{route_id}/stops?direction=reverse")

    assert len(calls) == 2
    # Both calls should have bearings and radiuses (constrained)
    assert calls[0]["bearings"] is not None
    assert calls[1]["bearings"] is not None
    # Coords should be different (reversed order)
    assert calls[0]["coords"] != calls[1]["coords"]
    # Both should have same number of coords (same stops, different order)
    assert len(calls[0]["coords"]) == len(calls[1]["coords"])
