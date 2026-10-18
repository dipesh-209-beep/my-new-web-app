"""
tests/test_ranking.py

Unit tests for R2's transparent ranking policy (app/routing/ranking.py) and
its use inside find_shortest_path: direct and transfer candidates are scored
on ONE basis (distance plus TRANSFER_PENALTY per boarding, congestion only
when opted in) instead of a direct route winning by fiat, and the returned
result carries a machine-readable rank_reason.

Same lightweight monkeypatch approach as tests/test_routing.py -- no live
database is required.
"""

import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.routing import graph_builder as gb
from app.routing import congestion_zones as cz
from app.routing import pathfinder as pf
from app.routing import ranking
from app.routing.constants import CONGESTION_LAMBDA, TRANSFER_PENALTY
from app.routing.pathfinder import (
    NoRouteFoundError,
    PathSegment,
    RouteFinderResult,
    find_shortest_path,
)


def make_stop(stop_id, name, lat, lng):
    return SimpleNamespace(stop_id=stop_id, stop_name=name, lat=lat, lng=lng)


def make_route(route_id, stops, bidirectional=True, status="active", **extra):
    route_stops = [
        SimpleNamespace(stop_id=s.stop_id, sequence_no=i, stop=s)
        for i, s in enumerate(stops, start=1)
    ]
    return SimpleNamespace(
        route_id=route_id,
        route_stops=route_stops,
        is_bidirectional=bidirectional,
        status=status,
        **extra,
    )


@pytest.fixture(autouse=True)
def _reset_graph_cache():
    gb.invalidate_graph_cache()
    yield
    gb.invalidate_graph_cache()


@pytest.fixture
def detour_vs_short_transfer(monkeypatch):
    """A direct route that goes a long way around, versus a short transfer
    across an interchange:

      Route DIRECT: S1 -> D1 -> S3        (a ~20 km detour)
      Route A:      S1 -> S2
      Route B:      S2x -> S3             (S2x ~2 m from S2)

    The transfer costs ~2.2 km + two boardings; the direct costs ~20 km +
    one boarding. On the shared policy the transfer is far cheaper, so it
    must win by default (no congestion opt-in required).
    """
    s1 = make_stop("S1", "Start", 27.7000, 85.3100)
    d1 = make_stop("D1", "Far Detour", 27.8000, 85.3100)
    s3 = make_stop("S3", "End", 27.7200, 85.3100)
    route_direct = make_route("R_DIRECT", [s1, d1, s3])

    s2 = make_stop("S2", "Interchange", 27.7100, 85.3100)
    route_a = make_route("R_A", [s1, s2])
    s2x = make_stop("S2x", "Interchange West", 27.71002, 85.31002)
    route_b = make_route("R_B", [s2x, s3])

    monkeypatch.setattr(
        gb, "get_active_routes", lambda session: [route_direct, route_a, route_b]
    )
    return {"s1": s1, "s3": s3, "s2": s2, "s2x": s2x, "d1": d1}


@pytest.fixture
def congested_direct_vs_transfer(monkeypatch):
    """Direct S1->S2->S3 (~8.4 km) versus the S2/S2x transfer. At free flow
    the direct is cheaper (one boarding); 3x congestion on its final leg
    makes the transfer cheaper -- but only when the caller opts in."""
    s1 = make_stop("S1", "Start", 27.7050, 85.3145)
    s2 = make_stop("S2", "Mid", 27.7010, 85.3130)
    s3 = make_stop("S3", "Far", 27.6300, 85.3050)
    route_a = make_route("R_A", [s1, s2, s3])

    s2x = make_stop("S2x", "Mid West", 27.70102, 85.31302)
    route_b = make_route("R_B", [s2x, s3])

    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b])
    return {"s1": s1, "s2": s2, "s3": s3, "s2x": s2x}


@pytest.fixture
def transfer_only_with_alternatives(monkeypatch):
    """A transfer-only origin/destination (no single route connects S1 and
    S3) with TWO different transfer corridors:

      Corridor 1 (weight-optimal, 2 buses, ~3.8 km raw): S1->M1, walk,
        M1x->S3.
      Corridor 2 (3 buses, ~2.2 km raw, straight line): S1->N1, walk,
        N1x->N2, walk, N2x->S3.

    Corridor 1 wins the score (fewer boardings outweigh its longer raw
    distance), but corridor 2 is shorter and is surfaced as the
    'shortest_distance' alternative. This is the exact shape that used to
    produce a contradictory `only_option` alongside a non-empty
    alternatives list.
    """
    s1 = make_stop("S1", "Start", 27.7000, 85.3100)
    s3 = make_stop("S3", "End", 27.7200, 85.3100)

    m1 = make_stop("M1", "Detour", 27.7000, 85.3225)
    m1x = make_stop("M1x", "Detour West", 27.70002, 85.32252)
    route_a = make_route("R_A", [s1, m1])
    route_b = make_route("R_B", [m1x, s3])

    n1 = make_stop("N1", "Line 1", 27.7033, 85.3100)
    n1x = make_stop("N1x", "Line 1 West", 27.70332, 85.31002)
    n2 = make_stop("N2", "Line 2", 27.7133, 85.3100)
    n2x = make_stop("N2x", "Line 2 West", 27.71332, 85.31002)
    route_c = make_route("R_C", [s1, n1])
    route_e = make_route("R_E", [n1x, n2])
    route_f = make_route("R_F", [n2x, s3])

    monkeypatch.setattr(
        gb, "get_active_routes", lambda session: [route_a, route_b, route_c, route_e, route_f]
    )
    return {"s1": s1, "s3": s3, "m1": m1, "n1": n1, "n2": n2}


def patch_congestion(monkeypatch, ratios):
    monkeypatch.setattr(cz, "load_zones", lambda: [])
    monkeypatch.setattr(
        pf,
        "get_congestion_ratios",
        lambda session, day_of_week, hour_bucket: ratios,
    )


# --- 1. direct and transfer ranked together ---


def test_short_transfer_beats_long_direct_by_default(detour_vs_short_transfer):
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    assert result.stop_sequence == ["S1", "S2", "S2x", "S3"]
    assert result.transfer_count == 1
    # The winner won on distance parity, not on a direct-route fiat.
    assert result.rank_reason == ranking.SHORTEST_DISTANCE


def test_direct_still_wins_when_genuinely_cheaper(congested_direct_vs_transfer):
    """The default policy is not 'transfers win'. The direct route here is
    both shorter and boards fewer buses, so the alternative transfer is
    dominated -- the search never prefers it under any weighting, leaving
    the direct as the only viable candidate (hence only_option)."""
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    assert result.stop_sequence == ["S1", "S2", "S3"]
    assert result.transfer_count == 0
    assert result.rank_reason == ranking.ONLY_OPTION


# --- 2. determinism (and cache-hit stability) ---


def test_ranking_is_deterministic_across_repeated_calls(detour_vs_short_transfer):
    first = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", include_alternatives=True
    )
    # Second call reuses the cached graph (the "cache hit" path).
    second = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", include_alternatives=True
    )
    assert first.stop_sequence == second.stop_sequence
    assert first.rank_reason == second.rank_reason
    assert [alt.stop_sequence for alt in first.alternatives] == [
        alt.stop_sequence for alt in second.alternatives
    ]


# --- 3. transfer constraints still respected ---


def test_max_transfers_blocks_otherwise_winning_transfer(detour_vs_short_transfer):
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        max_transfers=0,
    )
    assert result.transfer_count == 0
    assert result.stop_sequence == ["S1", "D1", "S3"]


# --- 4 & 5. frequency / service hours / fare never influence ranking ---


def test_frequency_and_service_hours_do_not_change_ranking(monkeypatch):
    """Two identical networks, one with frequency/service-hour data attached
    to the routes and one without: the chosen path and the rank_reason must
    be identical, because the policy never reads those fields (unknown must
    never be treated as favourable)."""
    def build():
        s1 = make_stop("S1", "Start", 27.7000, 85.3100)
        d1 = make_stop("D1", "Far Detour", 27.8000, 85.3100)
        s3 = make_stop("S3", "End", 27.7200, 85.3100)
        s2 = make_stop("S2", "Interchange", 27.7100, 85.3100)
        s2x = make_stop("S2x", "Interchange West", 27.71002, 85.31002)
        return s1, d1, s3, s2, s2x

    s1, d1, s3, s2, s2x = build()
    plain_direct = make_route("R_DIRECT", [s1, d1, s3])
    plain_a = make_route("R_A", [s1, s2])
    plain_b = make_route("R_B", [s2x, s3])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [plain_direct, plain_a, plain_b])
    baseline = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")

    gb.invalidate_graph_cache()
    s1, d1, s3, s2, s2x = build()
    freq_direct = make_route("R_DIRECT", [s1, d1, s3], frequency_min=2, service_start_time="05:00")
    freq_a = make_route("R_A", [s1, s2], frequency_min=None)
    freq_b = make_route("R_B", [s2x, s3], frequency_min=90, service_start_time="06:00")
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [freq_direct, freq_a, freq_b])
    with_frequency = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")

    assert baseline.stop_sequence == with_frequency.stop_sequence
    assert baseline.rank_reason == with_frequency.rank_reason


def test_policy_score_reads_only_distance_and_boardings():
    """The policy's inputs are provably limited to distance, transfer_count
    and whether a ride exists -- fare/frequency/time cannot leak in because
    they are not readable from the scoring inputs."""
    params = inspect.signature(ranking.static_ranking_score).parameters
    assert list(params) == ["result"]

    def seg(route_id, weight):
        return PathSegment(
            from_stop_id="A", to_stop_id="B", route_id=route_id, is_transfer=route_id is None, weight=weight
        )

    direct = RouteFinderResult(
        segments=[seg("R1", 4000.0)], total_distance_m=4000.0, transfer_count=0, stop_sequence=["A", "B"]
    )
    assert ranking.boardings(direct) == 1
    assert ranking.static_ranking_score(direct) == 4000.0 + TRANSFER_PENALTY

    one_transfer = RouteFinderResult(
        segments=[seg("R1", 1000.0), seg(None, 50.0), seg("R2", 1000.0)],
        total_distance_m=2050.0,
        transfer_count=1,
        stop_sequence=["A", "B", "C", "D"],
    )
    assert ranking.boardings(one_transfer) == 2
    assert ranking.static_ranking_score(one_transfer) == 2050.0 + 2 * TRANSFER_PENALTY

    pure_walk = RouteFinderResult(
        segments=[seg(None, 120.0)], total_distance_m=120.0, transfer_count=0, stop_sequence=["A", "B"]
    )
    assert ranking.boardings(pure_walk) == 0
    assert ranking.static_ranking_score(pure_walk) == 120.0


def test_no_rank_reason_claims_time_or_fare():
    """The reason vocabulary must not imply a 'fastest'/'cheapest' claim the
    data cannot support."""
    for reason in ranking.RANK_REASONS:
        for banned in ("fast", "cheap", "fare", "frequent"):
            assert banned not in reason


def test_explain_primary_branches():
    def result(distance, transfers, has_ride=True):
        segs = [PathSegment("A", "B", "R1" if has_ride else None, not has_ride, distance)]
        return RouteFinderResult(
            segments=segs, total_distance_m=distance, transfer_count=transfers, stop_sequence=["A", "B"]
        )

    one_bus = result(5000.0, 0)  # boardings == 1
    two_bus = result(9000.0, 1)  # boardings == 2
    assert ranking.explain_primary(one_bus, None, congestion_aware=False, congestion_flipped=False) == ranking.ONLY_OPTION
    assert ranking.explain_primary(one_bus, result(9000.0, 0), congestion_aware=False, congestion_flipped=False) == ranking.SHORTEST_DISTANCE
    assert ranking.explain_primary(one_bus, two_bus, congestion_aware=False, congestion_flipped=False) == ranking.FEWEST_TRANSFERS
    assert ranking.explain_primary(one_bus, result(9000.0, 0), congestion_aware=True, congestion_flipped=True) == ranking.AVOIDS_CONGESTION

    identical = result(5000.0, 0)
    assert ranking.explain_primary(one_bus, identical, congestion_aware=False, congestion_flipped=False) == ranking.BEST_OVERALL


# --- 6. congestion affects ranking only when opted in ---


def test_congestion_only_reranks_when_enabled(congested_direct_vs_transfer, monkeypatch):
    # Default (no opt-in): direct is genuinely cheaper and wins.
    default = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    assert default.stop_sequence == ["S1", "S2", "S3"]
    assert default.transfer_count == 0

    # Opted in, with the direct's last leg 3x congested: the transfer wins.
    patch_congestion(
        monkeypatch,
        {
            ("R_A", "S1", "S2"): 1.0,
            ("R_A", "S2", "S3"): 3.0,
            ("R_B", "S2x", "S3"): 1.0,
        },
    )
    avoided = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", avoid_congestion=True
    )
    assert avoided.stop_sequence == ["S1", "S2", "S2x", "S3"]
    assert avoided.transfer_count == 1
    assert avoided.rank_reason == ranking.AVOIDS_CONGESTION


# --- 7. empty / incomplete data handled safely ---


def test_incomplete_congestion_data_does_not_crash_or_hide_options(congested_direct_vs_transfer, monkeypatch):
    patch_congestion(monkeypatch, {})  # no ratios at all
    result = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", avoid_congestion=True
    )
    assert result.stop_sequence == ["S1", "S2", "S3"]
    # No congestion data -> nothing flipped -> no congestion explanation.
    assert result.rank_reason != ranking.AVOIDS_CONGESTION


def test_same_stop_request_returns_no_rank_reason(detour_vs_short_transfer):
    result = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S1")
    assert result.stop_sequence == ["S1"]
    assert result.rank_reason is None


# --- 8. alternatives stay distinct and ordered ---


def test_losing_direct_route_surfaces_as_an_alternative(detour_vs_short_transfer):
    """When a transfer wins, the real direct route must still be offered --
    hidden only because it lost, not because it doesn't exist."""
    result = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", include_alternatives=True
    )
    assert result.rank_reason == ranking.SHORTEST_DISTANCE
    assert len(result.alternatives) == 1
    alt = result.alternatives[0]
    assert alt.label == "alternate_direct_route"
    assert alt.stop_sequence == ["S1", "D1", "S3"]
    assert alt.stop_sequence != result.stop_sequence


# --- only_option is truthful: a surfaced alternative means a comparison ---


def test_surfaced_alternatives_mean_primary_is_not_only_option(transfer_only_with_alternatives):
    """Regression: a transfer-only O/D used to come back with rank_reason
    'only_option' even though the response listed a 'Least distance'
    alternative. When the response shows more than one option, the reason
    must describe the actual comparison instead."""
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        include_alternatives=True,
    )
    assert result.alternatives, "precondition: the fixture must surface an alternative"
    assert result.rank_reason != ranking.ONLY_OPTION
    # The reason agrees with the ordering: the primary boards fewer buses
    # than the surfaced alternative(s).
    assert result.transfer_count == 1
    assert result.rank_reason == ranking.FEWEST_TRANSFERS
    for alt in result.alternatives:
        assert alt.transfer_count >= result.transfer_count


def test_genuinely_single_option_still_gets_only_option(congested_direct_vs_transfer):
    """The flip side: when the search really does yield one option (the
    transfer corridor is dominated under every weighting and is never
    surfaced), only_option is the honest answer -- we do not invent a
    comparison that did not happen."""
    result = find_shortest_path(
        session=None,
        origin_stop_id="S1",
        destination_stop_id="S3",
        include_alternatives=True,
    )
    assert result.alternatives == []
    assert result.rank_reason == ranking.ONLY_OPTION


# --- transfer-penalty boundary and deterministic tie-breaking ---


def test_transfer_penalty_boundary_is_exactly_one_penalty():
    """The documented threshold: a transfer with one extra boarding wins
    only when its raw distance is more than TRANSFER_PENALTY metres below
    the direct's (score = distance + P * boardings)."""

    def seg(route_id, weight):
        return PathSegment("A", "B", route_id, route_id is None, weight)

    direct = RouteFinderResult(
        segments=[seg("R1", 1.0)],
        total_distance_m=4000.0,
        transfer_count=0,
        stop_sequence=["A", "B"],
    )
    # distance + 2P == direct distance + P  -> exact tie
    transfer_tie = RouteFinderResult(
        segments=[seg("R1", 1.0), seg(None, 1.0), seg("R2", 1.0)],
        total_distance_m=4000.0 - TRANSFER_PENALTY,
        transfer_count=1,
        stop_sequence=["A", "B", "C", "D"],
    )
    assert ranking.static_ranking_score(transfer_tie) == ranking.static_ranking_score(direct)
    assert ranking.static_ranking_score(
        replace(transfer_tie, total_distance_m=transfer_tie.total_distance_m - 1.0)
    ) < ranking.static_ranking_score(direct)
    assert ranking.static_ranking_score(
        replace(transfer_tie, total_distance_m=transfer_tie.total_distance_m + 1.0)
    ) > ranking.static_ranking_score(direct)


def test_tie_break_is_deterministic_by_stop_sequence(monkeypatch):
    """Two direct routes with identical score, transfer count and distance
    resolve the same way every time, by the documented final tie-break on
    the stop sequence."""
    s1 = make_stop("S1", "Start", 27.7000, 85.3100)
    s3 = make_stop("S3", "End", 27.7200, 85.3100)
    a = make_stop("A", "Alpha", 27.7100, 85.3100)
    b = make_stop("B", "Bravo", 27.7100, 85.3100)  # same coords -> same distance
    route_a = make_route("R_A", [s1, a, s3])
    route_b = make_route("R_B", [s1, b, s3])
    monkeypatch.setattr(gb, "get_active_routes", lambda session: [route_a, route_b])

    first = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    second = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    assert first.stop_sequence == second.stop_sequence
    # "S1","A","S3" sorts before "S1","B","S3"; both are otherwise identical.
    assert first.stop_sequence == ["S1", "A", "S3"]


# --- cache parity: the graph cache is the only cache on this path ---


def test_ranking_is_stable_across_cold_and_warm_graph_cache(detour_vs_short_transfer):
    """The route-finder has no response cache, so a warm graph cache must
    return exactly what a from-scratch rebuild returns: same ordering, same
    reason, same alternatives. This is the parity that would break first if
    a future response cache started serving a different shape."""
    cold = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", include_alternatives=True
    )
    cold_alts = [alt.stop_sequence for alt in cold.alternatives]

    gb.invalidate_graph_cache()  # force a full graph rebuild
    warm = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", include_alternatives=True
    )

    assert warm.stop_sequence == cold.stop_sequence
    assert warm.rank_reason == cold.rank_reason
    assert [alt.stop_sequence for alt in warm.alternatives] == cold_alts


def test_congestion_default_and_opt_in_do_not_share_a_result(
    congested_direct_vs_transfer, monkeypatch
):
    """Different ranking semantics must never be conflated: a default call,
    then an opted-in call, then a default call again must leave the default
    answer unchanged and the opted-in answer distinct."""
    patch_congestion(
        monkeypatch,
        {
            ("R_A", "S1", "S2"): 1.0,
            ("R_A", "S2", "S3"): 3.0,
            ("R_B", "S2x", "S3"): 1.0,
        },
    )
    default_first = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")
    opted = find_shortest_path(
        session=None, origin_stop_id="S1", destination_stop_id="S3", avoid_congestion=True
    )
    default_second = find_shortest_path(session=None, origin_stop_id="S1", destination_stop_id="S3")

    assert default_first.stop_sequence == default_second.stop_sequence
    assert default_first.rank_reason == default_second.rank_reason
    assert opted.stop_sequence != default_first.stop_sequence
    assert opted.rank_reason == ranking.AVOIDS_CONGESTION


def test_route_finder_endpoint_is_not_response_cached():
    """The route-finder must stay uncached (its ranking depends on
    avoid_congestion/include_alternatives, and cache keys would have to
    distinguish them). cached_response stamps
    __response_cache_namespace__ on its wrapper; its absence is what proves
    the endpoint isn't cached. (It can't be checked via __wrapped__, which
    functools.wraps sets for any decorator -- slowapi's rate limiter sets
    it too.)"""
    from app.api import routing

    assert not hasattr(routing.find_route, "__response_cache_namespace__")
    assert not hasattr(routing.walking_route, "__response_cache_namespace__")


# --- congestion-aware cost agrees with the documented policy ---


def test_congestion_aware_cost_collapses_to_static_policy():
    """With no congestion signal the shared scorer must equal the documented
    policy score exactly, so default and congestion-aware modes agree when
    there is nothing to distinguish."""
    direct = RouteFinderResult(
        segments=[PathSegment("A", "B", "R1", False, 4000.0)],
        total_distance_m=4000.0,
        transfer_count=0,
        stop_sequence=["A", "B"],
    )
    assert pf._congestion_aware_cost(None, [], {}, direct) == pytest.approx(
        ranking.static_ranking_score(direct)
    )


# --- constants unchanged ---


def test_transfer_penalty_and_lambda_are_the_documented_values():
    assert TRANSFER_PENALTY == 3000
    assert CONGESTION_LAMBDA == 0.75
