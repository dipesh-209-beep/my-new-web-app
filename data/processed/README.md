# Kathmandu Bus Route Finder — Database (processed, v4)

Cleaned, orphan-audited CSVs plus a PostgreSQL + PostGIS schema and import
script for the Kathmandu Bus Route Finder project. This is the *processed*
dataset — raw/unverified sources and the full audit trail live in
`report.md`, regenerated fresh by `scripts/clean_data.py` on every pipeline
run (it is not versioned per-round; re-run the pipeline to refresh it, and
diff against git history if you need a prior round's numbers).

## Requirements

The processed dataset and `schema.sql`/`scripts/import_data.py` were
validated against this stack (stock Ubuntu 24.04 LTS apt packages — no
manual version pinning needed):

| Component  | Version |
|---|---|
| PostgreSQL | 16.14 |
| PostGIS    | 3.4.2 |
| GEOS       | 3.12.1 |
| PROJ       | 9.4.0 |

```bash
sudo apt install postgresql-16 postgresql-16-postgis-3
```

> **Note:** the schema itself is PostgreSQL-version-agnostic (no
> version-specific SQL features are used), so it also runs unmodified
> against the app's actual Docker/CI database, which uses
> `postgis/postgis:15-3.4` (PostgreSQL 15 + PostGIS 3.4) — see the root
> `docker-compose.yml` and `.github/workflows/ci.yml`. The 16.14 stack
> above was this validation round's local environment, not a hard
> requirement.

## Files

`schema.sql` lives one level up, in `data/` (not in this folder alongside
the CSVs) -- see `../schema.sql`. The importer, `../scripts/import_data.py`,
lives in `../scripts/` alongside the rest of the pipeline.

| File | Contents |
|---|---|
| `../schema.sql` | Table definitions, indexes, triggers, constraints. Run first (via Alembic in the live app — see `data/README.md`). |
| `../scripts/import_data.py` | Loads all 6 CSVs via `COPY` + sanity checks. Run second. |
| `operators_clean.csv` | 29 rows — bus/microbus/tempo operators |
| `stops_clean.csv` | 376 rows — physical stops (lat/lng, amenities) |
| `routes_clean.csv` | 113 rows — routes (distance, timing, operator link) |
| `route_stops_clean.csv` | 1,849 rows — ordered route ↔ stop mapping |
| `route_operators_clean.csv` | 106 rows — route ↔ operator (M:N, one primary each) |
| `fare_rules_clean.csv` | 5 rows — distance-banded fare lookup |
| `report.md` | Full audit trail, regenerated every run — read before trusting a number |

## Setup

```bash
createdb ktm_bus
psql -d ktm_bus -f ../schema.sql

python ../scripts/import_data.py --database-url postgresql://localhost/ktm_bus --processed-dir .
```

No path editing needed — `import_data.py` takes the CSV directory and DB
URL as arguments. (For the live app, `make setup` / `make import` from the
repo root does both of the above for you against the Docker Postgres.)

The importer prints a sanity-check block after loading — every row should
read `0` except `fare_rules row count`, which should read `5`.

## Schema overview

- **operators** — carrier registry (`operator_id` PK)
- **stops** — physical stops; `geom` (PostGIS `geography`) is auto-derived
  from `lat`/`lng` by a trigger, never set directly
- **routes** — one row per route; `operator_id` is the primary carrier,
  `total_stops`/`start_stop_id`/`end_stop_id` are kept in sync with
  `route_stops` (enforced by the sanity checks, not a DB constraint)
- **route_stops** — ordered stop sequence per route; the same `stop_id` can
  legitimately repeat within one route (loop/bidirectional routes)
- **route_operators** — M:N join; exactly one `is_primary = true` row per
  route (enforced by a partial unique index)
- **fare_rules** — distance → fare lookup. `min_distance_km` is inclusive,
  `max_distance_km` is **exclusive** (a route at exactly 5.00 km falls into
  the `[5,10)` band, not `[0,5)`). An `EXCLUDE` constraint makes overlapping
  bands physically impossible to insert.

## Known data caveats (see `report.md` for full detail)

- `report.md` regenerates on every pipeline run and currently shows **0**
  rows with a corrupted/invalid `is_express` value — every route in the
  committed dataset has `is_express = False`. (An earlier round of this
  README flagged two specific route IDs here; those IDs turned out to
  belong to routes that were never part of the committed `data/raw`
  export — see the "Unintegrated candidate routes" note in
  `../raw/README.md` — so the caveat didn't describe the shipped dataset
  and has been corrected rather than carried forward.)
- **`fare_rules.verification_note`** flags the fare figures as a
  2026-08 desk estimate, pending confirmation against the official Bagmati
  Province gazette notice — not yet independently verified.
- **`routes.frequency_min` / `service_start_time` / `service_end_time`** are
  **scheduled/estimated figures from the source data, not operator-confirmed
  and not live**, and have no documented provenance beyond that. The shipped
  data repeats a near-uniform headway (whole minutes: 15 or 30) and a single
  typical-day window (start always `06:00`; end `19:00` or `20:00`) across
  routes, so treat them as rough planning cues. A blank cell means *unknown*,
  not "no service". `validate_clean.py` flags malformed values, and the API
  (`RouteOut` in `backend/app/schemas.py`) surfaces any unusable value as
  `null` rather than presenting it as a schedule.
