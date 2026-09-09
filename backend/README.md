# Backend — Kathmandu Bus Route Finder

FastAPI + NetworkX (graph engine) + PostGIS data access layer.

For the full project overview (frontend, tech stack, team), see the [root README](../README.md).

## Prerequisites

- Python 3.11
- Docker (for Postgres + PostGIS)

## Setup

The fastest path from a clean checkout to a running stack is the root
`Makefile` (repo root, not `backend/`):

```bash
make setup       # data clean+validate, db up, migrations, CSV import, OSRM prep+up
make seed-admin  # interactive -- create the first admin login
make up          # build + start the backend on top of the stack `setup` brought up
```

Each target is also runnable on its own and safe to re-run (`make db-up`,
`make migrate`, `make import`, `make osrm`, ...) -- see the `Makefile` at the
repo root for the full list. This replaces the old `data/import.sql` /
`data/import_in_container.sql` (hand-edited absolute paths per machine) and
the manual OSRM extract/partition/customize/rename sequence below -- both
retired now that this is proven out end-to-end. Nothing about the DB
schema, migrations, or API changed -- same steps, wired together and, in
the CSV-import case, reimplemented in Python instead of hand-edited `\copy`.
`docker compose logs -f` / `make logs` to watch it, `make down` to stop
everything.

When running the backend inside Docker (`make up`, or `docker compose up -d
backend`), the service hot-reloads on edits under `backend/` because the
dev-only `docker-compose.override.yml` adds `uvicorn --reload`. That reload
lives in the compose overlay, not the Dockerfile, so the image default is a
stable uvicorn -- a deployment that runs the image directly, or excludes the
override (`docker compose -f docker-compose.yml up -d`), gets a normal,
non-restarting process. See the root README for the same note.

<details>
<summary>Manual, step-by-step setup (what <code>make setup</code> does under the hood)</summary>

```bash
# 1. Start Postgres + PostGIS (from the repo root, not backend/)
cd ..
docker compose up -d db
cd backend

# 2. Create your local env file
cp .env.example .env
# Defaults in .env.example match docker-compose.yml, so no edits needed
# for local dev. If you change DB credentials in docker-compose.yml,
# update .env to match.
# ADMIN_API_KEY and JWT_SECRET_KEY have no defaults in the app itself --
# generate real values for local dev:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 3. Python environment
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt   # add -dev to run pytest locally

# 4. Apply migrations (creates stops / routes / route_stops / etc.)
alembic upgrade head

# 5. Import the cleaned CSVs (no path editing needed; reads DATABASE_URL
# from backend/.env)
cd .. && python data/scripts/import_data.py && cd backend

# 6. Seed the first admin account (needed for POST /admin/login --
# there's no self-registration endpoint)
python3 -m scripts.seed_admin

# 7. (Optional) Road-network route geometry via OSRM. Without this,
# /route-finder still works correctly -- it just returns
# road_geometry: null on every leg, and the frontend falls back to
# straight-line segments between stops.
./scripts/prepare_osrm_data.sh   # one-time; idempotent; car + foot profiles
cd .. && docker compose up -d osrm osrm-foot && cd backend
# Driving instance runs on http://localhost:5000, foot instance on
# http://localhost:5001 (see OSRM_FOOT_BASE_URL in backend/.env or your
# environment). Both restart automatically on reboot
# (restart: unless-stopped) -- no need to bring them up again after
# the first time unless you stop them explicitly.

# 8. Run the API
uvicorn app.main:app --reload
```
</details>

Backend runs at `http://localhost:8000` (interactive docs at `/docs`).

## Admin API

Data-entry endpoints in `app/api/admin.py` (`POST /stops`, `POST /routes`,
`POST /routes/{route_id}/stops`, `DELETE /routes/{route_id}/stops/{sequence_no}`,
`PATCH /routes/{route_id}/stops/order`, `PATCH /routes/{route_id}/status`,
`POST /graph/reload`) are behind `require_admin`, which accepts **either**
of two credentials:

- **`X-Admin-Api-Key` header** (`ADMIN_API_KEY` in `.env`) — one shared
  secret, for scripted/ETL callers.
- **JWT via `POST /admin/login`** — authenticates an `AdminUser` account
  (seeded with `python3 -m scripts.seed_admin`, see Setup step 6) and
  returns a bearer token, attaching that specific admin to the request
  for future per-admin authorization/audit use. Rate-limited to 5
  requests/minute per IP (see `app/core/rate_limit.py`) -- expect a 429
  if you're hammering this endpoint repeatedly while testing.

`stop_id`/`route_id` for newly created rows are server-generated, not
caller-supplied: stops get the next sequential `S####` value, routes get
a reserved `M######` prefix (kept separate from the existing `R`-number
space, which is OSM-sourced and not sequential — see
`app/db/id_generator.py`).

Flipping a route's status via `PATCH /routes/{route_id}/status`, or adding a stop to a route via `POST /routes/{route_id}/stops`, bumps a shared `graph_meta.version` counter in the database and refreshes this process's own cache immediately. Every other worker process/replica notices the version change on its own next request and rebuilds automatically -- this is what makes cache invalidation correct beyond a single-process deployment, rather than relying solely on the request that happened to make the change. See `app/models/graph_meta.py` for the full rationale. `POST /graph/reload` remains available as a manual escape hatch.

### Editing a route's stop sequence

- `DELETE /routes/{route_id}/stops/{sequence_no}` removes that stop and
  re-numbers the remaining stops to stay contiguous (`db.flush()` keeps the
  resequence UPDATE from matching the row it's about to delete). Removing a
  stop from a 1-stop route is rejected (a route needs both an end and — via
  the reverse leg or a second stop — a loop anchor, so `networkx` can route
  through it).
- `PATCH /routes/{route_id}/stops/order` takes `{"sequence": [1, 2, ...]}`
  — a permutation of the route's *current `sequence_no` values*, not stop
  ids — because loop routes legitimately visit the same stop more than once.
  Reorder applies with a two-pass offset (`+1_000_000` park, then final) so
  the `sequence_no > 0` CHECK constraint isn't violated mid-update.
- Both, plus `POST /routes/{route_id}/stops`'s append, update
  `routes.total_stops`.
- To *insert* a stop in the middle, append it — `POST /routes/{route_id}/stops`
  with `sequence_no = max + 1` — then reorder; the add endpoint deliberately
  refuses to shift existing rows (add always appends or hits the existing 409
  on duplicate `sequence_no`).

`routes.operator_id` can legitimately be `NULL` — this isn't a data bug.
Some routes are run by informal/unregistered local microbus services with
no known formal operator; for these, `operator_id` is left null while the
free-text `operator` column (e.g. "Local Microbus") still describes who
runs it. Don't assume a null `operator_id` means missing data that needs
fixing — check `operator` first before treating it as an issue.

## Traffic congestion

`GET /route-finder` records a background sample (day-of-week + 3-hour Nepal-time
bucket, duration/distance from OSRM) for every ride leg that got real road
geometry, upserting into `segment_congestion_stats`. `GET /congestion`
(optionally with `day_of_week`/`hour` query params, defaulting to "now") reads
those aggregates back and classifies each segment as `free_flow`, `moderate`,
or `heavy` based on `avg_duration_s / free_flow_duration_s`. `GET
/congestion/buckets` exposes the fixed set of valid hour buckets. The table
can also be pre-populated with `scripts/seed_congestion_stats.py` — seeded
rows are flagged `is_seeded=true` so real samples aren't confused with them.
See `app/api/congestion.py` and `app/routing/time_buckets.py`.

These aggregates also feed routing itself: `GET /route-finder`'s
`avoid_congestion` flag weights the transfer-search fallback's ride
edges by current historical congestion instead of raw distance alone
(direct routes are unaffected — a direct route always wins regardless).
See `app/routing/graph_builder.py`'s congestion/duration weight
functions and `tests/test_congestion_weight_fn.py` /
`tests/test_duration_weight_fn.py` / `tests/test_congestion_zones.py`.

## Stop positioning (stop markers onto route geometry)

`GET /routes/{route_id}/stops` places each stop onto its direction's road
geometry before returning it. `app/routing/stop_positioning.py` walks each
direction's stop sequence **forward along the route's LineString — never
backward** — and projects each stop onto the closest point of the segment the
route is actually on:

- **Monotonic cursor** (`MONOTONIC_POSITION_TOLERANCE_M = 5.0`): the
  projection point may only advance along the path, so a crossing street or a
  parallel carriageway can't steal a stop, and nothing snaps backward onto an
  earlier part of a route that loops and passes close by again later.
- **Closed-loop / ring-road anchor** (`FIRST_STOP_TIE_EPS_M = 10.0`): when a
  route's geometry end and start coincide — the fingerprint of a closed loop —
  the first stop is anchored at the true start of travel instead of the
  (roughly equidistant) end. Without it, the cursor jumps to the far end of
  the polyline and later stops fall back; measured 94% of a 36-stop ring-road
  loop (R3351751) stranded on canonical coordinates before the fix.
- **Canonical fallback** (`MAX_STOP_OFFSET_M = 100.0`): a stop whose
  projection lands more than this from its canonical coordinate isn't
  considered on the route and keeps its canonical `lat`/`lng`. The adjusted
  position is returned per stop as `display_lat`/`display_lng` (nullable);
  the frontend prefers it when present (else falls back to `lat`/`lng`).

Related, in `app/api/routing.py`: `WAYPOINT_SNAP_RADIUS_M = 150` bounds how
far OSRM may snap waypoints (per-waypoint `radiuses`) when computing the
route geometry itself — distinct from the post-hoc projection here.

Diagnostics and regression coverage:
- `scripts/validate_stop_positioning.py` — re-runs the placement over every
  route and reports projection/fallback counts and per-stop offsets.
- `scripts/replay_old_matcher.py` — replays the pre-fix matching logic for
  before/after comparisons.
- `tests/test_stop_positioning_adversarial.py` — pins the edge cases above.

## Running tests

```bash
pytest -v
```

Some tests in `tests/` (e.g. `test_stops.py`) require a live database and will skip cleanly if one isn't reachable — make sure `docker compose up -d db` has been run first and migrations are applied, or those tests will just no-op.

Routing/pathfinder unit tests live in `tests/test_routing.py` and
`tests/test_pathfinder_alternatives.py` (route alternatives: `alternate_direct_route`,
`shortest_distance`, `fastest_estimated`). `tests/test_congestion_weight_fn.py`,
`tests/test_duration_weight_fn.py`, and `tests/test_congestion_zones.py` cover the
edge-weighting functions behind `avoid_congestion` and the `fastest_estimated`
alternative. `tests/test_stop_positioning_adversarial.py` pins down the
stop-placement edge cases -- closed-loop / ring-road routes (Baneshwor stop
tie breaks), snap-radius-constrained OSRM failures, and monotonic-cursor
regressions -- so the fallback heuristics can't silently regress.

`tests/test_admin_auth_api.py`, `tests/test_fare_api.py`, and `tests/test_admin_crud_api.py` cover the admin-auth login flow (success, wrong password, unknown username, timing-safe error parity, and the 5/minute rate limit actually tripping), `GET /fare` band matching (inclusive-min/exclusive-max boundaries, 404 with no covering band), and the admin data-entry endpoints (`POST /stops`, `POST /routes`, `POST /routes/{id}/stops`, `DELETE /routes/{id}/stops/{seq}`, `PATCH /routes/{id}/stops/order` — auth enforcement, 404s on unknown references, the 409 on duplicate `sequence_no`, resequencing stays contiguous, reorder accepts any `sequence_no` permutation, and `graph_meta.version` bumping). All three create and tear down their own fixtures, so they don't depend on the shipped dataset like `test_stops.py` does.

`tests/test_stops_api.py`, `tests/test_route_finder_api.py`, and
`tests/test_route_geometry_api.py` are DB-backed integration tests for
`GET /stops`/`GET /stops/{stop_id}`, `GET /route-finder` (including
`avoid_congestion`/`include_alternatives`), and `GET /routes/{route_id}/geometry`
respectively — same live-database caveat as `test_stops.py` above.

## Inspecting the live database

Useful for debugging model/schema mismatches:

```bash
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder -c "\d <table_name>"
```

Or drop into an interactive session:

```bash
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder
```

## Database migrations

Schema changes are tracked with Alembic (`migrations/`).

```bash
alembic upgrade head                     # apply all migrations
alembic revision -m "add fare column"    # create a new migration
alembic downgrade -1                     # roll back one step
```

**Important:** SQLAlchemy models in `app/models/` are hand-written and not auto-generated from migrations. After writing or applying a migration that changes a table, manually update the corresponding model file to match — column names, types, and nullability must match exactly, or you'll get failures that only show up at query time rather than at import time. Cross-check with:

```bash
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder -c "\d <table_name>"
```

## Folder structure

```
backend/
├── app/
│   ├── api/            FastAPI route handlers (stops, routes, routing,
│   │                     fare, congestion, admin, admin_auth)
│   ├── core/            Config, security (shared-key + JWT auth), rate_limit (slowapi Limiter shared across endpoints)
│   ├── db/               Session, queries, id_generator, base
│   ├── models/          SQLAlchemy ORM models (hand-synced with migrations — see above; includes graph_meta, the routing-graph cache version counter)
│   ├── routing/          NetworkX routing logic (graph_builder, pathfinder,
│   │                     constants, osrm_client, time_buckets)
│   └── main.py
├── migrations/          Alembic migration scripts
├── scripts/              Admin/ops scripts (seed_admin.py, seed_congestion_stats.py,
│                           seed_demo_congestion.py, compute_osrm_route_distances.py),
│                           stop-placement diagnostics (validate_stop_positioning.py,
│                           replay_old_matcher.py) +
│                           prepare_osrm_data.sh (idempotent OSRM car+foot extract, see Setup)
├── tests/                DB-backed integration tests + routing unit tests
├── .env.example
├── alembic.ini
├── Dockerfile
├── requirements.txt          Runtime deps only -- what the Docker image installs
└── requirements-dev.txt      + pytest/httpx, for running tests locally
```
