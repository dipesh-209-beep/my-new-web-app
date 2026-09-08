# AGENTS.md

## Architecture

- **Backend**: FastAPI (Python 3.11) + NetworkX routing + PostGIS spatial queries
- **Frontend**: Next.js 16 (App Router) + React 18 + TypeScript + Leaflet.js
- **Database**: PostgreSQL 15 + PostGIS 3.4 (via `postgis/postgis:15-3.4`)
- **Routing**: NetworkX Dijkstra with OSRM road geometry enrichment (optional)
- **Caching**: In-process TTL cache (`app/core/response_cache.py`) — no Redis — wired into `/stops`, `/routes`, `/congestion`; admin writes invalidate the relevant namespace

## Key Entry Points

- `backend/app/main.py` — FastAPI app startup, router registration
- `backend/app/routing/pathfinder.py` — Core routing algorithm (direct route check → NetworkX Dijkstra fallback)
- `backend/app/routing/graph_builder.py` — NetworkX graph construction with caching via `graph_meta.version`
- `frontend/app/page.tsx` — Main search UI composition
- `backend/app/core/response_cache.py` — response caching layer; check here before assuming an endpoint hits the DB/graph directly

## Development Commands

### Backend
```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env  # Generate real ADMIN_API_KEY/JWT_SECRET_KEY
alembic upgrade head
uvicorn app.main:app --reload  # http://localhost:8000/docs
pytest -v  # Requires: docker compose up -d db
```

### Frontend
```bash
cd frontend
npm install
npm run dev  # http://localhost:3000
npm test  # Vitest + React Testing Library
npm run lint  # ESLint
```

### Full Stack (Makefile)
```bash
make setup  # One-time: data clean, db up, migrate, import, OSRM prep
make up     # Start full stack (db + osrm + backend)
make down   # Stop everything
make seed-admin  # Create first admin login
```

## Critical Gotchas

1. **Database URL Context**: `DATABASE_URL` uses `localhost` for host dev, `db` service name for Docker. Check `.env` vs `docker-compose.yml`.

2. **Migration Discipline**: Only ONE migration chain exists. Coordinate before adding migrations. After pulling new migrations: `cd backend && alembic upgrade head`.

3. **SQLAlchemy Models Are Hand-Written**: Models in `backend/app/models/` are NOT auto-generated from migrations. When adding/changing columns, update both migration AND model file manually. Verify with: `docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder -c "\d <table>"`

4. **Graph Cache Invalidation**: The in-memory NetworkX graph caches via `graph_meta.version`. Admin writes bump this version; each process rebuilds on next request. Don't bypass this mechanism.

5. **Test Dependencies**: Backend integration tests require a live database (`docker compose up -d db` + `alembic upgrade head`). Unit tests for routing work without DB. CI loads the full dataset from `data/processed/*_clean.csv`.

6. **Data Pipeline**: Raw CSVs → `data/scripts/clean_data.py` → `data/processed/*_clean.csv` → `data/scripts/import_data.py` → PostgreSQL. Never edit processed CSVs directly.

7. **OSRM Is Optional**: Backend works without OSRM (returns `road_geometry: null`). Setup: `make osrm` (one-time extract) + `make osrm-up` or `docker compose up -d osrm osrm-foot`.

8. **Congestion & Alternatives Are Implemented, Not Planned**: `avoid_congestion` and `include_alternatives` (up to 2 alternate paths) are live in `pathfinder.py`/`api/routing.py` — don't reimplement or assume they're TODOs.

## Testing Strategy

- **Backend unit tests**: `tests/test_routing.py`, `tests/test_pathfinder_alternatives.py` (no DB needed)
- **Backend integration tests**: `tests/test_stops_api.py`, `tests/test_route_finder_api.py` (need live DB)
- **Admin API tests**: Self-contained fixtures (create/teardown per test)
- **Frontend**: `lib/` (pure helpers) + `hooks/` (mocked API)
- **Data pipeline**: `data/scripts/test_clean_data.py` + full pipeline validation in CI

## Environment Variables

Required in `backend/.env`:
- `DATABASE_URL` — PostgreSQL connection string
- `ADMIN_API_KEY` — Shared secret for data-entry endpoints
- `JWT_SECRET_KEY` — Signs admin login JWTs

Optional:
- `OSRM_BASE_URL` (default `http://localhost:5000`) — Driving geometry
- `OSRM_FOOT_BASE_URL` (default `http://localhost:5001`) — Walking geometry

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

Three parallel jobs on every PR:
1. **backend-tests**: PostGIS container → `alembic upgrade head` → `import_data.py` → `pytest`
2. **data-pipeline-tests**: Python 3.12 → `pytest test_clean_data.py` → full pipeline validation
3. **frontend-checks**: Node 22 → `npm install` → `npm run lint` → `npm test` → `npm run build`

## Quick Reference

```bash
# Inspect live database
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder

# Force graph rebuild
curl -X POST http://localhost:8000/graph/reload -H "X-Admin-Api-Key: <key>"

# Check graph version
docker exec -it ktm_bus_db psql -U ktm_bus -d ktm_bus_route_finder -c "SELECT version FROM graph_meta"
```
