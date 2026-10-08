# AGENTS.md

## Architecture

- **Backend**: FastAPI (Python 3.11) + NetworkX routing + PostGIS spatial queries
- **Frontend**: Next.js 16 (App Router) + React 18 + TypeScript + Leaflet.js
- **Database**: PostgreSQL 15 + PostGIS 3.4 (via `postgis/postgis:15-3.4`)
- **Routing**: NetworkX Dijkstra with OSRM road geometry enrichment (optional)
- **Caching**: Two-tier TTL cache (`app/core/response_cache.py`) — Redis (when `REDIS_URL` is set, shared across workers) with in-memory fallback (always present, LRU-evicted) — wired into `/stops`, `/routes`, `/congestion`; admin writes invalidate the relevant namespace

## Key Entry Points

- `backend/app/main.py` — FastAPI app startup, router registration
- `backend/app/routing/pathfinder.py` — Core routing algorithm (direct route check → NetworkX Dijkstra fallback)
- `backend/app/routing/graph_builder.py` — NetworkX graph construction with caching via `graph_meta.version`
- `backend/app/routing/stop_positioning.py` — stop→geometry placement heuristics (projection w/ canonical fallback, monotonic cursor, loop-route anchor)
- `backend/app/api/admin.py` — admin data-entry endpoints (create stop/route, add/remove/reorder route stops, route status, graph reload); auth via `backend/app/core/security.py` (`require_admin`, which resolves a *principal* from an admin JWT, a scoped service credential (`Authorization: SvcKey <id>.<secret>`), or — only when `ALLOW_LEGACY_SHARED_ADMIN_KEY=true` — the legacy `X-Admin-Api-Key` shared key). Every guard checks the principal and a specific permission, never a raw header.
- `backend/app/api/service_credentials.py` — mint/list/revoke scoped service credentials and read the audit log; human-admin only
- `backend/app/core/admin_audit.py` — audit writes; successful mutations commit in the same transaction as the change, security events use a separate best-effort session
- `frontend/app/admin/page.tsx` — browser admin UI (/admin) backed by `frontend/lib/adminApi.ts` (JWT in localStorage); forms live in `frontend/components/admin/`
- `frontend/app/page.tsx` — Main search UI composition
- `backend/app/core/response_cache.py` — response caching layer; check here before assuming an endpoint hits the DB/graph directly
- `backend/app/core/config.py` — `Settings` + `validate_production_settings()`. Secret-bearing fields are `repr=False` so a Settings object can be printed in a traceback without disclosing passwords; do not remove that. Any new setting that production must have has to be added to the validator, and `backend/tests/test_config.py` updated to match.
- `scripts/check_prod_env.py` — runs the real production validator against the env `docker compose` resolves for the backend; run by `make prod-check`
- `scripts/gen_backend_env.py` / `scripts/check_env.py` — env generation (mode 600, no secret rotation on existing files) and the preflight that reports drift

## Development Commands

### Backend
```bash
cd ..
make env            # NOT `cp .env.example .env` -- generates real secrets, mode 600
cd backend
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload  # http://localhost:8000/docs
```

`make env` (from the repo root) is required before anything else. It creates
both `.env` and `backend/.env` with real random `POSTGRES_PASSWORD`,
`REDIS_PASSWORD`, `ADMIN_API_KEY` and `JWT_SECRET_KEY`, sets mode 600, and
keeps `backend/.env`'s `DATABASE_URL` password in sync with the root
`.env`. A copied `.env.example` keeps its `change_me_in_production`
placeholders, which the app refuses in production.

Run the tests with `make test` from the repo root — see the Testing Strategy
section for why not to point pytest at the dataset database.

### Frontend
```bash
cd frontend
npm install
npm run dev  # http://localhost:3000
npm test  # Vitest + React Testing Library
npm run lint  # ESLint, with jsx-a11y rules as errors
```

`npm run lint` enforces accessibility rules as **errors** (labelled
controls, keyboard handlers on click targets, focusable interactive
elements, `alt` text, ARIA correctness). Do not silence one to make lint
pass; fix the markup, or — if the rule is wrong — add a comment explaining
which probe showed it misfiring, the way `frontend/eslint.config.mjs`
documents `control-has-associated-label`.

### Full Stack (Makefile)
```bash
make setup  # One-time: data clean + validate, db up, migrate, import, OSRM prep
make up     # Build backend image + start full stack (db + osrm + backend)
make down   # Stop everything
make seed-admin  # Create first admin login
```

## Critical Gotchas

1. **Dev `--reload` lives in the compose overlay, not the image**: the backend Dockerfile runs a stable uvicorn; `docker-compose.override.yml` (auto-loaded by `docker compose`/`make up`) adds `--reload`. Logs full of "Reloading process"/"Started server process" under `backend/` are the dev hot-reloader reacting to file writes (incl. tests/scripts you create), not a crash loop. Deployments that exclude the override don't reload.
2. **`make up` builds before starting**: `up` re-runs `docker compose build backend` so pip deps baked into the image stay current (app code is bind-mounted in dev). `make setup` now **validates** the cleaned CSVs (`validate_clean.py`) before importing.
3. **Database URL Context**: `DATABASE_URL` uses `localhost` for host dev, `db` service name for Docker. Check `.env` vs `docker-compose.yml`.
4. **Migration Discipline**: Only ONE migration chain exists. Coordinate before adding migrations. After pulling new migrations: `cd backend && alembic upgrade head`.
5. **SQLAlchemy Models Are Hand-Written**: Models in `backend/app/models/` are NOT auto-generated from migrations. When adding/changing columns, update both migration AND model file manually. Verify with: `docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder -c "\d <table>"`
6. **Graph Cache Invalidation**: The in-memory NetworkX graph caches via `graph_meta.version`. Admin writes bump this version; each process rebuilds on next request. Don't bypass this mechanism.
7. **Test Dependencies**: Backend integration tests require a live database (`docker compose up -d db` + `alembic upgrade head`). Unit tests for routing work without DB. CI loads the full dataset from `data/processed/*_clean.csv`.
8. **Data Pipeline**: Raw CSVs → `data/scripts/clean_data.py` → `data/processed/*_clean.csv` → `data/scripts/import_data.py` → PostgreSQL. Never edit processed CSVs directly.
9. **OSRM Is Optional**: Backend works without OSRM (returns `road_geometry: null`). Setup: `make osrm` (one-time extract) + `make osrm-up` or `docker compose up -d osrm osrm-foot`.
10. **Congestion & Alternatives Are Implemented, Not Planned**: `avoid_congestion` and `include_alternatives` (up to 2 alternate paths) are live in `pathfinder.py`/`api/routing.py` — don't reimplement or assume they're TODOs.
11. **The backend container needs `cap_add: DAC_OVERRIDE`, on purpose**: `backend/.env` is mode 0600 and the dev override bind-mounts `backend/`, so `/app/.env` exists in the container owned by the host user. `cap_drop: ALL` removes `CAP_DAC_OVERRIDE`, so root cannot read it, and both pydantic-settings and slowapi raise `PermissionError` during import — the service dies at its health check and nginx returns 504 for the whole stack. Don't "tidy" that capability away, and don't chmod the env file more open to compensate. Production does not bind-mount, so it never opens the file.
12. **`redis_client` and `rate_limit` must read Settings, not `os.getenv`**: pydantic loads `backend/.env` into the Settings object without copying it into `os.environ`, so `os.getenv("REDIS_URL")` silently ignores the file. This has already caused one bug; `backend/tests/test_config.py` locks it down. `Limiter.app_config` is also replaced with a file-less `Config` for the same reason.
13. **`docker compose` loads the dev override automatically; production must name its files**: `docker compose up` picks up `docker-compose.override.yml` (loopback ports, bind mount, `--reload`) without being asked. Production is `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` (`make up-prod`). Running bare `docker compose` in production publishes the development shape.
14. **A production backend that would refuse to start is a config bug, not a deploy surprise**: `make prod-check` runs `validate_production_settings()` against the environment Compose actually resolves, via `scripts/check_prod_env.py`. If you add a setting that production must have, add it to the validator; if you change `TRUST_PROXY_HEADERS` or the uvicorn flags, keep `UVICORN_ARGS` in `docker-compose.prod.yml` in sync with the `command:` in the same file.

## Testing Strategy

- **Backend unit tests**: `tests/test_routing.py`, `tests/test_pathfinder_alternatives.py`, `tests/test_stop_positioning_adversarial.py` (no DB needed)
- **Backend integration tests**: `tests/test_stops_api.py`, `tests/test_route_finder_api.py` (need live DB)
- **Admin API tests**: Self-contained fixtures (create/teardown per test)
- **Security regression tests**: `tests/test_privileged_routes_are_gated.py` (structural + behavioural audit of every privileged route), `tests/test_service_credentials_api.py`, `tests/test_admin_roles.py`, `tests/test_config.py`. When you add a privileged endpoint, extend the structural test — it fails if a new mutating route is ungated.
- **Frontend**: `lib/` (pure helpers) + `hooks/` (mocked API)
- **Data pipeline**: `data/scripts/test_clean_data.py` + full pipeline validation in CI

### Never run the suite against the dataset database

`make test-backend` targets the disposable `ktm_bus_sectest` and **refuses** to
run against `ktm_bus_route_finder`, which `make up` loads with the real
dataset. The integration tests create and drop stops, routes, service
credentials and audit rows. A failed or interrupted run against the dataset
database leaves it mutated with no record of what changed.

Set it up once:

```bash
docker compose up -d db redis
docker exec ktm_bus_db psql -U ktm_bus -d postgres -c "CREATE DATABASE ktm_bus_sectest"
cd backend && DATABASE_URL="postgresql://ktm_bus:$POSTGRES_PASSWORD@localhost:5432/ktm_bus_sectest" alembic upgrade head
```

## Environment Variables

Required in `backend/.env` (generated by `make env`; mode 600, gitignored):
- `DATABASE_URL` — PostgreSQL connection string. Password must equal the root `.env`'s `POSTGRES_PASSWORD`; `make env` syncs it and `scripts/check_env.py` reports drift.
- `JWT_SECRET_KEY` — Signs admin login JWTs
- `ADMIN_API_KEY` — Legacy shared key, consulted only when `ALLOW_LEGACY_SHARED_ADMIN_KEY=true` (refused in production). Still must be a real value.

Required in the root `.env` (read by Compose, not the app):
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD` — Compose refuses to start without them
- `PUBLIC_HOST`, `PUBLIC_CORS_ORIGINS` — required by `docker-compose.prod.yml` via `:?`; must be the real hostname and an `https://` origin

Optional:
- `REDIS_URL` — shared response cache + rate-limit store. **Production refuses to start without it**: with no shared store the login rate limit is per-worker, so a multi-worker deploy multiplies an attacker's guesses by the worker count.
- `RATE_LIMIT_REDIS_URL` — put rate-limit counters on a different Redis DB than the cache

- `OSRM_BASE_URL` (default `http://localhost:5000`) — Driving geometry
- `OSRM_FOOT_BASE_URL` (default `http://localhost:5001`) — Walking geometry
- `TRUST_PROXY_HEADERS` — only `true` when the sole path to the backend is through nginx

## Documentation That Must Stay True

These four files make claims that are easy to falsify by changing the code.
Update them in the same change that makes them wrong:

- `README.md` — auth model, env vars, deployment, testing.
- `PRIVACY.md` — what personal data is held. If you add analytics, cookies,
  session storage, or a new table holding identifiers, update it; the
  no-cookie and no-tracking claims are only true while nothing adds them.
- `data/LICENSING.md` — the code is MIT but the data is ODbL. Do not relicense
  the data, and do not describe fares as authoritative; they are desk
  estimates.
- `frontend/ACCESSIBILITY.md` — the honest accessibility state, including the
  known gaps. Do not let a UI change quietly invalidate a "known gaps" entry,
  and do not describe the app as WCAG-conformant; it has not been audited.

## Conventions

- Branch naming: `<scrum-number>/<short-description>` (e.g., `scrum-3/dedup-stops`)
- Commit messages: Prefix with Jira key if linking (e.g., `KTM-14: add dedup script`)
- PRs target `main`, squash-merge preferred
- `stop_id`/`route_id` are server-generated (S####, M######, R-numbered)
- `routes.operator_id` can be NULL (informal operators) — check `operator` text column first

## Data Ownership (CONTRIBUTING.md)

- **Dipesh**: `backend/app/db/`, `backend/migrations/`, `data/` (schema, pipeline, spatial queries)
- **Janak**: `backend/app/api/`, `backend/app/core/` (graph engine, API endpoints)
- **Dinesh**: `frontend/` (UI, map, Leaflet/OSRM)

## CI Pipeline (`.github/workflows/ci.yml`)

Four parallel jobs on every PR:
1. **backend-tests**: PostGIS container → `alembic upgrade head` → `import_data.py` → `pytest`
2. **data-pipeline-tests**: Python 3.12 → `pytest test_clean_data.py` → full pipeline validation
3. **frontend-checks**: Node 22 → `npm install` → `npm run lint` → `npm test` → `npm run build`

## Quick Reference

```bash
# Inspect live database
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder

# Force graph rebuild
# Prefer a scoped service credential over the legacy shared key.
curl -X POST http://localhost:8000/graph/reload \
  -H "Authorization: SvcKey <key_id>.<secret>"   # needs the graph:reload permission

# Check graph version
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder -c "SELECT version FROM graph_meta"
```
