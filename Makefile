# Kathmandu Bus Route Finder -- setup and operations.
#
# Wires together the existing pieces documented across data/scripts/README.md
# and backend/README.md into one command per stage (and one `make setup` for
# all of them, in order). Targets are safe to re-run individually.
#
# First-time setup:  make setup
# Day-to-day:         make up        (build + start the dev stack)
#                     make down      (stop everything)
#                     make test      (backend suite against the running db)
# Production:         make prod-check   (preflight, changes nothing)
#                     make up-prod      (base + prod, no dev overlay)
#                     make backup       (verified, restorable dump)
#
# Which compose files are in play:
#   make up / down      docker compose            -> base + override (dev)
#   make up-prod        docker compose -f base -f prod
# The dev override is auto-loaded by plain `docker compose`, and explicit
# -f flags suppress it, so `up-prod` gets the hardened base with no dev
# ports, no bind mount, and no --reload even though the override file is
# sitting right there.

SHELL := /bin/bash
COMPOSE := docker compose
# Production: explicit -f list, so docker-compose.override.yml is ignored.
PROD_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.prod.yml
DATA_SCRIPTS := data/scripts
PROCESSED_DIR := data/processed
SCRIPTS := scripts

# Root .env holds POSTGRES_PASSWORD / REDIS_PASSWORD, which docker-compose
# interpolates and which the backup scripts read. Sourced (not passed by
# make) so a value containing '#' or spaces survives.
ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: setup data validate env backend-env db-up backend-env-check migrate import \
        seed-admin osrm osrm-up backend-build up down logs psql \
        test test-backend test-frontend lint frontend-install \
        nginx-check prod-check up-prod down-prod backup restore-backup \
        certs renew-certs

## ---------------------------------------------------------------------------
## First-time setup
## ---------------------------------------------------------------------------

## Full first-time bootstrap, in dependency order. `env` comes first because
## docker-compose.yml refuses to start without POSTGRES_PASSWORD and
## REDIS_PASSWORD -- generating them has to happen before the db does, not
## after.
setup: env data validate db-up migrate import osrm osrm-up
	@echo ""
	@echo "Core stack is up. Run 'make seed-admin' to create the first admin login,"
	@echo "then 'make up' to build + start the backend."
	@echo ""
	@echo "Admin credentials: mint a scoped one via the API (POST /admin/login,"
	@echo "then POST /admin/service-credentials). The X-Admin-Api-Key shared"
	@echo "secret is disabled by default and stays disabled in production."

## Create backend/.env and the root .env from their .example templates, with
## real generated secrets. Never overwrites an existing file, and warns if an
## existing one still holds a placeholder.
env backend-env:
	python3 $(SCRIPTS)/gen_backend_env.py

## Fail if either .env is missing a usable secret. Separate from `env` so it
## can be a CI step and a pre-deploy check.
backend-env-check:
	@python3 $(SCRIPTS)/gen_backend_env.py >/dev/null
	@python3 $(SCRIPTS)/check_env.py

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
import: migrate env
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

## ---------------------------------------------------------------------------
## Development
## ---------------------------------------------------------------------------

## Build the backend Docker image (installs baked-in pip deps).
backend-build:
	$(COMPOSE) build backend

## Start the full dev stack. Builds the backend image first so local pip deps
## are actually baked in (the bind-mounted app code is what runs, but
## requirements come from the image). Picks up the dev overlay.
up: env osrm backend-build
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

## psql against the dev database, without needing psql installed locally.
psql:
	$(COMPOSE) exec db psql -U ktm_bus -d ktm_bus_route_finder

## ---------------------------------------------------------------------------
## Tests
## ---------------------------------------------------------------------------

## Full backend suite.
##
## Runs against ktm_bus_sectest, NOT the ktm_bus_route_finder that `make up`
## loads with the real dataset. The integration tests create and drop rows --
## stops, routes, service credentials, audit entries -- and pointing them at
## the dev database means a failed or interrupted run leaves that data
## mutated, with no record of what changed. A dedicated database is the
## difference between a test run and an incident. Override with
## TEST_DATABASE_URL if you need a different one; set it to the dev database
## only deliberately.
##
## It also needs the dev stack up (`make up`) for the Postgres and Redis
## ports, and the disposable database to exist and be migrated:
##   docker exec -it ktm_bus_db psql -U ktm_bus -d postgres \
##     -c "CREATE DATABASE ktm_bus_sectest"
##   cd backend && DATABASE_URL=.../ktm_bus_sectest alembic upgrade head
##
## REDIS_URL is exported from the root .env because the compose Redis runs
## with a password (--requirepass) and the cache tests would otherwise skip
## -- silently, which is exactly the failure mode that hides bugs.
##
## The interpreter is backend/venv/bin/python, not `python3`. The system
## interpreter has no fastapi/networkx/alembic, so `python3 -m pytest` fails
## at collection with an ImportError that looks like a broken repo rather
## than a missing venv. The guard below says which it is.
test: test-backend

test-backend:
	@test -f .env || { echo "missing .env -- run 'make env'" >&2; exit 1; }
	@set -a; . ./.env; set +a; \
	 py=backend/venv/bin/python; \
	 if [ ! -x "$$py" ]; then \
	   echo "missing $$py -- create it and 'pip install -r requirements.txt -r requirements-dev.txt'" >&2; \
	   exit 1; \
	 fi; \
	 db="$${TEST_DATABASE_URL:-postgresql://ktm_bus:$$POSTGRES_PASSWORD@localhost:5432/ktm_bus_sectest}"; \
	 case "$$db" in *ktm_bus_route_finder) \
	   echo "refusing to run the suite against the dev dataset database ($$db)." >&2; \
	   echo "set TEST_DATABASE_URL to a disposable database, e.g. ktm_bus_sectest." >&2; \
	   exit 1 ;; \
	 esac; \
	 echo "backend tests -> $$db"; \
	 cd backend && \
	 DATABASE_URL="$$db" \
	 REDIS_URL="redis://:$$REDIS_PASSWORD@localhost:6379/0" \
	 "$$OLDPWD/$$py" -m pytest -v

## Frontend lint + tests + production build. No .env needed (Next.js reads
## NEXT_PUBLIC_* at build time, not from this stack).
test-frontend:
	cd frontend && npm run lint && npm test && npm run build

frontend-install:
	cd frontend && npm ci

## ---------------------------------------------------------------------------
## Production
## ---------------------------------------------------------------------------

## Check both nginx configs parse. Catches a broken proxy config before it
## takes the ingress down -- and needs no certificates, because the HTTPS
## config is validated against a throwaway self-signed pair in a temp dir,
## never in deploy/certs/.
nginx-check:
	@set -e; \
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
	    -keyout "$$tmp/privkey.pem" -out "$$tmp/fullchain.pem" >/dev/null 2>&1; \
	for conf in deploy/nginx.conf deploy/nginx.https.conf; do \
	  printf '%-28s ' "$$conf"; \
	  docker run --rm --add-host backend:127.0.0.1 \
	    -v "$$PWD/$$conf:/etc/nginx/nginx.conf:ro" \
	    -v "$$PWD/deploy/proxy_params.conf:/etc/nginx/proxy_params.conf:ro" \
	    -v "$$tmp:/etc/nginx/certs:ro" \
	    nginx:1.27-alpine nginx -t 2>&1 | grep -q "test is successful" \
	    && echo ok || { echo FAILED; exit 1; }; \
	done

## Pre-deploy preflight. Read-only: starts nothing, writes nothing, changes
## nothing. Prints the resolved production compose config, verifies the nginx
## configs, checks the env files have real secrets, and fails if the hardened
## base would publish anything but the ingress.
prod-check: backend-env-check
	@echo "==> compose config (production overlays)"
	@$(PROD_COMPOSE) config --quiet && echo "    compose config ok"
	@published=$$($(PROD_COMPOSE) config 2>/dev/null \
	    | grep -E '^\s+published:' | tr -dc '0-9\n' | sort -un | tr '\n' ' '); \
	 echo "    published host ports: $${published:-none}"; \
	 if [ "$$published" != "80 443 " ] && [ "$$published" != "443 80 " ] && [ -n "$$published" ]; then \
	   echo "    ERROR: expected only 80 and 443 published, got: $$published" >&2; \
	   echo "    A service in docker-compose.yml has a ports: block that belongs" >&2; \
	   echo "    in docker-compose.override.yml." >&2; \
	   exit 1; \
	 fi
	@echo "==> backend production settings (real validator, resolved prod env)"
	@./backend/venv/bin/python scripts/check_prod_env.py || exit 1
	@echo "==> nginx"
	@$(MAKE) --no-print-directory nginx-check
	@echo "==> host identity"
	@set -a; . ./.env; set +a; \
	 case "$$PUBLIC_HOST" in \
	   ""|change_me*|*example.com|*example.org|*example.net|localhost|127.0.0.1) \
	     echo "    ERROR: PUBLIC_HOST=$${PUBLIC_HOST:-<unset>} is not a real hostname." >&2; \
	     echo "    It reaches the backend as TRUSTED_HOSTS, so every request" >&2; \
	     echo "    with a real Host header would be rejected by TrustedHostMiddleware." >&2; \
	     echo "    Set PUBLIC_HOST and PUBLIC_CORS_ORIGINS in .env." >&2; \
	     exit 1 ;; \
	 esac; \
	 case "$$PUBLIC_CORS_ORIGINS" in \
	   ""|change_me*|*example.com|*example.org|*example.net) \
	     echo "    ERROR: PUBLIC_CORS_ORIGINS=$${PUBLIC_CORS_ORIGINS:-<unset>} is still the template value." >&2; \
	     echo "    Set it to the exact https:// origin the frontend is served from." >&2; \
	     exit 1 ;; \
	 esac; \
	 echo "    PUBLIC_HOST=$$PUBLIC_HOST  PUBLIC_CORS_ORIGINS=$$PUBLIC_CORS_ORIGINS"
	@echo "==> certificates"
	@if [ -f deploy/certs/fullchain.pem ] && [ -f deploy/certs/privkey.pem ]; then \
	   openssl x509 -in deploy/certs/fullchain.pem -noout -subject -enddate; \
	 elif [ "$(ALLOW_MISSING_CERTS)" = "1" ]; then \
	   echo "    WARNING: deploy/certs/ is empty, so 'make up-prod' will not"; \
	   echo "    serve HTTPS. Continuing because ALLOW_MISSING_CERTS=1."; \
	 else \
	   echo "    ERROR: deploy/certs/fullchain.pem and privkey.pem are missing." >&2; \
	   echo "    'make up-prod' would start and then fail to serve." >&2; \
	   echo "    Fix:  make certs DOMAIN=<your-hostname>" >&2; \
	   echo "    Or, to check the rest of the config before DNS/certs exist:" >&2; \
	   echo "        make prod-check ALLOW_MISSING_CERTS=1" >&2; \
	   exit 1; \
	 fi
	@echo
	@echo "prod-check passed. Bring it up with 'make up-prod'."

## Start the production stack: hardened base + prod overlay, dev overlay NOT
## loaded. Requires certificates in deploy/certs/ (see `make certs`).
up-prod: backend-env-check nginx-check
	$(PROD_COMPOSE) up -d --build

down-prod:
	$(PROD_COMPOSE) down

## ---------------------------------------------------------------------------
## TLS
## ---------------------------------------------------------------------------

## Obtain a Let's Encrypt certificate for DOMAIN via certbot's webroot
## plugin, using the port-80 challenge path nginx.https.conf serves.
##   make certs DOMAIN=ktm-bus.example.com
certs:
	@test -n "$(DOMAIN)" || { echo "usage: make certs DOMAIN=<hostname>" >&2; exit 2; }
	@test -f deploy/certs/fullchain.pem || { \
	  echo "no certificate yet -- starting the stack so the challenge path exists"; \
	  $(PROD_COMPOSE) up -d proxy backend; \
	  sleep 5; }
	docker run --rm \
	  -v "$(PWD)/deploy/certbot:/var/www/certbot" \
	  -v "$(PWD)/deploy/certs:/etc/letsencrypt" \
	  certbot/certbot certonly --webroot -w /var/www/certbot \
	    -d "$(DOMAIN)" --email "$(EMAIL)" --agree-tos --no-eff-email
	@echo
	@echo "certificates in deploy/certs/. Check the filenames against"
	@echo "deploy/nginx.https.conf (fullchain.pem / privkey.pem), then 'make up-prod'."

## Renew. Wire into cron on the host: 17 3 * * * cd <repo> && make renew-certs
## then reload nginx. certbot's own container has no timer.
renew-certs:
	docker run --rm \
	  -v "$(PWD)/deploy/certbot:/var/www/certbot" \
	  -v "$(PWD)/deploy/certs:/etc/letsencrypt" \
	  certbot/certbot renew --webroot -w /var/www/certbot
	$(PROD_COMPOSE) exec -T proxy nginx -s reload

## ---------------------------------------------------------------------------
## Backup / restore
## ---------------------------------------------------------------------------

## Verified, restorable, timestamped dump into backups/ (gitignored).
## Copies off this machine -- a backup on the same disk as the database is
## not a backup.
backup:
	$(SCRIPTS)/backup.sh

## Restore a dump. Refuses to overwrite a non-empty database unless
## FORCE_REPLACE=1, and takes a safety backup first when it does.
##   make restore-backup FILE=backups/ktm_bus_route_finder-....dump
##   DB_NAME=ktm_bus_scratch make restore-backup FILE=... --dry-run
restore-backup:
	@test -n "$(FILE)" || { echo "usage: make restore-backup FILE=<path>" >&2; exit 2; }
	$(SCRIPTS)/restore.sh FILE="$(FILE)" $(if $(DB_NAME),DB_NAME=$(DB_NAME),) $(ARGS)
