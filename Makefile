# Kathmandu Bus Route Finder -- setup orchestration.
#
# Wires together the existing pieces documented across data/scripts/README.md
# and backend/README.md into one command per stage (and one `make setup` for
# all of them, in order). Does not change the DB schema, Alembic migrations,
# or any app code -- every target below just calls the scripts/tools that
# already exist. Targets are safe to re-run individually.
#
# First-time setup:  make setup
# Day-to-day:         make up        (build + start db + osrm + backend)
#                      make down      (stop everything)

SHELL := /bin/bash
COMPOSE := docker compose
DATA_SCRIPTS := data/scripts
PROCESSED_DIR := data/processed

.PHONY: setup data validate db-up backend-env migrate import seed-admin osrm osrm-up backend-build up down logs

## Full first-time bootstrap, in dependency order (data -> validate -> db ->
## migrate -> import -> OSRM prep + up).
setup: data validate db-up backend-env migrate import osrm osrm-up
	@echo ""
	@echo "Core stack is up. Run 'make seed-admin' to create the first admin login,"
	@echo "then 'make up' to build + start the backend."

## Create backend/.env from .env.example on first run, with real generated
## ADMIN_API_KEY/JWT_SECRET_KEY values. Never overwrites an existing file.
backend-env:
	python3 scripts/gen_backend_env.py

## Clean + validate the raw CSVs into data/processed/.
data:
	pip install -q -r $(DATA_SCRIPTS)/requirements.txt
	python $(DATA_SCRIPTS)/clean_data.py --raw-dir data/raw --out-dir $(PROCESSED_DIR)

## Validate the cleaned CSVs (integrity/consistency checks run in CI too).
validate:
	python $(DATA_SCRIPTS)/validate_clean.py --dir $(PROCESSED_DIR)

## Start Postgres/PostGIS only (needed before migrate/import).
db-up:
	$(COMPOSE) up -d db

## Apply Alembic migrations inside the backend image -- no host venv needed.
migrate: db-up
	$(COMPOSE) run --rm --no-deps backend alembic upgrade head

## Load data/processed/*_clean.csv into the DB (no path-editing required).
import: migrate backend-env
	pip install -q -r $(DATA_SCRIPTS)/requirements.txt
	python $(DATA_SCRIPTS)/import_data.py --processed-dir $(PROCESSED_DIR)

## Create the first admin account (interactive).
seed-admin: db-up
	$(COMPOSE) run --rm --no-deps backend python3 -m scripts.seed_admin

## One-time, idempotent OSRM data prep (car + foot profiles).
## Optional: /route-finder works without it, just with road_geometry: null.
osrm:
	backend/scripts/prepare_osrm_data.sh

## Bring up the OSRM routers once their .osrm files exist.
osrm-up: osrm
	$(COMPOSE) up -d osrm osrm-foot

## Build the backend Docker image (installs baked-in pip deps).
backend-build:
	$(COMPOSE) build backend

## Start the full stack (db, osrm, osrm-foot, backend). Builds the backend
## image first so local pip deps are actually baked in (the bind-mounted
## app code is what runs, but requirements come from the image).
## Picks up the dev --reload overlay via docker-compose.override.yml.
up: backend-env osrm backend-build
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f
