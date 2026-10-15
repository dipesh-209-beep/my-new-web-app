"""R4: frequency / service-hours serialization on RouteOut.

The routes table has always stored `frequency_min`, `service_start_time`
and `service_end_time` (models/route.py), but RouteOut never exposed them
and nothing validated them. These tests pin the read-time contract:

  * only a usable, whole, positive frequency is surfaced -- zero, negative,
    fractional, boolean, non-numeric, and non-finite values become None;
  * a service time is surfaced as "HH:MM" (from a datetime.time or a cache
    round-tripped string), never as a raw/malformed value;
  * a one-sided or reversed window is dropped entirely rather than shown as
    a schedule;
  * the model serializes to JSON-safe strings, so the Redis response cache
    round trip (model_dump -> json -> model_validate) is lossless.

No database is required for the unit cases; the integration cases skip when
no live DB is reachable, following the suite's existing convention.
"""
import json
from datetime import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.schemas import RouteOut


def make_route(**overrides) -> RouteOut:
    """Build a RouteOut from a minimal from_attributes source object.

    Only the fields RouteOut requires are supplied; the optional
    frequency/service attrs are set per test, and a test that omits them
    exercises the "attribute absent" path.
    """
    base = dict(
        route_id="R_TEST",
        route_name="Test Route",
        vehicle_type="bus",
        start_stop_id="S0001",
        end_stop_id="S0002",
        total_stops=2,
        status="active",
        is_bidirectional=False,
    )
    base.update(overrides)
    return RouteOut.model_validate(SimpleNamespace(**base))


class TestFrequency:
    def test_valid_positive_frequency_is_preserved(self):
        assert make_route(frequency_min=15).frequency_min == 15
        assert make_route(frequency_min=30).frequency_min == 30

    def test_missing_frequency_is_none(self):
        assert make_route().frequency_min is None
        assert make_route(frequency_min=None).frequency_min is None

    @pytest.mark.parametrize("value", [0, -5, 15.5, True, False, "abc", float("nan"), float("inf")])
    def test_unusable_frequency_becomes_none(self, value):
        assert make_route(frequency_min=value).frequency_min is None

    def test_numeric_string_frequency_is_accepted(self):
        # A cache round trip stores the int, but a hand-built payload may
        # carry it as a string; a whole positive string is still valid.
        assert make_route(frequency_min="30").frequency_min == 30


class TestServiceWindow:
    def test_valid_window_is_formatted_as_hh_mm(self):
        route = make_route(service_start_time=time(6, 0), service_end_time=time(20, 0))
        assert route.service_start_time == "06:00"
        assert route.service_end_time == "20:00"

    def test_missing_window_is_none(self):
        route = make_route()
        assert route.service_start_time is None
        assert route.service_end_time is None

    def test_one_sided_window_is_fully_dropped(self):
        start_only = make_route(service_start_time=time(6, 0))
        assert start_only.service_start_time is None
        assert start_only.service_end_time is None

        end_only = make_route(service_end_time=time(20, 0))
        assert end_only.service_start_time is None
        assert end_only.service_end_time is None

    def test_start_not_before_end_is_dropped(self):
        # No overnight/cross-midnight semantics exist in the schema, so a
        # reversed window must not be presented as a same-day schedule.
        reversed_ = make_route(service_start_time=time(20, 0), service_end_time=time(6, 0))
        assert reversed_.service_start_time is None
        assert reversed_.service_end_time is None

        equal = make_route(service_start_time=time(6, 0), service_end_time=time(6, 0))
        assert equal.service_start_time is None
        assert equal.service_end_time is None

    def test_invalid_time_is_none_and_drops_the_window(self):
        route = make_route(service_start_time="25:00", service_end_time=time(20, 0))
        assert route.service_start_time is None
        assert route.service_end_time is None

    def test_malformed_time_is_none(self):
        assert make_route(service_start_time="nope").service_start_time is None


class TestSerialization:
    def test_model_dumps_to_json_safe_strings(self):
        route = make_route(
            frequency_min=15,
            service_start_time=time(6, 0),
            service_end_time=time(20, 0),
        )
        # json.dumps would raise TypeError on a datetime.time; the fields
        # must already be plain strings for the Redis cache write path.
        payload = json.dumps(route.model_dump())
        assert '"frequency_min": 15' in payload
        assert '"service_start_time": "06:00"' in payload

    def test_cache_round_trip_is_lossless(self):
        route = make_route(
            frequency_min=15,
            service_start_time=time(6, 0),
            service_end_time=time(20, 0),
        )
        rehydrated = RouteOut.model_validate(json.loads(json.dumps(route.model_dump())))
        assert rehydrated.model_dump() == route.model_dump()


# ---------------------------------------------------------------------------
# Integration: the live endpoints must expose the (validated) fields
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()
    return TestClient(app)


class TestRoutesApiServiceInfo:
    def test_listed_routes_expose_only_valid_service_values(self, client):
        resp = client.get("/routes", params={"limit": 200})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items, "expected the dataset to contain routes"

        import re

        hhmm = re.compile(r"^\d{2}:\d{2}$")
        seen_frequency = False
        for item in items:
            for field in ("frequency_min", "service_start_time", "service_end_time"):
                assert field in item, f"{field} missing from GET /routes payload"

            freq = item["frequency_min"]
            if freq is not None:
                assert isinstance(freq, int) and freq > 0
                seen_frequency = True

            start, end = item["service_start_time"], item["service_end_time"]
            if start is None or end is None:
                assert start is None and end is None, "window must be all-or-nothing"
            else:
                assert hhmm.match(start) and hhmm.match(end)
                assert start < end

        # The dataset ships a near-uniform frequency, so a page of 200 must
        # contain at least one real value -- otherwise the assertion above is
        # vacuous and wouldn't catch a regression to always-None.
        assert seen_frequency, "expected at least one route with a frequency in the dataset"

    def test_route_detail_exposes_service_values(self, client):
        listing = client.get("/routes", params={"limit": 200}).json()["items"]
        target = next((r for r in listing if r["frequency_min"] is not None), None)
        if target is None:
            pytest.skip("No route with a frequency value in the dataset")

        detail = client.get(f"/routes/{target['route_id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["frequency_min"] == target["frequency_min"]
        assert body["service_start_time"] == target["service_start_time"]
        assert body["service_end_time"] == target["service_end_time"]
