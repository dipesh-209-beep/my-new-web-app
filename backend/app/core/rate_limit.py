"""Shared slowapi Limiter instance.

Kept separate from main.py so route modules (e.g. app/api/admin_auth.py)
can import `limiter` to decorate individual endpoints without creating a
circular import with the FastAPI app itself.

Storage backend
---------------
Redis when `RATE_LIMIT_REDIS_URL` (or `REDIS_URL`) is set, otherwise
slowapi/limits' in-memory "memory://".

That in-memory fallback is fine for local development and pytest, and it
is *deliberately not* the production configuration: with in-memory
storage each worker process keeps its own count, so "5/minute" across 3
workers is really 15/minute in aggregate, and across N replicas it is
5xN -- a per-instance limit presented as a global one.
validate_production_settings() (app/core/config.py) therefore refuses to
start an ENVIRONMENT=production process that has no shared rate-limit
store configured, so this cannot silently degrade in production.

If Redis is configured but unreachable, limits' storage raises rather
than falling back: for a login endpoint, failing closed (denying
requests) is safer than failing open. That is deliberate, and it is why
the redis service has a healthcheck and the backend depends on it.

Client IP for the key
---------------------
Delegated to app.core.request_context.get_client_ip, which reads
X-Forwarded-For only when TRUST_PROXY_HEADERS is on. Trusting those
headers unconditionally -- the previous behaviour -- meant any client
that could reach the backend directly could send a fresh
X-Forwarded-For on every request and never hit its own limit.
"""

from fastapi import Request
from slowapi import Limiter
from starlette.config import Config

from .config import get_settings
from .request_context import get_client_ip


def _get_client_ip(request: Request) -> str:
    """Limiter key function: one bucket per real client, and only
    proxy-aware when the deployment says the proxy is trustworthy."""
    return get_client_ip(request)


# Read through Settings rather than os.getenv so there is exactly one
# place that knows how a rate-limit store is chosen. RATE_LIMIT_REDIS_URL
# wins over REDIS_URL (see Settings.rate_limit_redis_url).
_rate_limit_redis_url = get_settings().rate_limit_redis_url

limiter = Limiter(
    key_func=_get_client_ip,
    storage_uri=_rate_limit_redis_url or "memory://",
)

# slowapi builds a starlette Config that auto-detects a ".env" in the
# current working directory and reads it. Two reasons to replace that
# object with one that never touches the filesystem:
#
#  1. It duplicates configuration. Everything slowapi could read from
#     .env is already in os.environ by way of pydantic Settings, and
#     having a second, differently-sourced path to the same variables is
#     how a value ends up honoured in one place and ignored in another --
#     the same class of bug as the os.getenv("REDIS_URL") mistake fixed in
#     config.py.
#
#  2. It was a hard startup failure in Docker. backend/.env is mode 0600
#     and the backend service runs with `cap_drop: ALL`, which removes
#     CAP_DAC_OVERRIDE, so the container's root process cannot read a file
#     owned by the host user. slowapi's read raised PermissionError while
#     this module was still being imported, taking the service down at
#     health check and returning 504 through nginx for the entire stack.
#
# Config(env_file=None) still reads os.environ -- it just does not go
# looking for a dotenv file, which is the behaviour the container and a
# host run both want.
limiter.app_config = Config(env_file=None)
