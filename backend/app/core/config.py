"""
core/config.py

Central app settings, loaded from environment / .env via pydantic-settings.
Everything that varies between dev, docker-compose, and prod should be
read from here, never hardcoded in models/queries/api files.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved relative to this file (backend/app/core/config.py), not the
# process's current working directory -- a bare "env_file=.env" only
# resolves when pytest/uvicorn happen to be launched from backend/, and
# fails collection with "Field required" for admin_api_key/jwt_secret_key
# when launched from the repo root instead.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    # Matches docker-compose.yml's `backend.environment.DATABASE_URL` and
    # migrations/env.py's os.getenv("DATABASE_URL") — keep all three in sync.
    DATABASE_URL: str = "postgresql://ktm_bus:ktm_bus_dev@localhost:5432/ktm_bus_route_finder"

    # Default radius (meters) for GET /stops/nearby when the caller omits it.
    DEFAULT_NEARBY_RADIUS_M: int = 500

    # Default / max page size for GET /stops.
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 200

    # In-process response cache TTLs (seconds) for read-mostly GET
    # endpoints -- see app/core/response_cache.py. Kept short since the
    # underlying data can change via /admin writes at any time; long
    # enough to absorb bursty repeat requests (map re-renders, pagination
    # clicks, multiple users browsing the same route) within a session.
    STOPS_CACHE_TTL_S: int = 30
    ROUTES_CACHE_TTL_S: int = 30
    # Congestion has no admin write path that should invalidate it (real
    # traffic gets recorded on every /route-finder call -- invalidating
    # on every write would defeat the cache), so this TTL is the only
    # thing bounding staleness. 60s is short relative to the 3-hour
    # buckets the data itself is aggregated into.
    CONGESTION_CACHE_TTL_S: int = 60

    # Comma-separated list of allowed CORS origins, e.g.
    # "https://app.example.com,https://staging.example.com".
    # Defaults to the local Next.js dev server so `docker compose up`
    # works out of the box; override in staging/prod via the env var.
    # In dev, localhost/LAN origins are also accepted via the regex-based
    # CORS middleware in app/main.py, so only exact production origins
    # need to be listed here.
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    # Shared secret checked by require_admin_key (app/core/security.py).
    # No default on purpose -- startup should fail loudly if it's unset.
    admin_api_key: str

    # JWT settings for the AdminUser login flow (app/core/security.py).
    # No default on jwt_secret_key -- startup should fail loudly if unset.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Crowd-sourced suggestions (app/api/suggestions.py): once a still-pending
    # suggestion's vote_count reaches this, it's applied to the live dataset
    # automatically (status -> "auto_applied") instead of waiting for an
    # editor/admin. Tuned low enough that a handful of genuinely-useful
    # fixes float up on their own, high enough that a single person can't
    # push a change through alone.
    AUTO_APPLY_VOTE_THRESHOLD: int = 26

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Cached so we parse the environment once per process."""
    return Settings()
