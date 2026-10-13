"""
tests/test_route_finder_congestion_integration.py

End-to-end integration coverage for GET /route-finder's `avoid_congestion`
query flag, driven through FastAPI's real query parsing and the real
endpoint handler (TestClient -> find_route -> find_shortest_path ->
RouteFinderResult response_model).

Why this exists: tests/test_congestion_direct_vs_transfer.py proves the
congestion scoring inside find_shortest_path in isolation, and the frontend
tests prove the client *sends* the flag, but nothing proved the two ends
meet -- REVIEW.md recorded `avoid_congestion=True` as "no integration
proof". These tests close that gap without a live database.

No DB, Redis, OSRM or network is used:
  * the request's get_db dependency is overridden to yield None.
    graph_builder.get_cached_graph explicitly supports a None session when
    gb.get_active_routes is monkeypatched (see its docstring), which is the
    same trick tests/test_routing.py and test_congestion_direct_vs_transfer.py
    already use.
  * the routing graph is built from in-memory route doubles.
  * the four app.db.queries lookups the handler performs after the search
    (get_route_names, get_route_display_info, get_stop, fare_for_distance)
    are stubbed deterministically.
  * OSRM geometry is forced to raise, so the handler takes its documented
    straight-line fallback instead of calling out over the network.
  * the background congestion recorder opens its own SessionLocal; that
    factory is replaced with a no-op so the post-response task touches no
    database.

The endpoint handler itself is not patched: only external infrastructure
and nondeterministic data sources are overridden.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routing
from app.db import queries
from app.db.queries import RouteDisplayInfo
from app.db.session import get_db
from app.main import app
from app.routing import congestion_zones as cz
from app.routing import graph_builder as gb
from app.routing import pathfinder as pf
from app.routing.osrm_client import OSRMError


def make_stop(stop_id, name, lat, lng):
    return SimpleNamespace(
        stop_id=stop_id,
        stop_name=name,
        lat=lat,
        lng=lng,
        zone=None,
        district=None,
        aliases=None,
        ward=None,
        landmark=None,
        is_major_stop=False,
        is_interchange=False,
        status="active",
    )


def make_route(route_id, stops, bidirectional=True, status="active"):
    route_stops = [
        SimpleNamespace(stop_id=s.stop_id, sequence_no=i, stop=s)
        for i, s in enumerate(stops, start=1)
    ]
    return SimpleNamespace(
        route_id=route_id,
        route_stops=route_stops,
        is_bidirectional=bidirectional,
        status=status,
    )


class _NoopSession:
    """Stand-in for the background recorder's self-opened SessionLocal()."""

    def commit(self):
        pass

    def close(self):
        pass


def _display_info(network, route_ids):
    """The smallest honoured version of queries.get_route_display_info:
    operator + termini + stop sequence, derived from the in-memory routes."""
    info = {}
    for route in network["routes"]:
        if route.route_id not in route_ids:
            continue
        positions = {rs.stop_id: rs.sequence_no for rs in route.route_stops}
        first = min(positions, key=positions.get)
        last = max(positions, key=positions.get)
        info[route.route_id] = RouteDisplayInfo(
            operator_name="Test Operator",
            start_stop_id=first,
            start_stop_name=network["stops"][first].stop_name,
            end_stop_id=last,
            end_stop_name=network["stops"][last].stop_name,
            stop_positions=positions,
        )
    return info


@pytest.fixture(autouse=True)
def _reset_graph_cache():
    gb.invalidate_graph_cache()
    yield
    gb.invalidate_graph_cache()


@pytest.fixture
def direct_plus_transfer():
    """S1 -> S2 -> S3 direct on R_A, plus a real S2/S2x interchange to R_B
    (S2x ~6m from S2) that reaches S3 without the congested R_A S2->S3 leg.

    Same shape as tests/test_congestion_direct_vs_transfer.py's fixture:
    with R_A's S2->S3 leg congested, the transfer is cheaper under
    avoid_congestion=True, while R_A wins by raw distance by default."""
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    s3 = make_stop("S3", "Far Bazaar", 27.6300, 85.3050)
    route_a = make_route("R_A", [s1, s2, s3])

    s2x = make_stop("S2x", "Sundhara West", 27.70102, 85.31302)
    route_b = make_route("R_B", [s2x, s3])

    return {
        "routes": [route_a, route_b],
        "stops": {s.stop_id: s for s in (s1, s2, s3, s2x)},
    }


@pytest.fixture
def direct_only():
    """A direct route that is the only physical path (no transfer exists)."""
    s1 = make_stop("S1", "Ratnapark", 27.7050, 85.3145)
    s2 = make_stop("S2", "Sundhara", 27.7010, 85.3130)
    s3 = make_stop("S3", "Far Bazaar", 27.6300, 85.3050)
    route_a = make_route("R_A", [s1, s2, s3])
    return {
        "routes": [route_a],
        "stops": {s.stop_id: s for s in (s1, s2, s3)},
    }


@pytest.fixture
def client_factory(monkeypatch):
    """Returns build(network, ratios) -> TestClient wired to an in-memory
    graph and deterministic congestion/DB data. Restores dependency
    overrides afterwards."""
    previous_overrides = dict(app.dependency_overrides)

    def build(network, ratios):
        monkeypatch.setattr(gb, "get_active_routes", lambda session: network["routes"])
        monkeypatch.setattr(cz, "load_zones", lambda: [])
        monkeypatch.setattr(
            pf,
            "get_congestion_ratios",
            lambda session, day_of_week, hour_bucket: dict(ratios),
        )
        monkeypatch.setattr(
            queries, "get_stop", lambda session, stop_id: network["stops"].get(stop_id)
        )
        monkeypatch.setattr(
            queries,
            "get_route_names",
            lambda session, ids: {rid: f"Line {rid}" for rid in ids},
        )
        monkeypatch.setattr(
            queries,
            "get_route_display_info",
            lambda session, ids: _display_info(network, ids),
        )
        monkeypatch.setattr(queries, "fare_for_distance", lambda session, km: None)
        monkeypatch.setattr(queries, "record_congestion_sample", lambda *a, **k: None)

        # OSRM is down: the handler must fall back, never reach the network.
        def _no_osrm(*args, **kwargs):
            raise OSRMError("OSRM disabled in tests")

        monkeypatch.setattr(routing, "get_route_geometry", _no_osrm)
        monkeypatch.setattr(routing, "SessionLocal", lambda: _NoopSession())

        app.dependency_overrides[get_db] = lambda: None
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


CONGESTED_RATIOS = {
    ("R_A", "S1", "S2"): 1.0,
    ("R_A", "S2", "S3"): 3.0,
    ("R_B", "S2x", "S3"): 1.0,
}


# --- 1. omitting the flag keeps the documented default (false) ------------


def test_omitting_avoid_congestion_keeps_direct_route(client_factory, direct_plus_transfer):
    """No `avoid_congestion` in the query string -> default false. Even
    with congestion data that would flip the choice, the direct route wins
    by raw distance and no transfer is introduced."""
    client = client_factory(direct_plus_transfer, CONGESTED_RATIOS)

    resp = client.get("/route-finder", params={"origin": "S1", "destination": "S3"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["transfer_count"] == 0
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]


def test_explicit_false_matches_omitted_default(client_factory, direct_plus_transfer):
    """The query parser must read avoid_congestion=false as false (not as
    truthy "a non-empty string"), matching the omitted-parameter behavior."""
    client = client_factory(direct_plus_transfer, CONGESTED_RATIOS)

    resp = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "avoid_congestion": "false"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["transfer_count"] == 0
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]


# --- 2 & 3. the flag reaches congestion-aware routing and can pick the
#            transfer over the otherwise-preferred direct route ----------


def test_avoid_congestion_true_prefers_the_transfer(client_factory, direct_plus_transfer):
    """Identical graph and congestion data as the default test above, but
    avoid_congestion=true: the congested R_A S2->S3 leg must lose to the
    S2/S2x walk transfer onto R_B. This is the end-to-end proof that the
    HTTP query parameter actually reaches find_shortest_path's
    congestion-aware scoring."""
    client = client_factory(direct_plus_transfer, CONGESTED_RATIOS)

    resp = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "avoid_congestion": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["transfer_count"] == 1
    route_ids = [leg["route_id"] for leg in body["legs"]]
    assert route_ids == ["R_A", "TRANSFER", "R_B"]

    transfer_leg = body["legs"][1]
    assert transfer_leg["route_name"] == "Transfer (walk)"
    assert transfer_leg["board_stop"]["stop_id"] == "S2"
    assert transfer_leg["alight_stop"]["stop_id"] == "S2x"


# --- 4. the response is a valid RouteFinderResult -------------------------


def test_response_is_schema_valid(client_factory, direct_plus_transfer):
    """The handler returns through its real response_model, so a 200 is
    itself validation; assert the documented shape is present so a future
    schema drift that still validates (e.g. dropping a defaulted field)
    is caught here too."""
    client = client_factory(direct_plus_transfer, CONGESTED_RATIOS)

    resp = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "avoid_congestion": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["origin_stop_id"] == "S1"
    assert body["destination_stop_id"] == "S3"
    assert body["via_stop_ids"] == []
    assert body["alternatives"] == []
    assert body["total_cost"] > 0
    assert body["total_estimated_duration_s"] > 0

    for leg in body["legs"]:
        assert leg["board_stop"]["stop_id"]
        assert isinstance(leg["board_stop"]["lat"], float)
        assert isinstance(leg["board_stop"]["lng"], float)
        assert leg["num_ride_segments"] >= 1
        assert leg["estimated_duration_s"] > 0


# --- 5. failures / missing optional data never hide a viable direct route -


def test_avoid_congestion_with_no_congestion_data_keeps_direct(
    client_factory, direct_plus_transfer
):
    """avoid_congestion=true but the congestion source returns nothing:
    missing data must degrade to free flow, not delete the direct route."""
    client = client_factory(direct_plus_transfer, ratios={})

    resp = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "avoid_congestion": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["transfer_count"] == 0
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]


def test_transfer_search_failure_keeps_a_viable_direct_route(client_factory, direct_only):
    """avoid_congestion=true on a network where no transfer exists: the
    Dijkstra transfer search raises NoRouteFoundError, and the handler must
    still return the viable direct route (pathfinder's direct-fallback
    branch) rather than a 404."""
    client = client_factory(direct_only, ratios={})

    resp = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "avoid_congestion": "true"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["transfer_count"] == 0
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]
