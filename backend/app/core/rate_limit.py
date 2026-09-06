"""Shared slowapi Limiter instance.

Kept separate from main.py so route modules (e.g. app/api/admin_auth.py)
can import `limiter` to decorate individual endpoints without creating a
circular import with the FastAPI app itself.

Storage backend: in-memory by default (slowapi/limits' "memory://"),
same as before this module supported anything else. If RATE_LIMIT_REDIS_URL
is set, the limiter counts requests in Redis instead, which is what
actually makes "N/minute" mean N per minute across every worker process
or replica -- with in-memory storage, each worker keeps its own count, so
"5/minute" on 3 workers is really 15/minute in aggregate. See
app/api/admin_auth.py's login rate limit, which this specifically matters
for.

Unlike core/response_cache.py, this does NOT gracefully fall back to
in-memory if Redis is configured but unreachable -- the `limits` library
doesn't support a multi-backend fallback, and building one here would
mean tracking request counts in two places and reconciling them, which
is a lot of complexity for a feature whose failure mode (a rate limit
check errors) is already the safer direction to fail in for something
protecting a login endpoint. Concretely: if you set RATE_LIMIT_REDIS_URL,
you are taking on "the rate limiter depends on Redis being up" as a
trade for "the rate limit is now actually accurate across workers" --
that's a reasonable trade given Redis is already a `depends_on` service
in docker-compose.yml with its own restart policy, but it's a trade, not
a free upgrade, which is why this is a separate opt-in env var rather
than reusing REDIS_URL from response_cache.py automatically.
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.getenv("RATE_LIMIT_REDIS_URL", "memory://"),
)
