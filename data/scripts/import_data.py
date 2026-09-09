"""Load data/processed/*_clean.csv into the schema Alembic already created.

Replaces data/import.sql and data/import_in_container.sql: those need every
occurrence of a hardcoded absolute path hand-edited to match whoever's
machine (or container mount) is running them before each import. This script
takes the CSV path as an argument (or uses the repo-relative default) and
streams each file to Postgres with psycopg2's COPY, so there's no path to
edit and nothing environment-specific to get wrong.

Same tables, same column order, same COPY options (CSV, HEADER, NULL ''),
same post-import sanity checks as the two .sql files -- this only changes
*how* the CSVs get to the server, not the schema or the data.

Usage (from repo root):
    python data/scripts/import_data.py
    python data/scripts/import_data.py --database-url postgresql://...
    python data/scripts/import_data.py --processed-dir data/processed
    python data/scripts/import_data.py --truncate   # clear the 6 tables first (dev/re-import only)

DB connection resolution, in order:
    1. --database-url
    2. $DATABASE_URL environment variable
    3. DATABASE_URL= line in backend/.env
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[2]

# (table, columns, csv filename) -- same order as import.sql / import_in_container.sql,
# which is dependency order (referenced tables before referencing tables).
TABLES: list[tuple[str, list[str], str]] = [
    (
        "operators",
        ["operator_id", "name", "service_type", "contact_number", "rating", "unverified_fields"],
        "operators_clean.csv",
    ),
    (
        "stops",
        [
            "stop_id", "stop_name", "aliases", "lat", "lng", "zone", "district", "ward",
            "is_major_stop", "landmark", "has_shelter", "has_ticket_counter", "is_interchange",
            "wheelchair_access", "audio_support", "status", "unverified_fields", "created_at",
            "updated_at", "geo_out_of_bounds",
        ],
        "stops_clean.csv",
    ),
    (
        "routes",
        [
            "route_id", "route_name", "short_name", "vehicle_type", "route_type", "operator",
            "start_stop_id", "end_stop_id", "total_stops", "approx_distance_km",
            "estimated_duration_min", "service_start_time", "service_end_time", "frequency_min",
            "fare_type", "has_ac", "is_express", "status", "notes", "created_at", "updated_at",
            "operator_id", "return_leg_verified", "operator_id_raw", "is_multi_operator",
            "haversine_distance_km", "max_consecutive_stop_jump_km", "approx_distance_km_original",
            "distance_flagged_for_recompute", "status_original", "status_corrected_for_return_leg",
            "is_bidirectional", "osrm_distance_km",
        ],
        "routes_clean.csv",
    ),
    ("route_stops", ["route_id", "stop_id", "sequence_no"], "route_stops_clean.csv"),
    ("route_operators", ["route_id", "operator_id", "is_primary"], "route_operators_clean.csv"),
    (
        "fare_rules",
        [
            "fare_id", "min_distance_km", "max_distance_km", "fare_npr_min", "fare_npr_max",
            "student_discount_pct", "verification_note",
        ],
        "fare_rules_clean.csv",
    ),
]

SANITY_CHECKS = [
    (
        "route_stops -> stops orphan",
        "SELECT count(*) FROM route_stops rs LEFT JOIN stops s ON rs.stop_id = s.stop_id WHERE s.stop_id IS NULL",
    ),
    (
        "route_stops -> routes orphan",
        "SELECT count(*) FROM route_stops rs LEFT JOIN routes r ON rs.route_id = r.route_id WHERE r.route_id IS NULL",
    ),
    (
        "route_operators -> operators orphan",
        "SELECT count(*) FROM route_operators ro LEFT JOIN operators o ON ro.operator_id = o.operator_id WHERE o.operator_id IS NULL",
    ),
    (
        "routes.total_stops mismatch",
        """SELECT count(*) FROM routes r
           LEFT JOIN (SELECT route_id, count(*) c FROM route_stops GROUP BY route_id) rs
             ON rs.route_id = r.route_id
           WHERE r.total_stops IS DISTINCT FROM rs.c""",
    ),
    ("stops missing geom", "SELECT count(*) FROM stops WHERE geom IS NULL"),
]


def resolve_database_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_path = REPO_ROOT / "backend" / ".env"
    if env_path.exists():
        m = re.search(r"^DATABASE_URL=(.+)$", env_path.read_text(), re.MULTILINE)
        if m:
            return m.group(1).strip()
    sys.exit(
        "No DATABASE_URL found. Pass --database-url, set $DATABASE_URL, "
        "or create backend/.env (see backend/.env.example)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--processed-dir", default=str(REPO_ROOT / "data" / "processed"))
    parser.add_argument(
        "--truncate", action="store_true",
        help="TRUNCATE ... RESTART IDENTITY CASCADE the 6 tables before importing "
             "(dev convenience for re-running against a non-empty DB; off by default "
             "since the existing .sql workflow assumes an empty DB too).",
    )
    args = parser.parse_args()

    processed_dir = Path(args.processed_dir)
    missing = [name for _, _, name in TABLES if not (processed_dir / name).exists()]
    if missing:
        sys.exit(f"Missing CSVs in {processed_dir}: {', '.join(missing)}\nRun clean_data.py first.")

    database_url = resolve_database_url(args.database_url)

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                if args.truncate:
                    # Reverse dependency order so FKs don't block the truncate.
                    for table, _, _ in reversed(TABLES):
                        cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
                    print("truncated: " + ", ".join(t for t, _, _ in TABLES))

                for table, columns, csv_name in TABLES:
                    csv_path = processed_dir / csv_name
                    col_list = ", ".join(columns)
                    copy_sql = f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                    with open(csv_path, "r", encoding="utf-8", newline="") as f:
                        cur.copy_expert(copy_sql, f)
                    cur.execute(f"SELECT count(*) FROM {table}")
                    print(f"{table}: {cur.fetchone()[0]} rows ({csv_name})")

                print("\nsanity checks (every count should be 0):")
                failed = False
                for label, sql in SANITY_CHECKS:
                    cur.execute(sql)
                    count = cur.fetchone()[0]
                    flag = "" if count == 0 else "  <-- NONZERO"
                    if count != 0:
                        failed = True
                    print(f"  {label}: {count}{flag}")
        if failed:
            sys.exit("\nImport completed but sanity checks found issues -- see above.")
        print("\nImport complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
