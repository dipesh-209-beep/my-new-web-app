"""
tests/test_route_finder_distance_policy.py

Regression coverage for GET /route-finder's *total distance policy* -- the
shared rule that builds `total_cost` for the primary result and every
alternative (see app/api/routing.py:_total_distance_with_source).

Before this, the primary total preferred the summed OSRM road distance
whenever all its legs had geometry, while each alternative always used
`alt.total_distance_m` -- the routing graph's straight-line (haversine)
edge-weight sum. A response could therefore hand back an OSRM total for the
primary and a graph total for an alternative, two numbers the caller had no
way to tell apart. Now both go through one helper and `total_distance_source`
says which basis was used.

Driven end-to-end through the real endpoint (TestClient -> find_route ->
find_shortest_path -> RouteFinderResult response_model). No DB, Redis, OSRM
or network: the graph is built from in-memory route doubles, the DB lookups
the handler performs after the search are stubbed, and `routing.get_route_geometry`
is replaced with per-test fakes so geometry can be present, absent, or fail
on demand.
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
from app.routing.constants import BOARD_WAIT_S, BUS_AVG_SPEED_MPS, WALK_SPEED_MPS
from app.routing.graph_builder import haversine_distance_m
from app.routing.osrm_client import OSRMError

# The fake road distance is this many times the straight-line distance, so
# an OSRM total is unmistakably different from the graph fallback and the
# two can never be confused in an assertion.
OSRM_FACTOR = 10.0


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
    """Smallest honoured version of queries.get_route_display_info."""
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
def client_factory(monkeypatch):
    """build(network, osrm) -> TestClient wired to an in-memory graph and a
    caller-supplied `routing.get_route_geometry` fake."""
    previous_overrides = dict(app.dependency_overrides)

    def build(network, osrm):
        monkeypatch.setattr(gb, "get_active_routes", lambda session: network["routes"])
        monkeypatch.setattr(cz, "load_zones", lambda: [])
        monkeypatch.setattr(
            pf, "get_congestion_ratios", lambda session, day_of_week, hour_bucket: {}
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
        monkeypatch.setattr(routing, "get_route_geometry", osrm)
        monkeypatch.setattr(routing, "SessionLocal", lambda: _NoopSession())

        app.dependency_overrides[get_db] = lambda: None
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


# --- stops and networks ---------------------------------------------------
# All stops in the direct network sit well over the 200 m interchange radius
# apart, so the graph has no walking edges and the transfer search finds no
# path -- leaving only the two genuinely different direct routes.

S1 = make_stop("S1", "Start", 27.7000, 85.3000)
S2 = make_stop("S2", "MidA", 27.7200, 85.3000)
S3 = make_stop("S3", "End", 27.7400, 85.3000)
S4 = make_stop("S4", "MidB", 27.7150, 85.3400)

# Transfer network: R_A stops at S2, R_B starts at a nearby-but-distinct S2x
# (~100 m away, inside the 200 m interchange radius), so the only path from
# S1 to S3 is ride R_A -> walk S2->S2x -> ride R_B: a real three-leg trip.
S2X = make_stop("S2x", "MidA2", 27.7200, 85.3010)


def direct_network():
    return {
        "routes": [
            make_route("R_A", [S1, S2, S3]),
            make_route("R_B", [S1, S4, S3]),
        ],
        "stops": {s.stop_id: s for s in (S1, S2, S3, S4)},
    }


def transfer_network():
    return {
        "routes": [
            make_route("R_A", [S1, S2]),
            make_route("R_B", [S2X, S3]),
        ],
        "stops": {s.stop_id: s for s in (S1, S2, S2X, S3)},
    }


def _graph_distance(stops):
    """The routing graph's straight-line (haversine) distance for a leg that
    visits `stops` in order -- what the fallback should equal."""
    return sum(
        haversine_distance_m(a.lat, a.lng, b.lat, b.lng)
        for a, b in zip(stops, stops[1:])
    )


EXPECTED_GRAPH_A = _graph_distance([S1, S2, S3])
EXPECTED_GRAPH_B = _graph_distance([S1, S4, S3])
EXPECTED_TRANSFER_GRAPH = (
    _graph_distance([S1, S2])
    + _graph_distance([S2, S2X])
    + _graph_distance([S2X, S3])
)


# --- OSRM fakes -----------------------------------------------------------


def osrm_all(coords, profile="driving", bearings=None, radiuses=None):
    """Every leg gets a road distance OSRM_FACTOR times its straight-line
    sum, so the OSRM total is a distinct, predictable figure."""
    straight = sum(
        haversine_distance_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
        for i in range(len(coords) - 1)
    )
    return {
        "geometry": {
            "type": "LineString",
            "coordinates": [[lng, lat] for lat, lng in coords],
        },
        "distance_m": OSRM_FACTOR * straight,
        "duration_s": 60.0,
    }


def osrm_missing_midA(coords, profile="driving", bearings=None, radiuses=None):
    """Fail only a leg that passes through S2 (R_A's), so R_A falls back to
    the graph while R_B keeps its real OSRM geometry."""
    near_mid_a = any(
        abs(lat - S2.lat) < 1e-4 and abs(lng - S2.lng) < 1e-4 for lat, lng in coords
    )
    if near_mid_a:
        raise OSRMError("no matchable road near MidA")
    return osrm_all(coords, profile=profile, bearings=bearings, radiuses=radiuses)


def osrm_down(coords, profile="driving", bearings=None, radiuses=None):
    """Whole-call OSRM failure -- every leg keeps road_geometry None."""
    raise OSRMError("OSRM disabled in tests")


# --- 1. every leg has geometry: both primary and alternative use OSRM -----


def test_primary_and_alternative_both_use_osrm_when_all_legs_have_geometry(
    client_factory,
):
    client = client_factory(direct_network(), osrm_all)

    body = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "include_alternatives": "true"},
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]
    assert body["total_distance_source"] == "osrm"
    assert body["total_cost"] == pytest.approx(OSRM_FACTOR * EXPECTED_GRAPH_A)

    assert len(body["alternatives"]) == 1
    alt = body["alternatives"][0]
    assert [leg["route_id"] for leg in alt["legs"]] == ["R_B"]
    assert alt["total_distance_source"] == "osrm"
    assert alt["total_cost"] == pytest.approx(OSRM_FACTOR * EXPECTED_GRAPH_B)


# --- 2. OSRM unavailable -> both fall back to the graph distance ----------


def test_primary_and_alternative_share_graph_fallback_when_osrm_unavailable(
    client_factory,
):
    client = client_factory(direct_network(), osrm_down)

    body = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "include_alternatives": "true"},
    ).json()

    assert body["total_distance_source"] == "graph"
    assert body["total_cost"] == pytest.approx(EXPECTED_GRAPH_A, rel=1e-6)

    assert len(body["alternatives"]) == 1
    alt = body["alternatives"][0]
    assert alt["total_distance_source"] == "graph"
    assert alt["total_cost"] == pytest.approx(EXPECTED_GRAPH_B, rel=1e-6)


# --- 3. one leg missing on the primary only -> only that path falls back --


def test_one_missing_leg_falls_back_without_changing_the_other_path(
    client_factory,
):
    client = client_factory(direct_network(), osrm_missing_midA)

    body = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "include_alternatives": "true"},
    ).json()

    # R_A's single leg passes through S2 and got no geometry -> graph fallback.
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]
    assert body["total_distance_source"] == "graph"
    assert body["total_cost"] == pytest.approx(EXPECTED_GRAPH_A, rel=1e-6)

    # R_B is untouched and still reports its real OSRM road distance, on the
    # same policy (all-or-nothing per path), just with complete data.
    alt = body["alternatives"][0]
    assert [leg["route_id"] for leg in alt["legs"]] == ["R_B"]
    assert alt["total_distance_source"] == "osrm"
    assert alt["total_cost"] == pytest.approx(OSRM_FACTOR * EXPECTED_GRAPH_B)


# --- 4. multi-leg trip: total sums every leg, in metres -------------------


def test_multi_leg_transfer_total_sums_all_legs_in_metres(client_factory):
    client = client_factory(transfer_network(), osrm_all)

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_A", "TRANSFER", "R_B"]
    assert body["total_distance_source"] == "osrm"
    # The total is every leg's road_geometry.distance_m summed, in metres --
    # not a km figure and not just the endpoints' straight-line distance.
    assert body["total_cost"] == pytest.approx(OSRM_FACTOR * EXPECTED_TRANSFER_GRAPH)
    assert body["total_cost"] > 1000


def test_multi_leg_transfer_falls_back_to_graph_distance_when_osrm_down(
    client_factory,
):
    client = client_factory(transfer_network(), osrm_down)

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_A", "TRANSFER", "R_B"]
    assert body["total_distance_source"] == "graph"
    assert body["total_cost"] == pytest.approx(EXPECTED_TRANSFER_GRAPH, rel=1e-6)


# --- fare and duration follow the same path-wide source -------------------


def _record_fares(monkeypatch):
    """Capture the km figure each fare lookup is asked for. The dummy fare
    rule itself is irrelevant here -- the distance handed to it is what
    proves which basis the fare was computed on."""
    seen: list[float] = []

    def record(session, km):
        seen.append(km)
        return None

    monkeypatch.setattr(queries, "fare_for_distance", record)
    return seen


def test_fare_and_duration_use_osrm_when_the_path_is_fully_covered(
    client_factory, monkeypatch
):
    client = client_factory(direct_network(), osrm_all)
    fares = _record_fares(monkeypatch)

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]
    assert body["total_distance_source"] == "osrm"
    # Ride-only distance is the path's single OSRM leg, so the fare is asked
    # for exactly that (in km), and the duration is OSRM distance at bus
    # speed plus one boarding wait.
    assert fares == [pytest.approx(OSRM_FACTOR * EXPECTED_GRAPH_A / 1000)]
    assert body["total_estimated_duration_s"] == pytest.approx(
        OSRM_FACTOR * EXPECTED_GRAPH_A / BUS_AVG_SPEED_MPS + BOARD_WAIT_S
    )


def test_fare_and_duration_use_graph_when_osrm_is_unavailable(
    client_factory, monkeypatch
):
    client = client_factory(direct_network(), osrm_down)
    fares = _record_fares(monkeypatch)

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    assert body["total_distance_source"] == "graph"
    assert fares == [pytest.approx(EXPECTED_GRAPH_A / 1000)]
    assert body["total_estimated_duration_s"] == pytest.approx(
        EXPECTED_GRAPH_A / BUS_AVG_SPEED_MPS + BOARD_WAIT_S
    )


def test_mixed_coverage_falls_the_whole_path_to_graph_for_fare_and_duration(
    client_factory, monkeypatch
):
    """Only R_B's leg can be routed by OSRM here; R_A's and the transfer
    walk's cannot. The path is all-or-nothing, so even R_B's real geometry
    must not leak into the fare or the duration -- both use the graph basis,
    matching total_distance_source."""
    client = client_factory(transfer_network(), osrm_missing_midA)
    fares = _record_fares(monkeypatch)

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_A", "TRANSFER", "R_B"]
    assert body["total_distance_source"] == "graph"

    ride_graph = _graph_distance([S1, S2]) + _graph_distance([S2X, S3])
    walk_graph = _graph_distance([S2, S2X])
    # Fare is charged per ride leg, not once over the summed distance: one
    # lookup per bus, each on that leg's own graph distance, walk excluded.
    assert fares == [
        pytest.approx(_graph_distance([S1, S2]) / 1000),
        pytest.approx(_graph_distance([S2X, S3]) / 1000),
    ]
    assert body["total_estimated_duration_s"] == pytest.approx(
        _graph_distance([S1, S2]) / BUS_AVG_SPEED_MPS
        + walk_graph / WALK_SPEED_MPS
        + _graph_distance([S2X, S3]) / BUS_AVG_SPEED_MPS
        + 2 * BOARD_WAIT_S
    )


# --- multi-hop, NON-COLLINEAR graph legs reconcile with the path ----------
#
# The fixtures above are collinear (S1, S2, S3 share a longitude), so a
# merged multi-hop leg's edge sum and its board->alight chord happen to be
# equal and could not tell a per-edge leg distance apart from a per-chord
# one. These stops bend the path so edge_sum > direct_chord by ~25%, the
# case where a chord-based leg distance understates the leg and disagrees
# with the path total even though both are labelled "graph".

BN1 = make_stop("BN1", "BentStart", 27.7000, 85.3000)
BN2 = make_stop("BN2", "BentMid", 27.7200, 85.3200)  # off the BN1->BN3 line
BN3 = make_stop("BN3", "BentEnd", 27.7400, 85.3000)

BTM1 = make_stop("BTM1", "TStart", 27.7000, 85.3000)
BTMM = make_stop("BTMM", "TMid", 27.7200, 85.3200)  # bends R_TA's leg
BTM2 = make_stop("BTM2", "TAlight", 27.7400, 85.3000)
# ~100 m from BTM2 (inside the 200 m interchange radius), on R_TB.
BTM2X = make_stop("BTM2X", "TBoard", 27.7400, 85.3010)
BTM3 = make_stop("BTM3", "TEnd", 27.7600, 85.2800)


def bent_direct_network():
    """One direct route whose middle stop sits off the start->end line, so
    the graph path has two edges and a strictly larger edge sum."""
    return {
        "routes": [make_route("R_N", [BN1, BN2, BN3])],
        "stops": {s.stop_id: s for s in (BN1, BN2, BN3)},
    }


def bent_transfer_network():
    """R_TA (bent, two hops) -> ~100 m walk -> R_TB. Used with an OSRM fake
    that fails only R_TA, so the whole path falls back to "graph" while
    R_TB still carries real OSRM geometry that must be ignored."""
    return {
        "routes": [
            make_route("R_TA", [BTM1, BTMM, BTM2]),
            make_route("R_TB", [BTM2X, BTM3]),
        ],
        "stops": {s.stop_id: s for s in (BTM1, BTMM, BTM2, BTM2X, BTM3)},
    }


def osrm_missing_bent_mid(coords, profile="driving", bearings=None, radiuses=None):
    """Fail only R_TA's leg (the one that passes through BTMM), so it keeps
    road_geometry None while the other legs get real OSRM geometry."""
    near_bent_mid = any(
        abs(lat - BTMM.lat) < 1e-4 and abs(lng - BTMM.lng) < 1e-4 for lat, lng in coords
    )
    if near_bent_mid:
        raise OSRMError("no matchable road near TMid")
    return osrm_all(coords, profile=profile, bearings=bearings, radiuses=radiuses)


def test_non_collinear_multihop_graph_leg_reconciles_with_path_total(
    client_factory, monkeypatch
):
    client = client_factory(bent_direct_network(), osrm_down)
    fares = _record_fares(monkeypatch)

    body = client.get(
        "/route-finder", params={"origin": "BN1", "destination": "BN3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_N"]
    assert body["total_distance_source"] == "graph"

    leg = body["legs"][0]
    assert leg["num_ride_segments"] == 2
    assert len(leg["stops"]) == 3

    edges = _graph_distance([BN1, BN2, BN3])
    chord = haversine_distance_m(BN1.lat, BN1.lng, BN3.lat, BN3.lng)
    # The fixture is genuinely non-collinear: the per-edge sum is bigger
    # than the straight board->alight chord, so the two leg measures can't
    # be confused.
    assert edges > chord * 1.05

    # The graph total is the full per-edge sum...
    assert body["total_cost"] == pytest.approx(edges, rel=1e-6)
    # ...and the ride-only fare distance is that same sum for the ride leg,
    # NOT the shorter chord the old code used for a graph leg.
    assert fares == [pytest.approx(edges / 1000)]
    # Duration uses the graph leg distance at bus speed, plus one wait.
    assert leg["estimated_duration_s"] == pytest.approx(edges / BUS_AVG_SPEED_MPS)
    assert body["total_estimated_duration_s"] == pytest.approx(
        edges / BUS_AVG_SPEED_MPS + BOARD_WAIT_S
    )


def test_non_collinear_graph_falls_back_on_all_ride_legs_and_excludes_walk(
    client_factory, monkeypatch
):
    client = client_factory(bent_transfer_network(), osrm_missing_bent_mid)
    fares = _record_fares(monkeypatch)

    body = client.get(
        "/route-finder", params={"origin": "BTM1", "destination": "BTM3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_TA", "TRANSFER", "R_TB"]
    assert body["total_distance_source"] == "graph"

    ra_edges = _graph_distance([BTM1, BTMM, BTM2])
    walk_edges = _graph_distance([BTM2, BTM2X])
    rb_edges = _graph_distance([BTM2X, BTM3])

    # Graph total sums every edge on the path, including the walk.
    assert body["total_cost"] == pytest.approx(ra_edges + walk_edges + rb_edges, rel=1e-6)
    # Fare is charged once per ride leg (both of them, R_TA's merged two hops
    # included) on each leg's own graph distance, and excludes the walk --
    # even though R_TB and the walk leg DO carry OSRM geometry here, the path
    # is "graph", so that geometry must not be substituted in.
    assert fares == [
        pytest.approx(ra_edges / 1000),
        pytest.approx(rb_edges / 1000),
    ]
    assert body["legs"][0]["estimated_duration_s"] == pytest.approx(
        ra_edges / BUS_AVG_SPEED_MPS
    )
    assert body["total_estimated_duration_s"] == pytest.approx(
        ra_edges / BUS_AVG_SPEED_MPS
        + walk_edges / WALK_SPEED_MPS
        + rb_edges / BUS_AVG_SPEED_MPS
        + 2 * BOARD_WAIT_S
    )


def test_non_collinear_all_osrm_path_still_uses_the_osrm_basis(
    client_factory, monkeypatch
):
    """The fix only touches the "graph" branch: a fully OSRM-covered bent
    path keeps summing the real road distances exactly as before."""
    client = client_factory(bent_direct_network(), osrm_all)
    fares = _record_fares(monkeypatch)

    body = client.get(
        "/route-finder", params={"origin": "BN1", "destination": "BN3"}
    ).json()

    assert body["total_distance_source"] == "osrm"
    edges = _graph_distance([BN1, BN2, BN3])
    assert body["total_cost"] == pytest.approx(OSRM_FACTOR * edges)
    assert fares == [pytest.approx(OSRM_FACTOR * edges / 1000)]
    assert body["total_estimated_duration_s"] == pytest.approx(
        OSRM_FACTOR * edges / BUS_AVG_SPEED_MPS + BOARD_WAIT_S
    )


# --- fare is summed once per ride leg, NOT once over the whole path --------
#
# The bug these lock down: the fare used to be one band looked up on the
# summed ride distance, so a two-bus 4 km + 4 km trip was charged the single
# 8 km band (25-30) instead of fare(4 km) + fare(4 km) = 40-50. The rule is
# fare(bus 1) + fare(bus 2) + ... -- charged once per bus boarded.

# A linear three-bus chain with two ~100 m walking interchanges, so a single
# S->E trip really boards three buses and must pay three fares.
P1 = make_stop("P1", "PStart", 27.7000, 85.3000)
P2 = make_stop("P2", "PMidA", 27.7200, 85.3000)
P2X = make_stop("P2X", "PMidA2", 27.7200, 85.3010)  # ~100 m from P2
P3 = make_stop("P3", "PMidB", 27.7800, 85.3000)
P3X = make_stop("P3X", "PMidB2", 27.7800, 85.3010)  # ~100 m from P3
P4 = make_stop("P4", "PEnd", 27.9000, 85.3000)


def two_transfer_network():
    return {
        "routes": [
            make_route("R_P", [P1, P2]),
            make_route("R_Q", [P2X, P3]),
            make_route("R_R", [P3X, P4]),
        ],
        "stops": {s.stop_id: s for s in (P1, P2, P2X, P3, P3X, P4)},
    }


def _fare_band(npr_min, npr_max, fare_id="FX", note="PENDING official gazette"):
    """Smallest honoured fare_rules row for FareOut.model_validate."""
    return SimpleNamespace(
        fare_id=fare_id,
        min_distance_km=0,
        max_distance_km=99,
        fare_npr_min=npr_min,
        fare_npr_max=npr_max,
        student_discount_pct=None,
        verification_note=note,
    )


def _banded_fare(seen, missing_above_km=None):
    """fare_for_distance stub: records each lookup's km and returns the desk
    band by distance (F01 <5, F02 <10, F03 <15, F04 else), or None when
    `missing_above_km` is set and the leg is at/above it."""
    def stub(session, km):
        seen.append(km)
        if missing_above_km is not None and km >= missing_above_km:
            return None
        if km < 5:
            return _fare_band(20, 25, "F01")
        if km < 10:
            return _fare_band(25, 30, "F02")
        if km < 15:
            return _fare_band(30, 35, "F03")
        return _fare_band(35, 45, "F04")
    return stub


def test_direct_journey_makes_one_fare_lookup_and_keeps_the_band(
    client_factory, monkeypatch
):
    client = client_factory(direct_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    assert seen == [pytest.approx(EXPECTED_GRAPH_A / 1000)]
    fare = body["fare"]
    assert fare["ride_leg_count"] == 1
    assert fare["priced_leg_count"] == 1
    assert fare["complete"] is True
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((20, 25))
    assert "PENDING" in fare["verification_note"]


def test_one_transfer_sums_both_leg_fares(client_factory, monkeypatch):
    client = client_factory(transfer_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder", params={"origin": "S1", "destination": "S3"}
    ).json()

    ra_km = _graph_distance([S1, S2]) / 1000
    rb_km = _graph_distance([S2X, S3]) / 1000
    assert seen == [pytest.approx(ra_km), pytest.approx(rb_km)]

    fare = body["fare"]
    # Two legs each in the 0-5 km band: 20-25 + 20-25 = 40-50, NOT the
    # single ~4.5-9 km band the old summed-distance code would have used.
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((40, 50))
    assert fare["ride_leg_count"] == 2
    assert fare["priced_leg_count"] == 2
    assert fare["complete"] is True

    # Each ride leg carries its own band; the walk leg carries none.
    per_leg = {leg["route_id"]: leg["fare"] for leg in body["legs"]}
    assert per_leg["TRANSFER"] is None
    assert per_leg["R_A"]["fare_npr_min"] == pytest.approx(20)
    assert per_leg["R_B"]["fare_npr_min"] == pytest.approx(20)


def test_two_transfers_sum_three_leg_fares_across_bands(
    client_factory, monkeypatch
):
    client = client_factory(two_transfer_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder", params={"origin": "P1", "destination": "P4"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == [
        "R_P",
        "TRANSFER",
        "R_Q",
        "TRANSFER",
        "R_R",
    ]
    p_km = _graph_distance([P1, P2]) / 1000
    q_km = _graph_distance([P2X, P3]) / 1000
    r_km = _graph_distance([P3X, P4]) / 1000
    assert seen == [pytest.approx(p_km), pytest.approx(q_km), pytest.approx(r_km)]

    # F01 + F02 + F03 = (20+25+30) - (25+30+35).
    fare = body["fare"]
    assert fare["ride_leg_count"] == 3
    assert fare["priced_leg_count"] == 3
    assert fare["complete"] is True
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx(
        (75, 90)
    )


def test_many_stops_on_one_bus_charge_one_fare(client_factory, monkeypatch):
    """R_N visits BN1->BN2->BN3 without changing buses: a single merged leg,
    so one fare lookup and one band -- not one per stop passed."""
    client = client_factory(bent_direct_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder", params={"origin": "BN1", "destination": "BN3"}
    ).json()

    assert [leg["route_id"] for leg in body["legs"]] == ["R_N"]
    assert body["legs"][0]["num_ride_segments"] == 2
    assert len(seen) == 1
    fare = body["fare"]
    assert fare["ride_leg_count"] == 1
    assert fare["priced_leg_count"] == 1
    assert fare["complete"] is True


def test_missing_band_on_a_leg_marks_the_total_incomplete(
    client_factory, monkeypatch
):
    client = client_factory(two_transfer_network(), osrm_down)
    seen = []
    # Only the first (~2 km) leg is priceable; the 6.7 km and 13 km legs have
    # no band. The total must not be silently presented as the full fare.
    monkeypatch.setattr(
        queries, "fare_for_distance", _banded_fare(seen, missing_above_km=5)
    )

    body = client.get(
        "/route-finder", params={"origin": "P1", "destination": "P4"}
    ).json()

    fare = body["fare"]
    assert fare is not None
    assert fare["complete"] is False
    assert fare["ride_leg_count"] == 3
    assert fare["priced_leg_count"] == 1
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((20, 25))

    per_leg = [leg["fare"] for leg in body["legs"] if leg["route_id"] != "TRANSFER"]
    assert per_leg[0] is not None
    assert per_leg[1] is None
    assert per_leg[2] is None


def test_no_matching_band_yields_null_fare_not_zero(client_factory, monkeypatch):
    client = client_factory(two_transfer_network(), osrm_down)
    monkeypatch.setattr(queries, "fare_for_distance", lambda session, km: None)

    body = client.get(
        "/route-finder", params={"origin": "P1", "destination": "P4"}
    ).json()

    # A completely unavailable fare is null, never a zero total.
    assert body["fare"] is None
    assert all(leg["fare"] is None for leg in body["legs"])


def test_each_alternative_gets_its_own_summed_fare(client_factory, monkeypatch):
    client = client_factory(direct_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "include_alternatives": "true"},
    ).json()

    # R_A (~4.4 km) is the primary, in the 0-5 band.
    assert (body["fare"]["fare_npr_min"], body["fare"]["fare_npr_max"]) == (
        pytest.approx(20),
        pytest.approx(25),
    )
    assert body["fare"]["complete"] is True

    assert len(body["alternatives"]) == 1
    alt = body["alternatives"][0]
    # R_B is a longer single leg (~9 km) -> the 5-10 band, its own aggregate.
    assert alt["fare"]["ride_leg_count"] == 1
    assert alt["fare"]["complete"] is True
    assert (alt["fare"]["fare_npr_min"], alt["fare"]["fare_npr_max"]) == (
        pytest.approx(25),
        pytest.approx(30),
    )


# --- via waypoints are required stops, not forced bus changes --------------
#
# find_route_via_stops used to add a transfer for every waypoint boundary
# unconditionally, while _build_legs merges consecutive same-route segments.
# A trip that rode one bus straight through the via came back as
# transfer_count=1 beside a single merged leg, one fare and one boarding
# wait -- the UI showed "1 transfer" and "1 bus" at once. These lock in the
# corrected contract: a waypoint costs a transfer only when the route
# actually changes there.


def test_via_on_one_continuous_bus_is_not_a_transfer(client_factory, monkeypatch):
    # R_A visits S1 -> S2 -> S3 without changing buses. Routing S1->S2 and
    # S2->S3 separately and stitching them is still one uninterrupted ride.
    client = client_factory(direct_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "via": ["S2"]},
    ).json()

    assert body["via_stop_ids"] == ["S2"]
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A"]
    assert body["legs"][0]["num_ride_segments"] == 2
    assert body["transfer_count"] == 0

    # One bus boarded -> one fare, one boarding wait, all agreeing with
    # transfer_count=0 instead of contradicting it.
    assert seen == [pytest.approx(EXPECTED_GRAPH_A / 1000)]
    fare = body["fare"]
    assert fare["ride_leg_count"] == 1
    assert fare["priced_leg_count"] == 1
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((20, 25))
    assert body["total_estimated_duration_s"] == pytest.approx(
        EXPECTED_GRAPH_A / BUS_AVG_SPEED_MPS + BOARD_WAIT_S
    )


def test_via_where_the_route_changes_still_counts_a_transfer(
    client_factory, monkeypatch
):
    # No single route covers S1->S2->S3, so the trip rides R_A to S2, walks
    # to the neighbouring S2x and boards R_B: a genuine transfer at the via.
    client = client_factory(transfer_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder",
        params={"origin": "S1", "destination": "S3", "via": ["S2"]},
    ).json()

    ride_legs = [leg for leg in body["legs"] if leg["route_id"] != "TRANSFER"]
    assert [leg["route_id"] for leg in body["legs"]] == ["R_A", "TRANSFER", "R_B"]
    assert body["transfer_count"] == len(ride_legs) - 1 == 1

    # Two buses boarded -> two fares summed, two boarding waits.
    assert seen == [
        pytest.approx(_graph_distance([S1, S2]) / 1000),
        pytest.approx(_graph_distance([S2X, S3]) / 1000),
    ]
    fare = body["fare"]
    assert fare["ride_leg_count"] == 2
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((40, 50))
    assert body["total_estimated_duration_s"] == pytest.approx(
        _graph_distance([S1, S2]) / BUS_AVG_SPEED_MPS
        + _graph_distance([S2, S2X]) / WALK_SPEED_MPS
        + _graph_distance([S2X, S3]) / BUS_AVG_SPEED_MPS
        + 2 * BOARD_WAIT_S
    )


# --- approach/egress walks at the ends of a via trip are not transfers -----
#
# A via leg can be a pure walk when the origin/via (or via/destination) are
# neighbouring stops within the interchange radius. Riding one bus after
# walking up to it (or walking away from it) is still a single boarding, so
# transfer_count must be 0 and agree with the single ride leg, the single
# fare and the single boarding wait.

HWS1 = make_stop("HWS1", "ApproachStart", 27.6000, 85.2000)
HWSFAR = make_stop("HWSFAR", "ApproachFar", 27.8000, 85.2000)
HWS2 = make_stop("HWS2", "ApproachVia", 27.6010, 85.2000)  # ~111 m from HWS1
HWS3 = make_stop("HWS3", "ApproachEnd", 27.6200, 85.2000)

TWS1 = make_stop("TWS1", "EgressStart", 27.6000, 85.2000)
TWS2 = make_stop("TWS2", "EgressVia", 27.6200, 85.2000)
TWS3 = make_stop("TWS3", "EgressEnd", 27.6210, 85.2000)  # ~111 m from TWS2
TWSFAR = make_stop("TWSFAR", "EgressFar", 27.8000, 85.2000)


def head_walk_network():
    return {
        "routes": [
            make_route("R_STUB_H", [HWS1, HWSFAR]),
            make_route("R_HA", [HWS2, HWS3]),
        ],
        "stops": {s.stop_id: s for s in (HWS1, HWSFAR, HWS2, HWS3)},
    }


def tail_walk_network():
    return {
        "routes": [
            make_route("R_TA", [TWS1, TWS2]),
            make_route("R_STUB_T", [TWS3, TWSFAR]),
        ],
        "stops": {s.stop_id: s for s in (TWS1, TWS2, TWS3, TWSFAR)},
    }


def test_via_head_walk_is_not_a_transfer(client_factory, monkeypatch):
    client = client_factory(head_walk_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder",
        params={"origin": "HWS1", "destination": "HWS3", "via": ["HWS2"]},
    ).json()

    walk_m = _graph_distance([HWS1, HWS2])
    ride_m = _graph_distance([HWS2, HWS3])
    assert [leg["route_id"] for leg in body["legs"]] == ["TRANSFER", "R_HA"]
    assert body["transfer_count"] == 0

    # One bus boarded -> one fare and one boarding wait, plus the approach
    # walk's time, all agreeing with transfer_count=0.
    assert seen == [pytest.approx(ride_m / 1000)]
    fare = body["fare"]
    assert fare["ride_leg_count"] == 1
    assert fare["priced_leg_count"] == 1
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((20, 25))
    assert body["total_estimated_duration_s"] == pytest.approx(
        walk_m / WALK_SPEED_MPS + ride_m / BUS_AVG_SPEED_MPS + BOARD_WAIT_S
    )


def test_via_tail_walk_is_not_a_transfer(client_factory, monkeypatch):
    client = client_factory(tail_walk_network(), osrm_down)
    seen = []
    monkeypatch.setattr(queries, "fare_for_distance", _banded_fare(seen))

    body = client.get(
        "/route-finder",
        params={"origin": "TWS1", "destination": "TWS3", "via": ["TWS2"]},
    ).json()

    ride_m = _graph_distance([TWS1, TWS2])
    walk_m = _graph_distance([TWS2, TWS3])
    assert [leg["route_id"] for leg in body["legs"]] == ["R_TA", "TRANSFER"]
    assert body["transfer_count"] == 0

    # One bus boarded -> one fare and one boarding wait, plus the egress
    # walk's time, all agreeing with transfer_count=0.
    assert seen == [pytest.approx(ride_m / 1000)]
    fare = body["fare"]
    assert fare["ride_leg_count"] == 1
    assert fare["priced_leg_count"] == 1
    assert (fare["fare_npr_min"], fare["fare_npr_max"]) == pytest.approx((20, 25))
    assert body["total_estimated_duration_s"] == pytest.approx(
        ride_m / BUS_AVG_SPEED_MPS + walk_m / WALK_SPEED_MPS + BOARD_WAIT_S
    )
