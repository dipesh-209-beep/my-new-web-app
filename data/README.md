# Data
(schema, ingestion, cleaning, spatial queries, data access layer)

> **Licensing:** the data here is mostly OpenStreetMap-derived and is
> therefore **ODbL**, not the MIT that covers this project's code. The
> obligations, the unverified items, and what a derived database obliges you
> to do are in **[`LICENSING.md`](LICENSING.md)**. Per-file provenance is in
> [`raw/README.md`](raw/README.md). Read at least the summary before
> redistributing the dataset or hosting it publicly.

```
data/
├── raw/          Original exports
│                   - 2013 Yatayat OSM export
│                   - Overpass Turbo pulls
│                   - DOTM records
│                   - plus 3 unintegrated candidate-route CSVs the
│                     pipeline doesn't read — see raw/README.md
│
├── processed/    Cleaned, validated CSVs ready for DB import
│                   (see processed/README.md)
│
├── congestion_zones.csv         Zone definitions feeding the
├── congestion_loss_table.csv    congestion/duration edge-weight
├── stop_congestion_scores.csv   functions behind `avoid_congestion`
│                                  and `fastest_estimated` (see
│                                  backend/app/routing/graph_builder.py)
│
└── scripts/      Data-cleaning and validation pipeline — see
                    data/scripts/README.md for the full file list
                    (clean_data.py, validate_clean.py, merge_stops.py,
                    fix_compound_csv_fields.py, verify_stop_coordinates.py,
                    dedup override YAML files, tests, requirements.txt)
```

## Pipeline order

1. **`scripts/clean_data.py`** — `raw/` → `processed/`
   Turns raw exports into validated CSVs and regenerates
   `processed/report.md` documenting exactly what changed:
     1. Removes `route_stops` rows referencing a `stop_id` with no matching
        row in `stops`
     2. Re-sequences `route_stops.sequence_no` per route after removals
        (1..N)
     3. Recomputes `routes.start_stop_id` / `end_stop_id` / `total_stops`
        from `route_stops`
     4. Nulls out `routes.operator_id` where it has no match in `operators`
        and isn't recoverable from `operator_id_raw` or `route_operators`
     5. Flags distance outliers (haversine vs. recorded
        `approx_distance_km`)
     6. Verifies `route_operators`/`operators` have no orphan pairs
     7. Runs the same post-cleanup integrity checks `scripts/import_data.py`
        runs against Postgres

```bash
   python scripts/clean_data.py \
       --raw-dir data/raw \
       --out-dir data/processed
```

   Produces 5 of the 6 `processed/*_clean.csv` files (`operators`, `stops`,
   `routes`, `route_stops`, `route_operators`). `fare_rules_clean.csv` is
   maintained separately as a static desk-estimate table (see
   `processed/README.md`) — there's no raw fare source to derive it from,
   so it isn't touched by this script and must already exist in
   `--out-dir` for `validate_clean.py` / `import_data.py` to find it.

2. **`scripts/validate_clean.py`** — integrity checks on `processed/*.csv`,
   no database required. Runs the same checks as the sanity-check block
   `import_data.py` runs after loading, so problems can be caught in CI
   before ever touching Postgres.

```bash
   python scripts/validate_clean.py --dir data/processed
   # exits 1 and prints failures if any check is non-zero
```

3. **`schema.sql`** — builds the full schema (6 tables: `operators`,
   `stops`, `routes`, `route_stops`, `route_operators`, `fare_rules`)
   against the app's PostgreSQL + PostGIS instance (`postgis/postgis:15-3.4`
   per `docker-compose.yml` and CI). Applied via Alembic migration
   `0002_replace_with_full_schema` in the backend's migration chain, so the
   live app DB and this file stay in sync going forward. An auxiliary
   `route_return_leg_priority` QA table existed for a time but was dropped
   in a later migration (`b3c9d1e4c6a7`) and is no longer part of the schema.

4. **`scripts/import_data.py`** — loads all `processed/*_clean.csv` files
   into the schema from step 3 via `COPY`, in dependency order (operators →
   stops → routes → route_stops → route_operators → fare_rules), followed
   by a built-in referential-integrity sanity check — all currently passing
   clean.

```bash
   python scripts/import_data.py --processed-dir data/processed
   # reads DATABASE_URL from backend/.env by default; --database-url to override
   # --truncate clears the 6 tables first, for re-importing into a non-empty DB
```

   From the repo root, `make import` runs this (after `make migrate`). No
   path editing required — the old `import.sql` / `import_in_container.sql`
   (which needed every hardcoded absolute path hand-edited per machine) have
   been retired now that this is proven out end-to-end.

---

## Current dataset status

| Table              | Row count |
|--------------------|-----------|
| `routes`           | 113       |
| `stops`             | 396       |
| `operators`         | 29        |
| `route_stops`       | 1,901     |
| `route_operators`   | 106       |
| `fare_rules`        | 5         |

All of the above are confirmed loaded successfully into a live
PostgreSQL 15 + PostGIS 3.4 instance (matching `docker-compose.yml`/CI) via
`scripts/import_data.py`. `data/processed/README.md` lists PostgreSQL 16.14
as the version tested against for the standalone import path — see the note
there; the app's own database (Docker/CI) currently runs PostgreSQL 15.

**Referential-integrity audit** — passed clean end-to-end:
  - `route_stops → stops` orphan check: **0**
  - `route_stops → routes` orphan check: **0**
  - `route_operators → operators` orphan check: **0**
  - `routes.total_stops` vs. actual `route_stops` count: **0 mismatches**
  - `stops` rows missing `geom`: **0**

  See `processed/README.md` and `processed/report.md` for the full audit trail.

**Fare rules** — 5 distance bands, confirmed non-overlapping (enforced by
the `EXCLUDE USING gist` constraint) and correctly computed as
`[min_distance_km, max_distance_km)`.
  > Note: all 5 rows are currently `desk_estimate_2026-08` — scaled from a
  > prior Bagmati Province rate, pending confirmation against the official
  > gazette notice. **Not yet field-verified.**

**Field verification status** — not re-assessed this round.
  <!-- TODO: replace with current Tier 1/2/3 breakdown -->

**Unresolved operator matches** — none currently. All routes have a
resolved `operator_id` as of the latest cleaned dataset.
