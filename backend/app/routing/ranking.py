"""
routing/ranking.py

The route-finder's ranking policy in one place, plus the machine-readable
reason why the primary ("Recommended") option was chosen.

Policy
------
Every eligible candidate -- each active route connecting origin and
destination directly, and the multi-transfer path the Dijkstra search
finds -- is scored on ONE basis:

    score = (sum of ridden/walked stop-to-stop distance in metres)
            + TRANSFER_PENALTY * (number of buses boarded)

This is a HEURISTIC over two readily-comparable attributes, not a model
of the complete passenger journey:

- The distance is the routing graph's `distance_m` edge weight, i.e. the
  straight-line (haversine) gap between consecutive stops, summed along
  the path. It is NOT OSRM road distance and NOT a measured trip length;
  the API separately attaches real OSRM road_geometry for display, but
  ranking never sees it.
- `TRANSFER_PENALTY` (constants.py) is a configurable m-equivalent
  preference for avoiding a bus change. It is deliberately expressed in
  metres only so it can be added to distance in one number -- it is NOT
  a measured transfer time, walk, or wait. Changing it changes which
  routes are preferred and needs its own tests.
- Nothing here estimates journey duration. Time is never a ranking input
  (see below); the UI's duration figure comes from a separate fixed-speed
  estimate.

Each *ride* edge's contribution is additionally inflated by the current
congestion ratio ONLY when the caller opts in (`avoid_congestion=true`):

    inflated = distance * (1 + CONGESTION_LAMBDA * max(ratio - 1, 0))

The lowest score wins; ties break deterministically by transfer count,
then distance, then stop sequence. This is deliberately the same basis
the routing graph's own edge weights use (see graph_builder.build_graph
and pathfinder._congestion_aware_cost): the primary is never chosen by a
rule the search itself does not apply, and a viable direct route is
compared against transfer options rather than winning by fiat.

What is deliberately NOT used for ranking, and why
--------------------------------------------------
- Estimated travel time: it is a re-parameterisation of the same
  distances at the fixed BUS_AVG_SPEED_MPS / WALK_SPEED_MPS assumptions
  (constants.py), so ranking by it would add no information while
  presenting rough assumptions as a precise basis. (It does still rank
  the separate "fastest_estimated" alternative, which is labelled as an
  estimate.)
- Frequency / service hours: recorded per route but source-supplied and
  not operator-confirmed, and not comparable across a candidate set that
  mixes routes which have the data with routes that don't. Unknown must
  never read as favourable, so these do not enter the score at all.
- Fare: a desk estimate, not a ticketed price; it is never allowed to
  dominate the ordering.
These are surfaced to the rider separately (see app/schemas.py) but they
never reorder the options.
"""

from typing import Optional

from app.routing.constants import TRANSFER_PENALTY

# Machine-readable reasons for the primary result's selection. The
# frontend maps these to honest, localized copy -- it must never invent a
# reason outside this set, and the backend must never send one it cannot
# substantiate from the candidate comparison.
#
# ONLY_OPTION means the response described exactly ONE viable option: the
# candidate set held a single path AND no distinct alternative was
# surfaced. It is never used merely because the primary won the score or
# another option was dominated -- a dominated-but-present candidate still
# makes the choice a comparison, so a real ranking reason is sent instead.
ONLY_OPTION = "only_option"
FEWEST_TRANSFERS = "fewest_transfers"
SHORTEST_DISTANCE = "shortest_distance"
AVOIDS_CONGESTION = "avoids_congestion"
BEST_OVERALL = "best_overall"
VIA_STOPS = "via_stops"

RANK_REASONS = frozenset(
    {
        ONLY_OPTION,
        FEWEST_TRANSFERS,
        SHORTEST_DISTANCE,
        AVOIDS_CONGESTION,
        BEST_OVERALL,
        VIA_STOPS,
    }
)


def boardings(result) -> int:
    """Number of buses boarded on a candidate.

    A path that rides no bus at all (e.g. a pure walk between two
    interchange stops) boards zero times and must not pay a boarding
    penalty. transfer_count alone cannot tell "no boardings" apart from
    "one boarding" (both are 0), so the presence of a ride segment is the
    marker instead -- the same rule pathfinder._congestion_aware_cost
    uses.
    """
    if not any(segment.route_id is not None for segment in result.segments):
        return 0
    return result.transfer_count + 1


def static_ranking_score(result) -> float:
    """The congestion-free policy score for a candidate.

    Equals `pathfinder._congestion_aware_cost(graph, [], {}, result)`.
    Kept here as a pure function of the candidate's own distance and
    boarding count so it is obvious the score has no other inputs (in
    particular, no frequency, fare or time data can influence it).
    """
    return result.total_distance_m + TRANSFER_PENALTY * boardings(result)


def explain_primary(
    primary,
    runner: Optional[object],
    *,
    congestion_aware: bool,
    congestion_flipped: bool,
) -> str:
    """A truthful, machine-readable reason the primary beat the runner-up.

    `runner` is the next genuinely different option the response describes
    -- the next-best ranked candidate, or, when the ranking considered a
    single path, the best alternative that was surfaced -- or None when the
    response really held one option. `congestion_flipped` is True only when
    the caller opted in to congestion AND the primary did not win on the
    congestion-free basis -- i.e. congestion is what changed the outcome.
    Every branch states something the candidate comparison actually
    supports; there is no "fastest"/"cheapest" claim, because the ranking
    never used time or fare.
    """
    if runner is None:
        return ONLY_OPTION
    if congestion_aware and congestion_flipped:
        return AVOIDS_CONGESTION
    if boardings(primary) < boardings(runner):
        return FEWEST_TRANSFERS
    if primary.total_distance_m < runner.total_distance_m:
        return SHORTEST_DISTANCE
    return BEST_OVERALL
