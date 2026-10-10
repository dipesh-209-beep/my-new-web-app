"""
API-level tests for GET /fare — require a live Postgres/PostGIS instance
(docker compose up -d db) with migration 0002 applied. Skip cleanly if no
DB is reachable.

Fully self-contained: inserts and tears down its own fare_rules band
(rather than depending on the shipped dataset's bands) so it pins exact
boundary behavior regardless of what's currently imported.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import FareRule


@pytest.fixture
def client():
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()
    return TestClient(app)


@pytest.fixture
def fare_band(client):
    """A dedicated [500.0, 510.0) km band, min_distance_km <= x < max_distance_km,
    picked well clear of the shipped dataset's real bands (which top out at
    99 km -- see data/processed/fare_rules_clean.csv) so no other row can
    match a query inside it and mask a bug in fare_for_distance()'s own
    filtering, and so this row can't collide with real bands under the
    fare_rules_distance_range_excl exclusion constraint."""
    session = SessionLocal()
    fare_id = f"test-band-{uuid.uuid4().hex[:8]}"
    row = FareRule(
        fare_id=fare_id,
        min_distance_km=500.0,
        max_distance_km=510.0,
        fare_npr_min=25.0,
        fare_npr_max=40.0,
        student_discount_pct=50.0,
        verification_note="test-band: desk estimate, pending gazette confirmation",
    )
    session.add(row)
    session.commit()
    session.close()
    try:
        yield fare_id
    finally:
        session = SessionLocal()
        existing = session.get(FareRule, fare_id)
        if existing is not None:
            session.delete(existing)
            session.commit()
        session.close()


def test_fare_lookup_matches_band_containing_distance(client, fare_band):
    resp = client.get("/fare", params={"distance_km": 505.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fare_id"] == fare_band
    assert body["fare_npr_min"] == 25.0
    assert body["fare_npr_max"] == 40.0
    assert body["student_discount_pct"] == 50.0


def test_fare_exposes_verification_note(client, fare_band):
    """The fare API must surface the rule's verification caveat, so the UI
    can label a fare as estimated/pending rather than authoritative."""
    resp = client.get("/fare", params={"distance_km": 505.0})
    assert resp.status_code == 200
    body = resp.json()
    assert "verification_note" in body
    assert body["verification_note"] == (
        "test-band: desk estimate, pending gazette confirmation"
    )


def test_fare_lookup_is_inclusive_of_min_distance(client, fare_band):
    """Band is [min, max) -- querying exactly at min_distance_km must match."""
    resp = client.get("/fare", params={"distance_km": 500.0})
    assert resp.status_code == 200
    assert resp.json()["fare_id"] == fare_band


def test_fare_lookup_is_exclusive_of_max_distance(client, fare_band):
    """Querying exactly at max_distance_km must NOT match this band (the
    next band up, if any, owns that boundary point)."""
    resp = client.get("/fare", params={"distance_km": 510.0})
    if resp.status_code == 200:
        assert resp.json()["fare_id"] != fare_band
    else:
        assert resp.status_code == 404


def test_fare_lookup_404s_when_no_band_covers_distance(client, fare_band):
    resp = client.get("/fare", params={"distance_km": 99999})
    assert resp.status_code == 404
    assert "99999" in resp.json()["detail"]


def test_fare_lookup_rejects_negative_distance(client):
    resp = client.get("/fare", params={"distance_km": -5})
    assert resp.status_code == 422


def test_fare_lookup_requires_distance_km_param(client):
    resp = client.get("/fare")
    assert resp.status_code == 422
