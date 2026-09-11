"""
tests/test_record_leg_congestion.py

Unit tests for app/api/routing.py::_record_leg_congestion -- the
BackgroundTask that turns each successful /route-finder ride leg's real
OSRM road_geometry into an upsert into segment_congestion_stats after the
response has already been sent. Previously entirely untested.

Verifies the two skip rules (walking TRANSFER legs and legs with no
OSRM geometry are never recorded) and that the sample is upserted with
the bucketed "now" parameters, then the session is committed and closed.
No live DB or OSRM required -- both are monkeypatched.
"""
import os

# app.api.routing imports app.db.session, which validates required
# Settings fields at import time. Set harmless test values first, same as
# test_attach_road_geometry.py does.
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

import pytest

from app.api import routing
from app.db import queries
from app.schemas import RouteLeg, StopOut


def make_stop(stop_id: str) -> StopOut:
    return StopOut(
        stop_id=stop_id,
        stop_name=stop_id,
        lat=27.70,
        lng=85.31,
        is_major_stop=False,
        is_interchange=False,
        status="active",
    )


def make_ride_leg(route_id: str, from_stop_id: str, to_stop_id: str, geometry=None) -> RouteLeg:
    from_stop = make_stop(from_stop_id)
    to_stop = make_stop(to_stop_id)
    return RouteLeg(
        route_id=route_id,
        route_name=route_id,
        board_stop=from_stop,
        alight_stop=to_stop,
        num_ride_segments=1,
        stops=[from_stop, to_stop],
        road_geometry=geometry,
    )


def _geometry(duration_s=120.0, distance_m=1800.0):
    return {"geometry": {"type": "LineString", "coordinates": []}, "distance_m": distance_m, "duration_s": duration_s}


def test_records_ride_legs_with_geometry_skips_walk_and_none(monkeypatch):
    """Only ride legs with real OSRM road_geometry become samples: a
    TRANSFER (walking) leg and a ride leg that lost its geometry to an
    OSRM failure must both be skipped."""
    ride_leg = make_ride_leg("R1", "S1", "S2", _geometry(duration_s=90.0, distance_m=1500.0))
    walk_leg = make_ride_leg("TRANSFER", "S2", "S3", _geometry(duration_s=240.0))
    no_geometry_leg = make_ride_leg("R2", "S3", "S4", None)

    calls = []
    monkeypatch.setattr(queries, "record_congestion_sample", lambda db, **kw: calls.append(kw))
    monkeypatch.setattr(routing, "day_and_bucket_for", lambda dt: (3, 9))

    db = type("FakeSession", (), {"commit": lambda self: None, "close": lambda self: None})()
    monkeypatch.setattr(routing, "SessionLocal", lambda: db)

    routing._record_leg_congestion([ride_leg, walk_leg, no_geometry_leg])

    assert len(calls) == 1
    assert calls[0]["route_id"] == "R1"
    assert calls[0]["from_stop_id"] == "S1"
    assert calls[0]["to_stop_id"] == "S2"
    assert calls[0]["day_of_week"] == 3
    assert calls[0]["hour_bucket"] == 9
    assert calls[0]["duration_s"] == pytest.approx(90.0)
    assert calls[0]["distance_m"] == pytest.approx(1500.0)


def test_multiple_ride_legs_all_recorded_with_their_own_geometry(monkeypatch):
    """An empty leg list must not crash, and when multiple ride legs each
    carry geometry every one is recorded with its own duration/distance."""
    leg_a = make_ride_leg("R1", "S1", "S2", _geometry(duration_s=60.0, distance_m=900.0))
    leg_b = make_ride_leg("R2", "S2", "S3", _geometry(duration_s=180.0, distance_m=2400.0))

    calls = []
    monkeypatch.setattr(queries, "record_congestion_sample", lambda db, **kw: calls.append(kw))
    monkeypatch.setattr(routing, "day_and_bucket_for", lambda dt: (0, 6))
    db = type("FakeSession", (), {"commit": lambda self: None, "close": lambda self: None})()
    monkeypatch.setattr(routing, "SessionLocal", lambda: db)

    routing._record_leg_congestion([leg_a, leg_b])
    assert len(calls) == 2
    assert [c["route_id"] for c in calls] == ["R1", "R2"]
    assert [c["duration_s"] for c in calls] == [pytest.approx(60.0), pytest.approx(180.0)]

    # And the empty input is a no-op, not an error.
    routing._record_leg_congestion([])


def test_commits_and_closes_session(monkeypatch):
    """The upsert session is committed once (after all samples) and always
    closed, even though _record_leg_congestion runs outside the request
    session's lifecycle."""
    ride_leg = make_ride_leg("R1", "S1", "S2", _geometry())
    calls = {"commits": 0, "closes": 0}
    db = type(
        "FakeSession",
        (),
        {"commit": lambda self: calls.__setitem__("commits", calls["commits"] + 1),
         "close": lambda self: calls.__setitem__("closes", calls["closes"] + 1)},
    )()
    monkeypatch.setattr(routing, "SessionLocal", lambda: db)
    monkeypatch.setattr(queries, "record_congestion_sample", lambda *args, **kwargs: None)
    monkeypatch.setattr(routing, "day_and_bucket_for", lambda dt: (1, 15))

    routing._record_leg_congestion([ride_leg])
    assert calls["commits"] == 1
    assert calls["closes"] == 1