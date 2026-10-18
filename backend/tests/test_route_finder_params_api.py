"""
tests/test_route_finder_params_api.py

API-level tests for the GET /route-finder query parameters that had no
HTTP-layer coverage: the `via` multi-waypoint list and `max_transfers`
transfer cap. Requires a live Postgres/PostGIS instance (docker compose
up -d db) with data imported -- skips cleanly if no DB is reachable, same
as test_route_finder_api.py.

Stop IDs were chosen against the current data/processed/*_clean.csv
import (re-derive them if a future data refresh changes the network):

  - via test: S0384 (Kandaghari) -> via S0107 (Koteshwor) -> S0056
    (Tripureshwor) -- all three are consecutive stops on R-NY-05, so the
    chain should ride the same route with the via stop as the seam.
  - max_transfers test: S0384 -> S0408 is a validated 3-transfer path
    (see test_route_finder_api.py); the transfer cap must reject caps
    below what the network actually requires.
"""
import pytest
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal


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


# --- via ---


def test_route_finder_via_chains_legs_and_echoes_via_stops(client):
    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "via": "S0107", "destination": "S0056"},
    )
    assert resp.status_code == 200
    body = resp.json()

    # The via list comes back verbatim in the response.
    assert body["via_stop_ids"] == ["S0107"]

    # Two chained legs: origin->via then via->destination, each ending/
    # starting at the via stop -- S0107 is the seam, not repeated.
    legs = body["legs"]
    assert len(legs) == 2
    assert legs[0]["board_stop"]["stop_id"] == "S0384"
    assert legs[0]["alight_stop"]["stop_id"] == "S0107"
    assert legs[1]["board_stop"]["stop_id"] == "S0107"
    assert legs[1]["alight_stop"]["stop_id"] == "S0056"

    # Chaining forces a re-board at the via stop, so transfer_count == 1.
    assert body["transfer_count"] == 1


def test_route_finder_via_disables_alternatives(client):
    """include_alternatives=true alongside via must still return an empty
    alternatives list -- per-leg alternatives would combinatorially
    explode, so the endpoint ignores the flag when via is present."""
    resp = client.get(
        "/route-finder",
        params={
            "origin": "S0384",
            "via": "S0107",
            "destination": "S0056",
            "include_alternatives": "true",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["alternatives"] == []


def test_route_finder_via_missing_stop_returns_404(client):
    """A via stop with no route connecting the surrounding legs reports a
    404 with the failing leg described in the detail, rather than a 500."""
    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "via": "S9999", "destination": "S0056"},
    )
    assert resp.status_code == 404
    assert "S9999" in resp.json()["detail"]


# --- max_transfers ---


def test_route_finder_max_transfers_rejects_insufficient_cap(client):
    """S0384 -> S0408 requires 3 transfers. Capping below that must 404
    with a message naming the cap, not fall back to an over-transfer path."""
    for cap in (0, 1):
        resp = client.get(
            "/route-finder",
            params={"origin": "S0384", "destination": "S0408", "max_transfers": cap},
        )
        assert resp.status_code == 404, f"max_transfers={cap} should 404"
        assert f"max {cap} transfers" in resp.json()["detail"]


def test_route_finder_max_transfers_respected_when_sufficient(client):
    """At the cap the search finds a real ≤cap-transfer path; at 3 it finds
    the same preferred 3-transfer path as the unlimited search."""
    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "destination": "S0408", "max_transfers": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["transfer_count"] <= 2

    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "destination": "S0408", "max_transfers": 3},
    )
    assert resp.status_code == 200
    assert resp.json()["transfer_count"] == 3
    assert [leg["route_id"] for leg in resp.json()["legs"]] == [
        "R-NY-05",
        "R3213434",
        "R2295734",
        "R-GAP-12",
    ]


def test_route_finder_max_transfers_validation(client):
    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "destination": "S0408", "max_transfers": -1},
    )
    assert resp.status_code == 422


# --- via cap + rate limit (AUD-003) ----------------------------------------


def test_route_finder_rejects_more_than_max_via(client):
    """An over-long via list is refused by query validation before any
    routing work runs -- not by grinding through an unbounded number of
    independent leg solves."""
    from app.api.routing import MAX_VIA_STOPS

    params = [("origin", "S0384")]
    params += [("via", "S0107") for _ in range(MAX_VIA_STOPS + 1)]
    params += [("destination", "S0056")]
    resp = client.get("/route-finder", params=params)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["loc"] == ["query", "via"]
    assert resp.json()["detail"][0]["type"] == "too_long"


def test_route_finder_accepts_up_to_max_via(client):
    """The documented maximum still passes validation and routes."""
    from app.api.routing import MAX_VIA_STOPS

    params = [("origin", "S0384")]
    params += [("via", "S0107") for _ in range(MAX_VIA_STOPS)]
    params += [("destination", "S0056")]
    resp = client.get("/route-finder", params=params)
    assert resp.status_code != 422, resp.text


def test_route_finder_is_rate_limited(client, monkeypatch):
    """GET /route-finder carries its own per-client application rate limit
    (not only nginx's global one). The limit is read per request from
    Settings, so a tiny value can be injected to prove enforcement without
    hammering the endpoint."""
    import app.api.routing as routing
    from app.core.config import Settings
    from app.core.rate_limit import limiter

    class _TinyLimitSettings(Settings):
        ROUTE_FINDER_RATE_LIMIT: str = "2/minute"

    monkeypatch.setattr(routing, "get_settings", lambda: _TinyLimitSettings())
    limiter.reset()
    try:
        statuses = [
            client.get(
                "/route-finder", params={"origin": "S0384", "destination": "S0056"}
            ).status_code
            for _ in range(3)
        ]
        assert statuses[0] != 429 and statuses[1] != 429
        assert statuses[2] == 429, f"expected the 3rd request to be limited, got {statuses}"
    finally:
        limiter.reset()
