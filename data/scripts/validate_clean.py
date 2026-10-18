#!/usr/bin/env python3
"""
validate_clean.py — integrity checks on data/processed/*.csv, no database required.

Runs the same checks as the sanity-check block import_data.py runs after
loading, so you can catch problems in CI before ever touching Postgres.

Usage:
    python scripts/validate_clean.py --dir data/processed
    # exits 1 and prints failures if any check is non-zero

    python scripts/validate_clean.py --dir data/processed --write-ready
    # also writes normalized, load-ready copies to <dir>/ready/ once every
    # check passes (bare comma lists in array columns like `aliases` and
    # `unverified_fields` get wrapped as Postgres array literals, e.g.
    # "a, b" -> "{a,b}") — nothing is written if any check fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ARRAY_COLS = ("aliases", "unverified_fields")


def load(dir_: Path, name: str) -> pd.DataFrame:
    path = dir_ / name
    if not path.exists():
        print(f"MISSING: {path}", file=sys.stderr)
        sys.exit(2)
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def to_array_literal(val: object) -> object:
    """"a, b" -> "{a,b}"; already-braced or empty values pass through unchanged."""
    if pd.isna(val) or val == "":
        return val
    s = str(val)
    if s.startswith("{") and s.endswith("}"):
        return s
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return "{" + ",".join(parts) + "}"


def route_service_checks(routes: pd.DataFrame) -> dict[str, int]:
    """Read-only checks for routes.frequency_min and the service window.

    Mirrors the read-time normalization RouteOut applies (app/schemas.py):
    a frequency must be a positive whole number of minutes, and a service
    window is only meaningful when both bounds are present, parse as
    HH:MM[:SS], and start before end -- the schema has no overnight
    semantics. Anything failing a check is surfaced to the API/UI as
    "unknown", not repaired here; this only flags it for a human. Columns
    absent from the frame are skipped, so a fixture without service columns
    still validates.

    Returns a {check_name: failing_row_count} mapping; all-zero is healthy.
    """
    checks: dict[str, int] = {}

    if "frequency_min" in routes.columns:
        raw = routes["frequency_min"]
        present = raw.notna() & (raw.astype(str).str.strip() != "")
        numeric = pd.to_numeric(raw, errors="coerce")
        # Whole minutes only: 15 is fine, "15.5", "abc", 0 and -5 are not.
        bad_freq = present & (numeric.isna() | (numeric <= 0) | (numeric % 1 != 0))
        checks["routes.frequency_min non-positive or non-integer"] = int(bad_freq.sum())

    def bad_time_count(col: str) -> int:
        raw = routes[col]
        present = raw.notna() & (raw.astype(str).str.strip() != "")
        text = raw.astype(str).str.strip()
        shape_ok = text.str.match(r"^\d{2}:\d{2}(:\d{2})?$")
        hour = pd.to_numeric(text.str.slice(0, 2), errors="coerce")
        minute = pd.to_numeric(text.str.slice(3, 5), errors="coerce")
        in_range = hour.between(0, 23) & minute.between(0, 59)
        return int((present & (~shape_ok | ~in_range)).sum())

    if "service_start_time" in routes.columns:
        checks["routes.service_start_time invalid"] = bad_time_count("service_start_time")
    if "service_end_time" in routes.columns:
        checks["routes.service_end_time invalid"] = bad_time_count("service_end_time")

    if {"service_start_time", "service_end_time"}.issubset(routes.columns):
        start = routes["service_start_time"].astype(str).str.strip()
        end = routes["service_end_time"].astype(str).str.strip()
        both_present = (
            routes["service_start_time"].notna() & (start != "")
            & routes["service_end_time"].notna() & (end != "")
        )
        # Zero-padded HH:MM[:SS] sorts lexicographically the same as it does
        # chronologically, so this catches a missing, equal, or reversed end.
        ordered = start < end
        checks["routes.service window start >= end"] = int((both_present & ~ordered).sum())

    return checks


def consecutive_duplicate_stops(
    route_stops: pd.DataFrame,
) -> dict[str, list[str]]:
    """Routes with the same stop_id at two adjacent sequence_no values.

    Returns {route_id: ["seq 8/9 S0304", ...]} for every such run. The
    validator used to check only orphan/non-contiguous sequence numbers, so
    these slipped through entirely (AUD-007); the graph turns each pair into
    a distinct ride node with a zero-length ride edge. Whether a given run is
    an intentional loop revisit or a data error is a data-owner decision, so
    callers report this rather than treating it as automatically fatal.
    Columns absent from the frame yield an empty mapping.
    """
    if not {"route_id", "stop_id", "sequence_no"}.issubset(route_stops.columns):
        return {}

    found: dict[str, list[str]] = {}
    for route_id, group in route_stops.groupby("route_id"):
        # sequence_no is read as str, so order numerically (as bad_sequence
        # above does) or "10" sorts before "2".
        ordered = sorted(
            (
                (int(seq), stop_id)
                for seq, stop_id in zip(group["sequence_no"], group["stop_id"])
            ),
            key=lambda pair: pair[0],
        )
        pairs = [
            f"seq {ordered[i][0]}/{ordered[i + 1][0]} {ordered[i][1]}"
            for i in range(len(ordered) - 1)
            if ordered[i][1] == ordered[i + 1][1]
        ]
        if pairs:
            found[str(route_id)] = pairs
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path("data/processed"))
    ap.add_argument(
        "--write-ready",
        action="store_true",
        help="write normalized, load-ready CSVs to <dir>/ready/ if all checks pass",
    )
    args = ap.parse_args()

    operators = load(args.dir, "operators_clean.csv")
    stops = load(args.dir, "stops_clean.csv")
    routes = load(args.dir, "routes_clean.csv")
    route_stops = load(args.dir, "route_stops_clean.csv")
    route_operators = load(args.dir, "route_operators_clean.csv")
    fare_rules = load(args.dir, "fare_rules_clean.csv")

    checks = {}

    checks["route_stops.stop_id not in stops"] = (
        ~route_stops["stop_id"].isin(stops["stop_id"])
    ).sum()

    checks["route_stops.route_id not in routes"] = (
        ~route_stops["route_id"].isin(routes["route_id"])
    ).sum()

    checks["route_operators.route_id not in routes"] = (
        ~route_operators["route_id"].isin(routes["route_id"])
    ).sum()

    checks["route_operators.operator_id not in operators"] = (
        ~route_operators["operator_id"].isin(operators["operator_id"])
    ).sum()

    checks["routes.operator_id not in operators (excl. NULL)"] = (
        routes["operator_id"].notna()
        & ~routes["operator_id"].isin(operators["operator_id"])
    ).sum()

    checks["routes.start_stop_id not in stops"] = (
        ~routes["start_stop_id"].isin(stops["stop_id"])
    ).sum()

    checks["routes.end_stop_id not in stops"] = (
        ~routes["end_stop_id"].isin(stops["stop_id"])
    ).sum()

    counts = route_stops.groupby("route_id").size()
    mismatch = routes.apply(
        lambda r: int(r["total_stops"]) != int(counts.get(r["route_id"], 0)), axis=1
    ).sum()
    checks["routes.total_stops mismatched vs actual route_stops count"] = int(mismatch)

    dup_stop_ids = stops["stop_id"].duplicated().sum()
    checks["stops.stop_id duplicated"] = int(dup_stop_ids)

    dup_operator_ids = operators["operator_id"].duplicated().sum()
    checks["operators.operator_id duplicated"] = int(dup_operator_ids)

    # sequence_no must be 1..N with no gaps/dupes within each route
    def bad_sequence(group: pd.DataFrame) -> bool:
        seqs = sorted(int(s) for s in group["sequence_no"])
        return seqs != list(range(1, len(seqs) + 1))

    bad_seq_routes = route_stops.groupby("route_id").apply(bad_sequence)
    checks["routes with non-contiguous sequence_no"] = int(bad_seq_routes.sum())

    dup_route_ids = routes["route_id"].duplicated().sum()
    checks["routes.route_id duplicated"] = int(dup_route_ids)

    dup_route_operator_pairs = route_operators.duplicated(subset=["route_id", "operator_id"]).sum()
    checks["route_operators.(route_id, operator_id) duplicated"] = int(dup_route_operator_pairs)

    lat = pd.to_numeric(stops["lat"], errors="coerce")
    lng = pd.to_numeric(stops["lng"], errors="coerce")
    bad_latlng = (lat.isna() | lng.isna() | ~lat.between(-90, 90) | ~lng.between(-180, 180)).sum()
    checks["stops.lat/lng out of range or non-numeric"] = int(bad_latlng)

    fr_min = pd.to_numeric(fare_rules["min_distance_km"], errors="coerce")
    fr_max = pd.to_numeric(fare_rules["max_distance_km"], errors="coerce")
    fare_min = pd.to_numeric(fare_rules["fare_npr_min"], errors="coerce")
    fare_max = pd.to_numeric(fare_rules["fare_npr_max"], errors="coerce")
    bad_fare_bounds = (
        fr_min.isna() | fr_max.isna() | fare_min.isna() | fare_max.isna()
        | (fr_min < 0) | (fr_max <= fr_min) | (fare_max < fare_min)
    ).sum()
    checks["fare_rules min/max bounds invalid"] = int(bad_fare_bounds)

    fr_sorted = fare_rules.assign(_lo=fr_min, _hi=fr_max).sort_values("_lo")
    overlaps = (fr_sorted["_lo"].shift(-1) < fr_sorted["_hi"]).sum()
    checks["fare_rules overlapping distance bands"] = int(overlaps)

    checks.update(route_service_checks(routes))

    consec_dupes = consecutive_duplicate_stops(route_stops)

    ok = True
    print(f"{'CHECK':<62}{'COUNT':>8}  STATUS")
    print("-" * 82)
    for name, count in checks.items():
        status = "OK" if count == 0 else "FAIL"
        if count != 0:
            ok = False
        print(f"{name:<62}{count:>8}  {status}")

    print()
    if consec_dupes:
        # Reported, not failed: the current dataset's runs are override-kept
        # revisits pending a data-owner keep/drop decision (AUD-007).
        print(
            f"WARNING: {len(consec_dupes)} route(s) have adjacent identical "
            f"stops (consecutive duplicate sequence_no):",
            file=sys.stderr,
        )
        for route_id, pairs in consec_dupes.items():
            print(f"  {route_id}: {', '.join(pairs)}", file=sys.stderr)
        print(file=sys.stderr)

    if not ok:
        print("One or more checks FAILED — see above.", file=sys.stderr)
        return 1

    print("All checks passed.")

    if args.write_ready:
        ready_dir = args.dir / "ready"
        ready_dir.mkdir(parents=True, exist_ok=True)
        frames = {
            "operators_clean.csv": operators,
            "stops_clean.csv": stops,
            "routes_clean.csv": routes,
            "route_stops_clean.csv": route_stops,
            "route_operators_clean.csv": route_operators,
            "fare_rules_clean.csv": fare_rules,
        }
        for name, df in frames.items():
            out = df.copy()
            for col in ARRAY_COLS:
                if col in out.columns:
                    out[col] = out[col].map(to_array_literal)
            out.to_csv(ready_dir / name, index=False)
        print(f"Load-ready CSVs written to {ready_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
