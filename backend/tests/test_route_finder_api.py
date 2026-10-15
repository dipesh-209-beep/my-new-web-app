"""
API-level tests for GET /route-finder — require a live Postgres/PostGIS
instance (docker compose up -d db) with migration 0002 applied and sample
data imported. Skip cleanly if no DB is reachable.

These specifically guard against a regression where the endpoint returned
one RouteLeg per raw graph edge instead of consolidating consecutive
segments that share the same route_id, which also caused transfer_count
to be computed incorrectly (it was reading pathfinder.py's edge-level
is_transfer flag, which only fires on walking-transfer edges — not on
route changes made at a shared stop).
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


def test_route_finder_consolidates_consecutive_same_route_legs(client):
    """
    S0384 -> S0408 is a known multi-transfer path riding 4 distinct routes,
    none of the connections being a walking transfer (every leg boundary is
    a route change made at a shared stop). Before the fix, each raw graph
    edge became its own leg, several sharing the same route_id back-to-back.
    After the fix, consecutive same-route segments must be merged into one
    leg per route ridden.

    NOTE: this path (and the exact route_ids below) reflect the dataset as
    of the current data/processed/*_clean.csv import -- if a future data
    refresh changes the shortest path here, re-derive fresh expected values
    rather than trying to patch these ones, the same way these were
    derived: run the actual /route-finder call against a freshly loaded DB
    and record what it returns. (The previous fixture pair, S0072/S0326,
    stopped being connected by any route after a later data refresh --
    S0072 has zero route_stops rows in the current dataset -- which is why
    this pair replaces it. Likewise, the second leg used to ride R-NY-06,
    Gopi Krishna-Balkumari, but a later data refresh added R3213434,
    Kalanki-Koteshwor, which the pathfinder now prefers for this stretch
    instead -- R-NY-06 is still a real, separate route in the dataset;
    this is a shortest-path choice changing, not a route being renamed.)
    """
    resp = client.get("/route-finder", params={"origin": "S0384", "destination": "S0408"})
    assert resp.status_code == 200
    body = resp.json()

    legs = body["legs"]
    route_ids = [leg["route_id"] for leg in legs]

    # No two consecutive legs should share a route_id -- if they did,
    # they should have been merged into one leg.
    for prev_route, next_route in zip(route_ids, route_ids[1:]):
        assert prev_route != next_route, (
            f"Consecutive legs both on {prev_route!r} were not consolidated: {route_ids}"
        )

    # Known shape of this specific path as of the current dataset.
    assert route_ids == [
        "R-NY-05",
        "R3213434",
        "R2295734",
        "R-GAP-12",
    ]


def test_route_finder_transfer_count_matches_leg_boundaries(client):
    """
    transfer_count must equal len(legs) - 1: the number of times the
    rider actually changes route, regardless of whether the graph edge
    connecting them was flagged is_transfer (walking) or not (same-stop
    route change).
    """
    resp = client.get("/route-finder", params={"origin": "S0384", "destination": "S0408"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["transfer_count"] == len(body["legs"]) - 1
    assert body["transfer_count"] == 3


def test_route_finder_leg_num_ride_segments_reflects_consolidated_hops(client):
    """
    A consolidated leg spanning multiple raw hops on the same route must
    report num_ride_segments > 1, and the total across all legs must be
    internally consistent (each leg's num_ride_segments counts the hops
    folded into it, not just always 1 as it did pre-fix).
    """
    resp = client.get("/route-finder", params={"origin": "S0384", "destination": "S0408"})
    assert resp.status_code == 200
    legs = resp.json()["legs"]

    # Second leg on R3213434 (Kalanki-Koteshwor) covers 10 raw hops per
    # the known shape of this path -- the clearest example of
    # consolidation in this dataset.
    assert legs[1]["route_id"] == "R3213434"
    assert legs[1]["num_ride_segments"] == 10

    # At least one leg must have been consolidated (num_ride_segments > 1) --
    # guards against a future regression back to "always 1".
    assert any(leg["num_ride_segments"] > 1 for leg in legs)


def test_route_finder_legs_carry_operator_and_direction(client):
    """
    Each ride leg must expose the operating company (resolved from the
    linked Operator row, falling back to the free-text route.operator
    column) and a direction derived from the board/alight stops'
    positions in the route's stop sequence. These are what the frontend
    timeline shows ("towards X", "run by Y"); dropping them makes the UI
    invent "bus service" labels instead of showing real data.
    """
    resp = client.get("/route-finder", params={"origin": "S0384", "destination": "S0408"})
    assert resp.status_code == 200
    legs = resp.json()["legs"]

    ride_legs = [leg for leg in legs if leg["route_id"] != "TRANSFER"]
    assert len(ride_legs) == 4, ride_legs

    for leg in ride_legs:
        assert leg.get("operator_name"), (
            f"ride leg {leg['route_id']} missing operator_name: {leg}"
        )
        assert leg.get("direction_towards"), (
            f"ride leg {leg['route_id']} missing direction_towards: {leg}"
        )


def test_route_finder_exposes_estimated_time_and_per_option_fare(client):
    """
    The result (and every alternative) must carry a backend-computed
    estimated trip time and its own fare, so the UI can show a labeled
    estimate instead of presenting the OSRM driving-profile duration as
    the trip time, and so a selected alternative never borrows the
    primary result's fare. Fare is banded on ridden distance only.
    """
    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "destination": "S0408", "include_alternatives": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()

    # Whole-trip estimate is present and positive; each leg carries its own.
    assert body["total_estimated_duration_s"] > 0
    for leg in body["legs"]:
        assert leg["estimated_duration_s"] is not None
        assert leg["estimated_duration_s"] > 0

    # The primary fare, if any, exposes its verification caveat.
    if body["fare"] is not None:
        assert "verification_note" in body["fare"]

    # Each alternative carries its own estimate and fare (or an explicit
    # null when no ride leg / no matching band), never omitted.
    for alt in body["alternatives"]:
        assert alt["total_estimated_duration_s"] is not None
        assert alt["total_estimated_duration_s"] > 0
        assert "fare" in alt
        for leg in alt["legs"]:
            assert leg["estimated_duration_s"] is not None


def test_route_finder_exposes_rank_reason(client):
    """
    The response must explain why the primary was chosen, using only codes
    the frontend knows how to translate. S0384 -> S0408 is a transfer-only
    path (no route connects the stops directly), so the reason is
    only_option; the key must always be present so the UI never has to
    guess.
    """
    from app.routing.ranking import RANK_REASONS

    resp = client.get("/route-finder", params={"origin": "S0384", "destination": "S0408"})
    assert resp.status_code == 200
    body = resp.json()
    assert "rank_reason" in body
    assert body["rank_reason"] in RANK_REASONS
    assert body["rank_reason"] == "only_option"


def test_route_finder_reason_never_contradicts_alternatives(client):
    """
    rank_reason must be consistent with what the response actually shows:
    'only_option' is only truthful when there is a single option, so a
    non-empty alternatives list can never come with it. S0384 -> S0408 is
    the live transfer-only case that used to regress here.
    """
    from app.routing.ranking import RANK_REASONS

    resp = client.get(
        "/route-finder",
        params={"origin": "S0384", "destination": "S0408", "include_alternatives": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rank_reason"] in RANK_REASONS
    if body["alternatives"]:
        assert body["rank_reason"] != "only_option", (
            "only_option contradicts the surfaced alternatives: "
            f"{[a['label'] for a in body['alternatives']]}"
        )
