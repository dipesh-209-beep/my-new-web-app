"""
tests/test_congestion_api.py

API-level tests for GET /congestion and GET /congestion/buckets: the
classification thresholds, Nepal-time defaults, and hour-bucket rounding
are previously-untested at the HTTP layer. The DB-backed query underneath
get_congestion is monkeypatched so these exercises the endpoint's own
logic (defaulting, bucketing, classification, response shape) against a
controlled input rather than whatever data happens to be seeded. A live
Postgres is still required so the FastAPI app / TestClient boot exactly
as they do in the other API test files; the query itself is the only
piece substituted.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.db import queries
from app.api import congestion as congestion_api
from app.routing.time_buckets import NEPAL_TZ

_MODERATE = congestion_api._MODERATE_RATIO
_HEAVY = congestion_api._HEAVY_RATIO


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


def _segment_row(route_id, from_stop_id, to_stop_id, avg, free, sample_count=1, is_seeded=False):
    """A (SegmentCongestionStat, free_flow_duration_s) tuple shaped exactly
    like what queries.get_congestion_stats returns, built from a plain
    namespace so the endpoint's read-only attribute access works."""
    stat = SimpleNamespace(
        route_id=route_id,
        from_stop_id=from_stop_id,
        to_stop_id=to_stop_id,
        avg_duration_s=avg,
        avg_distance_m=1200.0,
        sample_count=sample_count,
        is_seeded=is_seeded,
    )
    return (stat, free)


# --- _classify unit tests (threshold boundaries) ---


def test_classify_threshold_boundaries():
    # Just below the moderate threshold is still free_flow (ratio 1.0-1.15).
    assert congestion_api._classify(1.0) == "free_flow"
    assert congestion_api._classify(_MODERATE - 0.001) == "free_flow"
    # Exactly at the moderate threshold -> moderate.
    assert congestion_api._classify(_MODERATE) == "moderate"
    assert congestion_api._classify(_HEAVY - 0.001) == "moderate"
    # At and above the heavy threshold -> heavy.
    assert congestion_api._classify(_HEAVY) == "heavy"
    assert congestion_api._classify(4.2) == "heavy"


# --- GET /congestion: response shape + classification ---


def test_congestion_classifies_segments_with_echoed_day_and_bucket(client, monkeypatch):
    rows = [
        # ratio 1.0 -> free_flow
        _segment_row("R1", "S1", "S2", avg=100.0, free=100.0),
        # ratio 1.3 -> moderate
        _segment_row("R2", "S3", "S4", avg=130.0, free=100.0),
        # ratio 2.0 -> heavy
        _segment_row("R3", "S5", "S6", avg=200.0, free=100.0),
    ]
    monkeypatch.setattr(queries, "get_congestion_stats", lambda db, day_of_week, hour_bucket: rows)

    resp = client.get("/congestion", params={"day_of_week": 3, "hour": 10})
    assert resp.status_code == 200
    body = resp.json()
    # hour=10 rounds down to its 3-hour bucket (9-12) on the way out.
    assert body["day_of_week"] == 3
    assert body["hour_bucket"] == 9

    segs = body["segments"]
    assert [s["congestion_level"] for s in segs] == ["free_flow", "moderate", "heavy"]
    assert segs[0]["route_id"] == "R1"
    assert segs[1]["congestion_ratio"] == pytest.approx(1.3)
    assert segs[2]["sample_count"] == 1


# --- GET /congestion: Nepal-time defaults ---


def test_congestion_defaults_to_nepal_now(client, monkeypatch):
    """No day/hour params -> 'right now' in Nepal time. Fix the clock to
    Wednesday (weekday 2) 10:30 NPT -> hour_bucket 9, and expect the API
    to surface exactly that."""
    monkeypatch.setattr(
        congestion_api,
        "now_in_nepal",
        lambda: datetime(2026, 9, 9, 10, 30, tzinfo=NEPAL_TZ),
    )
    monkeypatch.setattr(queries, "get_congestion_stats", lambda db, day_of_week, hour_bucket: [])
    # Use a distinct hour= param value so no cached key from another test collides.
    resp = client.get("/congestion", params={"day_of_week": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["day_of_week"] == 2
    assert body["hour_bucket"] == 9


def test_congestion_defaults_day_but_uses_provided_hour(client, monkeypatch):
    """A provided hour overrides 'now', but the default day still comes
    from Nepal today -- the two defaults are independent."""
    monkeypatch.setattr(
        congestion_api,
        "now_in_nepal",
        lambda: datetime(2026, 9, 9, 10, 30, tzinfo=NEPAL_TZ),  # Wednesday
    )
    monkeypatch.setattr(queries, "get_congestion_stats", lambda db, day_of_week, hour_bucket: [])
    resp = client.get("/congestion", params={"hour": 13})
    assert resp.status_code == 200
    body = resp.json()
    assert body["day_of_week"] == 2  # defaulted from Nepal now
    assert body["hour_bucket"] == 12  # hour=13 rounded down to 12


def test_congestion_hour_bucket_rounding(client, monkeypatch):
    """Every hour maps onto its 3-hour bucket start: 0/5/19 -> 0/3/18."""
    monkeypatch.setattr(queries, "get_congestion_stats", lambda db, day_of_week, hour_bucket: [])
    for hour, expect_bucket in [(0, 0), (5, 3), (19, 18), (23, 21)]:
        resp = client.get("/congestion", params={"day_of_week": 1, "hour": hour})
        assert resp.status_code == 200
        assert resp.json()["hour_bucket"] == expect_bucket, f"hour={hour}"


def test_congestion_free_flow_zero_anchor_guards_ratio(client, monkeypatch):
    """A stored free_flow_duration_s of 0 (or a fallback that returned 0)
    would divide by zero -- the endpoint must treat it as ratio 1.0
    (free_flow) rather than crash."""
    rows = [_segment_row("R1", "S1", "S2", avg=100.0, free=0.0)]
    monkeypatch.setattr(queries, "get_congestion_stats", lambda db, day_of_week, hour_bucket: rows)
    resp = client.get("/congestion", params={"day_of_week": 1, "hour": 3})
    assert resp.status_code == 200
    seg = resp.json()["segments"][0]
    assert seg["congestion_ratio"] == pytest.approx(1.0)
    assert seg["congestion_level"] == "free_flow"


def test_congestion_rejects_out_of_range_params(client):
    resp = client.get("/congestion", params={"day_of_week": 7})
    assert resp.status_code == 422
    resp = client.get("/congestion", params={"hour": 24})
    assert resp.status_code == 422


# --- GET /congestion/buckets ---


def test_congestion_buckets_endpoint(client):
    resp = client.get("/congestion/buckets")
    assert resp.status_code == 200
    # The fixed 3-hour bucket starts, in order -- the frontend time picker
    # reads this instead of hardcoding it.
    assert resp.json() == {"hour_buckets": [0, 3, 6, 9, 12, 15, 18, 21]}