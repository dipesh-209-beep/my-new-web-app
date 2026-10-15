"""
scripts/seed_demo_congestion.py

Seeds peak-hour congestion samples on real segments departing every stop
listed in data/stop_congestion_scores.csv -- a manifest that matches this
project's actual stops to a static per-intersection severity score from
data/congestion_loss_table.csv. That severity table's origin is NOT
recorded in this repository (no author, date, method or citation), so the
scores are unverified estimates, not measurements; the manifest's "source"
column labels each row "severity_table_unverified" (matched to the table)
or "fallback_average" (the table's own mean, used for the couple of stops
it does not cover -- see there for exactly which ones and why). This lets
avoid_congestion=true on /route-finder have data to route around instead
of an empty table.

The durations this script writes are SYNTHETIC: a free-flow guess (the
segment distance / an assumed city-bus speed) scaled by the score-derived
ratio -- not observed traffic. They go through the normal
record_congestion_sample upsert with is_seeded=False, so at the row level
they are indistinguishable from the OSRM-derived durations /route-finder
records. is_seeded is a seed-vs-upsert flag, NOT a provenance field -- see
app/routing/congestion_provenance.py.

For each stop in the manifest, this DISCOVERS a real departing segment at
runtime from the live routing graph (the longest "ride" edge leaving that
physical stop, across any route serving it) rather than a hardcoded list
-- so this script keeps working as routes/stops data changes, and scales
to however many stops the manifest covers without hand-picking each one.
A small OVERRIDE_SEGMENTS table below takes precedence for the couple of
stops where a specific segment (not just "the longest edge") is known to
produce a visible reroute -- see its own docstring.

This seeds the ORGANIC, per-(route, segment) congestion mechanism (see
app/db/queries.py's record_congestion_sample). There's a second,
independent mechanism too -- geographic congestion ZONES (see
app/routing/congestion_zones.py), which apply to any route passing near a
real congested point regardless of which route it is, and need no
seeding step at all (they load straight from data/congestion_zones.csv
whenever avoid_congestion=true is used). Both are combined in
pathfinder.py's weight function -- see its docstring for how. This script
demonstrates the organic mechanism specifically; see
docs/congestion-demo.md for a worked example of both.

Run:
    python -m scripts.seed_demo_congestion

Safe to re-run: each call just re-records another sample into the same
(day_of_week, hour_bucket) via the normal EMA blend in
queries.record_congestion_sample, it doesn't duplicate rows.
"""

import csv
from pathlib import Path

from app.db.session import SessionLocal
from app.db import queries
from app.routing import graph_builder as gb
from app.routing.congestion_zones import score_to_ratio
from app.routing.time_buckets import day_and_bucket_for, now_in_nepal

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "data" / "stop_congestion_scores.csv"

# Assumed average free-flow city-bus speed, used only to turn a real
# distance into a plausible free-flow duration for the seed.
FREE_FLOW_SPEED_MPS = 20_000 / 3600  # ~20 km/h


def load_manifest() -> list[dict]:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_departing_segment(graph, stop_id: str):
    """The longest real "ride" edge leaving any (stop_id, route) node in
    the live graph -- one real trunk-route hop departing this physical
    stop, picked dynamically so this script doesn't need a hardcoded
    segment per stop. Returns (route_id, to_stop_id, distance_m), or None
    if this stop boards no active route.

    OVERRIDE_SEGMENTS takes precedence for the handful of stops where a
    SPECIFIC segment (not necessarily the longest one) is known to
    produce a visible reroute under avoid_congestion=true -- "the longest
    edge" is a reasonable default for broad coverage, but it doesn't know
    which edge matters for a demo."""
    if stop_id in OVERRIDE_SEGMENTS:
        return OVERRIDE_SEGMENTS[stop_id]

    best = None
    for node in graph.nodes:
        if not (isinstance(node, tuple) and node[0] == stop_id):
            continue
        for _, v, data in graph.out_edges(node, data=True):
            if data.get("kind") != "ride":
                continue
            distance_m = data.get("distance_m", 0)
            if best is None or distance_m > best[2]:
                best = (data.get("route_id"), v[0], distance_m)
    return best


# Verified-by-hand overrides for stops where the organic-mechanism demo
# depends on a SPECIFIC segment -- see docs/congestion-demo.md for what
# each of these actually produces when congested:
#
# - S0056 (Tripureshwor): R-SAJHA-01 and R-SAJHA-02 run the identical
#   physical stops from here through Jamal before diverging. Congesting
#   THIS specific leg (not just any edge from Tripureshwor) causes a real
#   route SWITCH to R-SAJHA-02 for the same stretch.
# - S0044 (Narayangopal Chowk -- a "fallback_average" manifest stop): this
#   R-SAJHA-01 hop is the one Maitighar->Gongabu Bus Park traffic actually
#   rides through. Congesting it triggers a reroute via a different
#   feeder route, since R-SAJHA-01 is the only route serving Gongabu Bus
#   Park at all.
OVERRIDE_SEGMENTS = {
    "S0056": ("R-SAJHA-01", "S0236", 1466.0),
    "S0044": ("R-SAJHA-01", "S0065", 1565.0),
}


def main() -> None:
    session = SessionLocal()
    graph = gb.get_cached_graph(session)
    day_of_week, hour_bucket = day_and_bucket_for(now_in_nepal())
    manifest = load_manifest()

    print(f"Seeding demo congestion for day_of_week={day_of_week}, hour_bucket={hour_bucket}")
    print(f"Severity source: {MANIFEST_PATH.name} ({len(manifest)} stops)\n")

    seeded, skipped = 0, 0
    for row in manifest:
        stop_id = row["stop_id"]
        score = float(row["score"])
        segment = find_departing_segment(graph, stop_id)
        if segment is None:
            print(f"  SKIP {stop_id} ({row['stop_name']}): boards no active route")
            skipped += 1
            continue

        route_id, to_stop_id, distance_m = segment
        ratio = score_to_ratio(score)
        free_flow_duration_s = distance_m / FREE_FLOW_SPEED_MPS
        congested_duration_s = free_flow_duration_s * ratio

        queries.seed_congestion_baseline(
            session,
            route_id=route_id,
            from_stop_id=stop_id,
            to_stop_id=to_stop_id,
            duration_s=free_flow_duration_s,
            distance_m=distance_m,
        )
        queries.record_congestion_sample(
            session,
            route_id=route_id,
            from_stop_id=stop_id,
            to_stop_id=to_stop_id,
            day_of_week=day_of_week,
            hour_bucket=hour_bucket,
            duration_s=congested_duration_s,
            distance_m=distance_m,
        )
        print(
            f"  [{row['stop_name']}, score={score}, {row['source']}] {route_id}: "
            f"{stop_id} -> {to_stop_id}  ratio={ratio:.2f}"
        )
        seeded += 1

    session.close()
    print(f"\nSeeded {seeded} segments, skipped {skipped} (no active route from that stop).")
    print("Note: geographic congestion zones (app/routing/congestion_zones.py) apply")
    print("automatically without this script -- see docs/congestion-demo.md.")
    print("\nTry GET /route-finder?origin=S0056&destination=S0072&avoid_congestion=true")
    print("vs the same call with avoid_congestion=false to see the difference.")


if __name__ == "__main__":
    main()
