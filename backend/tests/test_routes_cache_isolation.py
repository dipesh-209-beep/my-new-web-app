"""
Regression tests for cache isolation bugs in GET /routes endpoints.

Bug 1: GET /routes/{route_id} and GET /routes/{route_id}/stops shared the
same cache namespace ("routes") and key (route_id), so whichever endpoint
was called second would silently overwrite the other's cache entry.

Bug 2: Endpoints were caching raw SQLAlchemy ORM objects instead of
validated Pydantic models. This worked accidentally with the in-memory
cache (lazy-loaded relationships stay populated once touched), but
pickling for Redis doesn't preserve that state — a Redis cache hit would
hand back an object whose relationship access raises DetachedInstanceError.

These tests verify both bugs are fixed by:
1. Calling endpoints in both orders and verifying correct return types
2. With Redis enabled, verifying cached responses don't raise
   DetachedInstanceError on relationship access
"""
import pytest
import redis as redis_module
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.core.redis_client import get_redis
from app.core.response_cache import invalidate_all
from sqlalchemy.exc import OperationalError
from sqlalchemy import select
from app.models import Route


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


@pytest.fixture
def route_id():
    """Get a valid route_id from the database."""
    session = SessionLocal()
    try:
        rid = session.execute(select(Route.route_id).limit(1)).scalar_one_or_none()
        if rid is None:
            pytest.skip("No routes in DB to check")
        return rid
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_all()
    yield
    invalidate_all()


class TestRoutesCacheIsolation:
    """Test that read_route and read_route_stops don't share cache entries."""

    def test_call_order_read_route_then_stops(self, client, route_id):
        """read_route called first, then read_route_stops, then read_route again."""
        r1 = client.get(f"/routes/{route_id}")
        assert r1.status_code == 200
        assert isinstance(r1.json(), dict)
        assert "route_id" in r1.json()

        r2 = client.get(f"/routes/{route_id}/stops")
        assert r2.status_code == 200
        assert isinstance(r2.json(), list)
        assert len(r2.json()) > 0

        # read_route again must return a dict (RouteOut), not a list
        r3 = client.get(f"/routes/{route_id}")
        assert r3.status_code == 200
        assert isinstance(r3.json(), dict), "read_route returned list after read_route_stops call"
        assert "route_id" in r3.json()

    def test_call_order_read_stops_then_route(self, client, route_id):
        """read_route_stops called first, then read_route, then read_route_stops again."""
        r1 = client.get(f"/routes/{route_id}/stops")
        assert r1.status_code == 200
        assert isinstance(r1.json(), list)

        r2 = client.get(f"/routes/{route_id}")
        assert r2.status_code == 200
        assert isinstance(r2.json(), dict)
        assert "route_id" in r2.json()

        # read_route_stops again must return a list
        r3 = client.get(f"/routes/{route_id}/stops")
        assert r3.status_code == 200
        assert isinstance(r3.json(), list), "read_route_stops returned dict after read_route call"
        assert len(r3.json()) > 0


class TestRoutesCacheWithRedis:
    """Test that cached responses work correctly with Redis (Bug 2 fix).

    These tests require a live Redis at redis://localhost:6379/0.
    """

    @pytest.fixture(autouse=True)
    def _require_redis(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        from app.core import redis_client
        redis_client._reset_for_tests()
        client = get_redis()
        try:
            client.ping()
        except redis_module.RedisError:
            pytest.skip("No live Redis available at redis://localhost:6379/0")
        invalidate_all()
        yield
        invalidate_all()

    def test_read_route_cached_response_has_operator(self, client, route_id):
        """After Redis cache hit, RouteOut.operator_ref relationship must be accessible."""
        # First call populates cache
        r1 = client.get(f"/routes/{route_id}")
        assert r1.status_code == 200
        data1 = r1.json()
        assert isinstance(data1, dict)
        # operator may be None if not set, but access must not raise
        _ = data1.get("operator")

        # Second call hits Redis cache
        r2 = client.get(f"/routes/{route_id}")
        assert r2.status_code == 200
        data2 = r2.json()
        assert isinstance(data2, dict)
        # This would raise DetachedInstanceError if Bug 2 not fixed
        _ = data2.get("operator")
        assert data2 == data1

    def test_read_route_stops_cached_response_has_stop(self, client, route_id):
        """After Redis cache hit, RouteStopOut.stop relationship must be accessible."""
        # First call populates cache
        r1 = client.get(f"/routes/{route_id}/stops")
        assert r1.status_code == 200
        data1 = r1.json()
        assert isinstance(data1, list)
        assert len(data1) > 0
        # Each item must have a stop with stop_id
        for item in data1:
            assert "stop" in item
            assert "stop_id" in item["stop"]

        # Second call hits Redis cache
        r2 = client.get(f"/routes/{route_id}/stops")
        assert r2.status_code == 200
        data2 = r2.json()
        assert isinstance(data2, list)
        assert len(data2) > 0
        # This would raise DetachedInstanceError if Bug 2 not fixed
        for item in data2:
            assert "stop" in item
            assert "stop_id" in item["stop"]
        assert data2 == data1


class TestStopsCacheWithRedis:
    """Test that read_stop cached response works with Redis (defensive fix)."""

    @pytest.fixture(autouse=True)
    def _require_redis(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        from app.core import redis_client
        redis_client._reset_for_tests()
        client = get_redis()
        try:
            client.ping()
        except redis_module.RedisError:
            pytest.skip("No live Redis available at redis://localhost:6379/0")
        invalidate_all()
        yield
        invalidate_all()

    def test_read_stop_cached_response_accessible(self, client):
        """After Redis cache hit, StopOut must be fully accessible."""
        session = SessionLocal()
        try:
            from app.models import Stop
            stop_id = session.execute(select(Stop.stop_id).limit(1)).scalar_one_or_none()
            if stop_id is None:
                pytest.skip("No stops in DB to check")
        finally:
            session.close()

        # First call populates cache
        r1 = client.get(f"/stops/{stop_id}")
        assert r1.status_code == 200
        data1 = r1.json()
        assert isinstance(data1, dict)
        assert "stop_id" in data1

        # Second call hits Redis cache
        r2 = client.get(f"/stops/{stop_id}")
        assert r2.status_code == 200
        data2 = r2.json()
        assert isinstance(data2, dict)
        assert data2["stop_id"] == stop_id
        assert data2 == data1