"""
core/config.py

Central app settings, loaded from environment / .env via pydantic-settings.
Everything that varies between dev, docker-compose, and prod should be
read from here, never hardcoded in models/queries/api files.

Two things in here are security-relevant rather than merely
configuration:

  * ENVIRONMENT / is_production -- the switch every "is this a real
    deployment?" check hangs off. See validate_production_settings at
    the bottom of this file, which refuses to start a production
    process that is configured like a development one.
  * validate_production_settings -- called once at import of app.main
    so a misconfigured production deploy fails immediately and loudly
    instead of quietly serving with development secrets, wildcard
    CORS, or per-worker in-memory rate limits. Development is left
    completely alone: it does nothing unless ENVIRONMENT is
    literally "production".
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved relative to this file (backend/app/core/config.py), not the
# process's current working directory -- a bare "env_file=.env" only
# resolves when pytest/uvicorn happen to be launched from backend/, and
# fails collection with "Field required" for admin_api_key/jwt_secret_key
# when launched from the repo root instead.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

logger = logging.getLogger(__name__)


def _dotenv_if_readable() -> Path | None:
    """The backend .env file, or None when it cannot be read.

    This file is mode 0600 on purpose: it holds the Postgres password, the
    Redis password, the admin API key and the JWT signing key, and
    scripts/gen_backend_env.py tightens the mode on any checkout it sees.
    In Docker, the backend service runs with `cap_drop: ALL`, which
    includes CAP_DAC_OVERRIDE -- so the container's root process cannot
    read a 0600 file owned by the host user, and pydantic's dotenv source
    raised PermissionError before any settings were assembled. The service
    then failed its health check and nginx returned 504 for the whole
    stack, with the cause buried in a dotenv traceback.

    None of that read was necessary. In a container the values arrive
    through Compose's `env_file:`, which the Docker daemon reads as root
    before the process starts, so they are already in the environment by
    the time this runs.

    So an unreadable file is treated as absent rather than fatal. If it was
    genuinely needed and its values are not otherwise in the environment,
    the failure is still immediate and legible -- pydantic reports which
    required field has no value -- instead of an unrelated traceback from
    inside a file-reading library. Readable in every normal case: a host
    uvicorn run, pytest, and a container where the bind mount happens to
    line up.
    """
    if not _ENV_FILE.is_file():
        return None
    try:
        with _ENV_FILE.open("rb"):
            return _ENV_FILE
    except OSError as exc:
        logger.warning(
            "Ignoring unreadable %s (%s); relying on the process environment "
            "instead. This is expected in Docker, where Compose supplies these "
            "values and `cap_drop: ALL` removes the ability to read a 0600 "
            "file owned by another user.",
            _ENV_FILE, exc,
        )
        return None

DEVELOPMENT = "development"
PRODUCTION = "production"
TESTING = "test"
VALID_ENVIRONMENTS = (DEVELOPMENT, PRODUCTION, TESTING)

# Values that the shipped .env.example / CI / old tutorials use as
# placeholders. Rejected outright in production so a deployment that
# copied an example file can't come up "successfully" with a secret
# that is published in the repository.
_PLACEHOLDER_SECRETS = frozenset(
    {
        "change_me_in_production",
        "changeme",
        "changemeinproduction",
        "secret",
        "dev",
        "password",
        "jwt-secret",
        "admin",
    }
)


class ProductionConfigError(RuntimeError):
    """Raised by validate_production_settings when ENVIRONMENT=production
    but the process is configured unsafely. A dedicated type so a
    deployment script can distinguish "you misconfigured this" from an
    arbitrary crash."""


class Settings(BaseSettings):
    # Matches docker-compose.yml's `backend.environment.DATABASE_URL` and
    # migrations/env.py's os.getenv("DATABASE_URL") — keep all three in sync.
    #
    # repr=False on every secret-bearing field below, and that is a
    # deliberate choice rather than an ergonomic one. A pydantic model
    # prints all its fields by default, and Settings ends up in assertion
    # output, `print()` debugging, and exception tracebacks far more often
    # than anyone intends -- a failing test asserted on a Settings object
    # dumped the live Postgres and Redis passwords, complete with
    # credentials, into CI output. repr=False keeps the field out of
    # str()/repr() so the value stays usable without becoming a
    # disclosure channel.
    DATABASE_URL: str = Field(
        default="postgresql://ktm_bus:ktm_bus_dev@localhost:5432/ktm_bus_route_finder",
        repr=False,
    )

    # Shared Redis, used for two separate things: the response cache
    # (app/core/response_cache.py) and, unless RATE_LIMIT_REDIS_URL says
    # otherwise, the admin-login rate limiter (app/core/rate_limit.py).
    #
    # These are declared here as Settings fields rather than being read
    # with os.getenv at each use site, and that is not tidiness. pydantic
    # loads backend/.env into the Settings object but does NOT inject it
    # into os.environ, so an os.getenv("REDIS_URL") never sees a value
    # written in backend/.env. The result was a silent failure: a
    # deployment that configured Redis in the .env file appeared to work,
    # ran with no shared cache and an in-process rate limiter, and only
    # surfaced when validate_production_settings() refused to start with
    # "no shared rate-limit store configured" -- pointing at a variable
    # the operator could plainly see set in their own file.
    #
    # Unset means "no Redis": the response cache falls back to an
    # in-process dict, and the rate limiter is per-worker. That is fine in
    # development and unacceptable in production, which is why
    # validate_production_settings refuses to start without it.
    REDIS_URL: str | None = Field(default=None, repr=False)

    # Optional override so rate-limit counters can live in a different
    # Redis database (or a different server) from the response cache --
    # eviction pressure on one should not reset login rate limits.
    # RATE_LIMIT_REDIS_URL wins over REDIS_URL; see rate_limit_redis_url.
    RATE_LIMIT_REDIS_URL: str | None = Field(default=None, repr=False)

    # Which of development / production / test this process is. Everything
    # security-gated below keys off `is_production`; nothing else in the
    # codebase should compare ENVIRONMENT directly.
    ENVIRONMENT: str = DEVELOPMENT

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
    # need to be listed here -- and the regex is *disabled* in production
    # (see validate_production_settings), so a prod process really does
    # need its own origin listed here.
    CORS_ORIGINS: str = "http://localhost:3000"

    # Comma-separated list of Host header values the server will answer
    # for, e.g. "ktm-bus.example.com,www.ktm-bus.example.com". Backs
    # Starlette's TrustedHostMiddleware, which is always on; the default
    # list below is what a local dev server legitimately answers to.
    # Required (non-empty, no dev defaults) in production -- see
    # validate_production_settings. This is the mitigation for the
    # Host-header / request.url reconstruction class of advisories
    # (PYSEC-2026-161, PYSEC-2026-248) regardless of starlette version.
    TRUSTED_HOSTS: str = "localhost,127.0.0.1,testserver,backend"

    # Whether to trust X-Forwarded-For / X-Real-IP when deriving the client
    # IP for rate limiting. MUST only be enabled when uvicorn is started
    # with --proxy-headers --forwarded-allow-ips=<the proxy's address>,
    # because otherwise any direct client can forge the header and get a
    # fresh rate-limit bucket per request. app/core/rate_limit.py refuses
    # to read the forwarded headers at all unless this is set. Off by
    # default because the dev stack publishes the backend's port 8000
    # directly (no proxy in front, so the header is attacker-controlled).
    TRUST_PROXY_HEADERS: bool = False

    # Shared secret for the legacy X-Admin-Api-Key header. See
    # ALLOW_LEGACY_SHARED_ADMIN_KEY below -- this value is *only* ever
    # consulted when that flag is on. Still has no default so a missing
    # value is caught at startup rather than at first request, and because
    # CI/tests set it.
    admin_api_key: str = Field(repr=False)

    # Escape hatch that re-enables the single shared X-Admin-Api-Key
    # secret, granting the full permission set to anyone holding it.
    #
    # Default False everywhere, and validate_production_settings *refuses
    # to start* a production process with it True -- so the shared key is
    # not an authentication path in production at all. It exists only so
    # an ad-hoc local `curl` during development keeps working; the
    # supported way for automation is a scoped, revocable service
    # credential (app/api/service_credentials.py).
    ALLOW_LEGACY_SHARED_ADMIN_KEY: bool = False

    # JWT settings for the AdminUser login flow (app/core/security.py).
    # No default on jwt_secret_key -- startup should fail loudly if unset.
    jwt_secret_key: str = Field(repr=False)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Crowd-sourced suggestions (app/api/suggestions.py): once a still-pending
    # suggestion's vote_count reaches this, it's applied to the live dataset
    # automatically (status -> "auto_applied") instead of waiting for an
    # editor/admin. Tuned low enough that a handful of genuinely-useful
    # fixes float up on their own, high enough that a single person can't
    # push a change through alone.
    AUTO_APPLY_VOTE_THRESHOLD: int = 26

    # Path to the geographic congestion-zone CSV
    # (app/routing/congestion_zones.py). This is a *file*, not database
    # data, so it cannot come from the DB the way the organic
    # segment_congestion_stats signal does.
    #
    # The default is the repo-root data/ directory, correct for a host
    # run (uvicorn from backend/). It is WRONG inside the container: the
    # image's build context is ./backend, which cannot reach data/ at the
    # repo root, and backend/.dockerignore excludes data/ anyway. The
    # container therefore needs data/congestion_zones.csv bind-mounted
    # in (docker-compose.yml) and this variable pointed at the mount
    # point -- the same pattern the osrm services use for their .osrm
    # files. Compose sets it; a bare `docker run` of the image without it
    # loads no zones, which load_zones() logs a warning about rather than
    # failing silently.
    #
    # The original code walked up a fixed number of parent directories from
    # congestion_zones.py, which resolved to a path that happened to exist
    # on the host and to nothing at all in the image -- so the entire
    # geographic half of the congestion model silently returned a ratio of
    # 1.0 in every containerised deployment. See load_zones()'s warning
    # branch and the test that pins the default to a real file.
    #
    # Blank is treated as unset (see the validator below), because an empty
    # value in a .env would otherwise win over this default and resolve to
    # Path("") == ".", a directory that exists but is not a file.
    CONGESTION_ZONES_PATH: str = str(
        Path(__file__).resolve().parents[3] / "data" / "congestion_zones.csv"
    )

    # Congestion aggregates (app/api/congestion.py, db/queries.py) are
    # bucketed by (day_of_week, 3-hour hour_bucket), so the table is
    # inherently bounded -- there is no raw per-request log to grow.
    # These retention knobs bound it further by dropping day-of-week
    # buckets that are stale relative to the newest data we hold, so an
    # install that once had many more routes than it does today doesn't
    # keep those aggregates forever. 0 disables the check entirely
    # (documented "keep everything" mode); see scripts/retention.py.
    CONGESTION_RETENTION_DAYS: int = 0

    model_config = SettingsConfigDict(env_file=_dotenv_if_readable(), extra="ignore")

    @field_validator("ENVIRONMENT")
    @classmethod
    def _normalise_environment(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in VALID_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be one of {VALID_ENVIRONMENTS}, got {value!r}."
            )
        return normalised

    @field_validator("CONGESTION_ZONES_PATH", mode="before")
    @classmethod
    def _blank_zones_path_falls_back_to_default(cls, value: object) -> object:
        """Treat a blank CONGESTION_ZONES_PATH as unset rather than as a path.

        `CONGESTION_ZONES_PATH=` in a .env is the natural way to write "I have
        no opinion, use the default" -- and backend/.env.example is copied
        verbatim into backend/.env by scripts/gen_backend_env.py, so a
        commented-in blank line there would reach every developer. Left
        alone, pydantic takes "" as an explicit value, Path("") becomes ".",
        and load_zones() is handed a directory."""
        if isinstance(value, str) and not value.strip():
            return str(
                Path(__file__).resolve().parents[3] / "data" / "congestion_zones.csv"
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == PRODUCTION

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def trusted_hosts_list(self) -> list[str]:
        return [host.strip() for host in self.TRUSTED_HOSTS.split(",") if host.strip()]

    @property
    def rate_limit_redis_url(self) -> str | None:
        """Shared rate-limit counter store. RATE_LIMIT_REDIS_URL wins over
        REDIS_URL when both are set so an operator can give rate limits
        their own Redis database. None means "no shared store
        configured", which is only acceptable outside production --
        see validate_production_settings."""
        return self.RATE_LIMIT_REDIS_URL or self.REDIS_URL or None


def _looks_like_placeholder_secret(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _PLACEHOLDER_SECRETS:
        return True
    # A JWT secret shorter than this can't carry enough entropy to be
    # worth brute-forcing offline if the process ever leaks it.
    return len(value) < 32


def validate_production_settings(settings: "Settings | None" = None) -> list[str]:
    """Refuse to run a production process configured unsafely.

    Returns the list of problems found (empty = safe). Raises
    ProductionConfigError listing every one of them, so a single restart
    tells the operator everything that needs fixing rather than one
    thing at a time.

    A no-op unless ENVIRONMENT == "production", so `pytest`, `uvicorn
    --reload`, and `docker compose up` (which all run as development)
    are completely unaffected. Called from app/main.py at import time.
    """
    settings = settings or get_settings()
    if not settings.is_production:
        return []

    problems: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    if _looks_like_placeholder_secret(settings.jwt_secret_key):
        problems.append(
            "JWT_SECRET_KEY is unset, a placeholder, or shorter than 32 characters. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if _looks_like_placeholder_secret(settings.admin_api_key):
        problems.append(
            "ADMIN_API_KEY is unset, a placeholder, or shorter than 32 characters. "
            "(It is only consulted when ALLOW_LEGACY_SHARED_ADMIN_KEY is true, but it "
            "must still be a real value in case that flag is ever turned on.)"
        )
    if settings.ALLOW_LEGACY_SHARED_ADMIN_KEY:
        problems.append(
            "ALLOW_LEGACY_SHARED_ADMIN_KEY is enabled. The single shared "
            "X-Admin-Api-Key secret grants the full permission set to anyone "
            "holding it and cannot be revoked or scoped, so it is not an "
            "acceptable production authentication path. Turn it off and issue "
            "scoped service credentials instead (POST /admin/service-credentials)."
        )
    if not settings.trusted_hosts_list:
        problems.append(
            "TRUSTED_HOSTS is empty. Set it to the comma-separated hostnames this "
            "deployment answers for, e.g. TRUSTED_HOSTS=ktm-bus.example.com."
        )
    if any(host in ("localhost", "127.0.0.1", "testserver", "*") for host in settings.trusted_hosts_list):
        problems.append(
            "TRUSTED_HOSTS still contains a development host "
            f"({settings.TRUSTED_HOSTS!r}). Replace it with the real public hostname(s)."
        )
    if not settings.cors_origins_list:
        problems.append(
            "CORS_ORIGINS is empty. Set it to the comma-separated origin(s) of the "
            "frontend that calls this API."
        )
    if "*" in settings.cors_origins_list:
        problems.append(
            "CORS_ORIGINS contains '*'. Wildcard origins are rejected in production; "
            "list the exact frontend origin(s) instead."
        )
    if all(
        origin.startswith(("http://", "localhost", "127.0.0.1", "10.", "192.168."))
        for origin in settings.cors_origins_list
    ):
        problems.append(
            f"CORS_ORIGINS ({settings.CORS_ORIGINS!r}) contains only plain-HTTP or "
            "private-network origins. Production frontend origins must be https://."
        )
    if settings.rate_limit_redis_url is None:
        problems.append(
            "No shared rate-limit store is configured. Set REDIS_URL (or "
            "RATE_LIMIT_REDIS_URL) so the login rate limit is counted once across all "
            "workers/instances; without it each worker keeps its own in-memory count."
        )
    if settings.TRUST_PROXY_HEADERS and "forwarded-allow-ips" not in os.environ.get("UVICORN_ARGS", ""):
        # Fatal, unlike most of the softer warnings here. TRUST_PROXY_HEADERS
        # asserts that X-Forwarded-For is trustworthy, which is only true if
        # uvicorn was actually told to forward it; if the flag is missing,
        # every request appears to come from the proxy and the per-IP login
        # rate limit collapses into one global bucket. An earlier version of
        # this comment called it "not fatal on its own" while appending to
        # `problems` -- i.e. fatal -- which is how docker-compose.prod.yml
        # shipped a backend that refused to start.
        #
        # The check is a substring test on UVICORN_ARGS because it cannot
        # inspect how uvicorn was really launched. The compose prod service
        # therefore sets UVICORN_ARGS to exactly the flags in its `command:`,
        # and both must change together.
        problems.append(
            "TRUST_PROXY_HEADERS is on but UVICORN_ARGS does not contain "
            "--forwarded-allow-ips, so uvicorn is not being told to trust "
            "X-Forwarded-For. Either start uvicorn with "
            "`--proxy-headers --forwarded-allow-ips=<proxy address>` and mirror "
            "those flags in UVICORN_ARGS, or turn TRUST_PROXY_HEADERS off. "
            "Left as-is, every request is attributed to the proxy and the "
            "per-IP rate limit becomes a single global bucket."
        )

    if problems:
        raise ProductionConfigError(
            "Refusing to start with ENVIRONMENT=production because the configuration "
            "is not safe for production:\n  - " + "\n  - ".join(problems)
        )
    return problems


@lru_cache
def get_settings() -> Settings:
    """Cached so we parse the environment once per process."""
    return Settings()
