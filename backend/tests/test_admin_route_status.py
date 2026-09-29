"""
API-level tests for PATCH /routes/{route_id}/status — require a live
Postgres/PostGIS instance (docker compose up -d db) with migration 0002
applied and sample data imported. Skip cleanly if no DB is reachable.

Uses R2295986 (Sundhara-Shankhamul) as the test route: known to be the
sole connection between S0018 and S0069 in the current dataset, and
known to already be 'active' before/after this test runs, so the test
restores it to 'active' in a finally block regardless of outcome.
"""
import pytest
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal

TEST_ROUTE_ID = "R2295986"
ORIGIN_STOP = "S0018"
ADJACENT_STOP = "S0069"


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


def _set_status(client, route_id, new_status, headers):
    return client.patch(
        f"/routes/{route_id}/status",
        json={"status": new_status},
        headers=headers,
    )


def test_status_update_requires_credentials(client):
    # No credential at all. The gate is a dependency, so this is a clean
    # 401 -- not the 422 that the old `Header(...)` declaration produced
    # when the header was simply absent.
    resp = client.patch(f"/routes/{TEST_ROUTE_ID}/status", json={"status": "active"})
    assert resp.status_code == 401, resp.text


def test_status_update_forbidden_for_editor(client, editor_headers):
    """routes:status is admin-only. An editor is authenticated and still
    refused, and the refusal must come before the route lookup -- an
    editor shouldn't be able to probe which route_ids exist."""
    resp = _set_status(client, TEST_ROUTE_ID, "active", editor_headers)
    assert resp.status_code == 403
    assert "routes:status" in resp.json()["detail"]


def test_status_update_forbidden_for_credential_without_scope(
    client, service_key_headers
):
    """A credential scoped for ordinary data entry still cannot flip a
    route's status. This is the whole point of scopes: the old shared key
    could do this, and so could any credential that inherited its
    permissions."""
    resp = _set_status(
        client, TEST_ROUTE_ID, "active", service_key_headers(["routes:write"])
    )
    assert resp.status_code == 403


def test_status_update_rejects_unknown_route(client, admin_headers):
    resp = _set_status(client, "R_DOES_NOT_EXIST", "active", admin_headers)
    assert resp.status_code == 404


def test_status_update_rejects_invalid_status_value(client, admin_headers):
    resp = _set_status(client, TEST_ROUTE_ID, "not_a_real_status", admin_headers)
    assert resp.status_code == 422


def test_status_flip_auto_invalidates_graph(client, admin_headers):
    """
    Setting a route to pending_release must immediately remove it (and
    any stop only reachable via it) from the live routing graph, with no
    separate POST /graph/reload call required. Flipping back to active
    must restore routability. Always restores 'active' afterward.
    """
    try:
        resp = _set_status(client, TEST_ROUTE_ID, "pending_release", admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending_release"

        resp = client.get(
            "/route-finder", params={"origin": ORIGIN_STOP, "destination": ADJACENT_STOP}
        )
        assert resp.status_code == 404, (
            f"{ORIGIN_STOP} should be unroutable while {TEST_ROUTE_ID} is pending_release"
        )

        resp = _set_status(client, TEST_ROUTE_ID, "active", admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

        resp = client.get(
            "/route-finder", params={"origin": ORIGIN_STOP, "destination": ADJACENT_STOP}
        )
        assert resp.status_code == 200, (
            f"{ORIGIN_STOP} should be routable again once {TEST_ROUTE_ID} is active"
        )
    finally:
        # Always leave the route active, regardless of assertion outcome above.
        _set_status(client, TEST_ROUTE_ID, "active", admin_headers)
