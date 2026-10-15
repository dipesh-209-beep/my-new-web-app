"""Focused tests for scripts/compute_osrm_route_distances.py.

That script talks to a real database and a live OSRM, so every test here
replaces both: a fake Session plus a fake queries.get_active_routes for
the rows, a fake get_route_geometry for the measurement, and a spy for
the cache invalidator. No database connection is opened and no OSRM
request is made.

What's covered:
  * idempotency -- a plain run only computes routes that are still null,
    and a second run is a no-op;
  * --force recomputes every active route;
  * OSRM failures and unusable measurements are skipped, not written, and
    never clobber an existing value;
  * the routes/route_detail caches are invalidated exactly once per
    changed committed batch (the periodic one and the final partial one),
    after the commit succeeds and never for a batch that changed nothing;
  * an interruption after an earlier batch has committed leaves that
    batch's caches already invalidated, and a failed commit never
    invalidates its own uncommitted batch.
"""
import sys
from decimal import Decimal

import pytest

import scripts.compute_osrm_route_distances as script
from app.routing.osrm_client import OSRMError


class _Stop:
    def __init__(self, lat, lng):
        self.lat = lat
        self.lng = lng


class _RouteStop:
    def __init__(self, sequence_no, stop):
        self.sequence_no = sequence_no
        self.stop = stop


class _Route:
    def __init__(self, route_id, stops, osrm_distance_km=None):
        self.route_id = route_id
        self.osrm_distance_km = osrm_distance_km
        self.route_stops = [_RouteStop(i, s) for i, s in enumerate(stops)]


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.closed = False
        # Ordered (kind, detail) log so tests can assert that an
        # invalidation lands after the commit it belongs to.
        self.events = []
        # When set, commit() with this 1-based call number raises instead
        # of succeeding, simulating a later batch failing to persist.
        self.fail_commit_number = None

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1
        self.events.append(("commit", self.commits))
        if self.fail_commit_number == self.commits:
            raise RuntimeError(f"commit #{self.commits} failed")

    def close(self):
        self.closed = True


# Roughly 400 m apart at this latitude, so _thin_waypoints keeps all three
# (its threshold is 80 m).
_SPREAD = [_Stop(27.700, 85.300), _Stop(27.703, 85.303), _Stop(27.706, 85.306)]


class _Harness:
    def __init__(self, monkeypatch, routes, geometry):
        self.monkeypatch = monkeypatch
        self.session = _FakeSession()
        self.routes = routes
        self.geometry = geometry
        self.calls = []
        self.invalidated = []

        monkeypatch.setattr(script, "SessionLocal", lambda: self.session)

        class _Queries:
            @staticmethod
            def get_active_routes(db):
                assert db is self.session
                return routes

        monkeypatch.setattr(script, "queries", _Queries)
        monkeypatch.setattr(script, "get_route_geometry", self._get_geometry)
        monkeypatch.setattr(script, "invalidate_cache", self._record_invalidation)

    def _record_invalidation(self, namespace):
        self.invalidated.append(namespace)
        self.session.events.append(("invalidate", namespace))

    def _get_geometry(self, coords):
        self.calls.append(list(coords))
        if isinstance(self.geometry, BaseException):
            raise self.geometry
        if callable(self.geometry):
            return self.geometry(coords)
        return self.geometry

    def run(self, *extra_args):
        self.monkeypatch.setattr(
            sys, "argv", ["compute_osrm_route_distances.py", *extra_args]
        )
        script.main()


@pytest.fixture
def harness(monkeypatch):
    def _build(routes, geometry):
        return _Harness(monkeypatch, routes, geometry)

    return _build


# --- idempotency / force ---------------------------------------------------

def test_plain_run_only_computes_null_routes(harness):
    pending = _Route("R1", _SPREAD, osrm_distance_km=None)
    done = _Route("R2", _SPREAD, osrm_distance_km=4.8)
    h = harness([pending, done], {"distance_m": 5000.0})

    h.run()

    assert len(h.calls) == 1, "only the null route should hit OSRM"
    assert pending.osrm_distance_km == 5.0
    assert done.osrm_distance_km == 4.8, "an already-set value must be left alone"
    assert h.session.added == [pending]
    assert h.invalidated == ["routes", "route_detail"]


def test_second_run_is_a_no_op(harness):
    route = _Route("R1", _SPREAD, osrm_distance_km=None)
    h = harness([route], {"distance_m": 5000.0})

    h.run()
    assert route.osrm_distance_km == 5.0
    calls_after_first = len(h.calls)
    h.invalidated.clear()

    h.run()

    assert len(h.calls) == calls_after_first, "nothing left to compute"
    assert h.invalidated == [], "no change on the second run -> no invalidation"
    assert h.session.added == [route]


def test_force_recomputes_every_active_route(harness):
    r1 = _Route("R1", _SPREAD, osrm_distance_km=4.8)
    r2 = _Route("R2", _SPREAD, osrm_distance_km=4.8)
    h = harness([r1, r2], {"distance_m": 6000.0})

    h.run("--force")

    assert len(h.calls) == 2
    assert r1.osrm_distance_km == 6.0
    assert r2.osrm_distance_km == 6.0
    assert h.invalidated == ["routes", "route_detail"]


def test_force_with_unchanged_values_does_not_invalidate(harness):
    route = _Route("R1", _SPREAD, osrm_distance_km=5.0)
    h = harness([route], {"distance_m": 5000.0})

    h.run("--force")

    assert route.osrm_distance_km == 5.0
    assert h.invalidated == [], "recomputed to the same value -> no invalidation"


def test_decimal_value_equal_to_measurement_is_not_a_change(harness):
    # Numeric(6, 2) columns load back as Decimal; comparing them to the
    # rounded float must detect "same value", or an idle --force run would
    # needlessly bust the cache.
    route = _Route("R1", _SPREAD, osrm_distance_km=Decimal("5.00"))
    h = harness([route], {"distance_m": 5000.0})

    h.run("--force")

    assert h.invalidated == []


# --- OSRM failure handling -------------------------------------------------

def test_osrm_error_skips_route_without_writing(harness):
    route = _Route("R1", _SPREAD, osrm_distance_km=None)
    h = harness([route], OSRMError("boom"))

    h.run()

    assert route.osrm_distance_km is None
    assert h.session.added == []
    assert h.invalidated == []


def test_osrm_error_under_force_keeps_existing_value(harness):
    route = _Route("R1", _SPREAD, osrm_distance_km=4.8)
    h = harness([route], OSRMError("boom"))

    h.run("--force")

    assert route.osrm_distance_km == 4.8, "a failed recompute must not blank the value"
    assert h.invalidated == []


def test_route_with_fewer_than_two_stops_is_skipped(harness):
    route = _Route("R1", [_Stop(27.700, 85.300)])
    h = harness([route], {"distance_m": 5000.0})

    h.run()

    assert h.calls == [], "OSRM should not be called for a single-stop route"
    assert route.osrm_distance_km is None
    assert h.invalidated == []


# --- invalid measurements --------------------------------------------------

@pytest.mark.parametrize(
    "geometry",
    [
        {"distance_m": 0},
        {"distance_m": 0.0},
        {"distance_m": -12.0},
        {"distance_m": float("nan")},
        {"distance_m": float("inf")},
        {"distance_m": float("-inf")},
        {"distance_m": None},
        {"distance_m": "5000"},
        {"distance_m": True},
        {},
        "not-a-dict",
        None,
    ],
)
def test_unusable_measurement_is_not_written(harness, geometry):
    route = _Route("R1", _SPREAD, osrm_distance_km=None)
    h = harness([route], geometry)

    h.run()

    assert route.osrm_distance_km is None
    assert h.session.added == []
    assert h.invalidated == []


def test_unusable_measurement_under_force_keeps_existing_value(harness):
    route = _Route("R1", _SPREAD, osrm_distance_km=4.8)
    h = harness([route], {"distance_m": float("nan")})

    h.run("--force")

    assert route.osrm_distance_km == 4.8
    assert h.invalidated == []


# --- cache invalidation ----------------------------------------------------

def test_caches_invalidated_once_even_for_many_changed_routes(harness):
    routes = [_Route(f"R{i}", _SPREAD) for i in range(1, 6)]
    h = harness(routes, {"distance_m": 5000.0})

    h.run()

    assert h.invalidated == ["routes", "route_detail"], "one invalidation of each namespace"


def test_no_invalidation_when_nothing_is_pending(harness):
    routes = [_Route("R1", _SPREAD, osrm_distance_km=4.8)]
    h = harness(routes, {"distance_m": 5000.0})

    h.run()

    assert h.calls == []
    assert h.session.added == []
    assert h.invalidated == []


def test_failed_sibling_does_not_add_extra_invalidations(harness):
    other_coords = [_Stop(27.800, 85.400), _Stop(27.803, 85.403), _Stop(27.806, 85.406)]
    changing = _Route("R1", _SPREAD, osrm_distance_km=None)
    failing = _Route("R2", other_coords, osrm_distance_km=None)

    def geometry(coords):
        if coords[0] == (27.700, 85.300):  # R1 succeeds
            return {"distance_m": 5000.0}
        raise OSRMError("boom")  # R2 fails and is skipped

    h = harness([changing, failing], geometry)

    h.run()

    assert changing.osrm_distance_km == 5.0
    assert failing.osrm_distance_km is None, "the failed route must stay unwritten"
    assert h.invalidated == ["routes", "route_detail"], "one pair regardless of route count"


# --- per-batch invalidation after commits ----------------------------------

def _null_routes(n):
    return [_Route(f"R{i:02d}", _SPREAD, osrm_distance_km=None) for i in range(1, n + 1)]


def test_batch_of_twenty_commits_and_invalidates_before_the_final_batch(harness):
    routes = _null_routes(21)
    h = harness(routes, {"distance_m": 5000.0})

    h.run()

    assert h.session.commits == 2, "one periodic batch plus the final partial batch"
    assert h.invalidated == ["routes", "route_detail"] * 2
    assert [r.osrm_distance_km for r in routes] == [5.0] * 21
    assert h.session.events == [
        ("commit", 1),
        ("invalidate", "routes"),
        ("invalidate", "route_detail"),
        ("commit", 2),
        ("invalidate", "routes"),
        ("invalidate", "route_detail"),
    ]


def test_first_batch_stays_invalidated_when_a_later_commit_fails(harness):
    routes = _null_routes(21)
    h = harness(routes, {"distance_m": 5000.0})
    h.session.fail_commit_number = 2

    with pytest.raises(RuntimeError, match="commit #2 failed"):
        h.run()

    assert h.session.commits == 2, "the second commit was attempted"
    assert h.invalidated == ["routes", "route_detail"], "only the committed batch invalidated"
    assert h.session.closed is True


def test_batch_with_no_changed_values_is_not_invalidated(harness):
    routes = [_Route(f"R{i:02d}", _SPREAD, osrm_distance_km=5.0) for i in range(1, 22)]
    h = harness(routes, {"distance_m": 5000.0})

    h.run("--force")

    assert h.session.commits == 2, "batches still commit even with no changes"
    assert h.invalidated == []


def test_final_partial_batch_is_committed_and_invalidated(harness):
    routes = _null_routes(25)
    h = harness(routes, {"distance_m": 5000.0})

    h.run()

    assert h.session.commits == 2
    assert h.invalidated == ["routes", "route_detail"] * 2
    assert [r.osrm_distance_km for r in routes] == [5.0] * 25
    assert h.session.events[-3:] == [
        ("commit", 2),
        ("invalidate", "routes"),
        ("invalidate", "route_detail"),
    ]


def test_each_changed_batch_invalidates_exactly_once(harness):
    routes = _null_routes(40)
    h = harness(routes, {"distance_m": 5000.0})

    h.run()

    # Two full changed batches, then a final commit with nothing left to
    # change -- which must not invalidate again.
    assert h.session.commits == 3
    assert h.invalidated == ["routes", "route_detail"] * 2
    assert h.session.events[-1] == ("commit", 3)
