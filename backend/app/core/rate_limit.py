"""Shared slowapi Limiter instance.

Kept separate from main.py so route modules (e.g. app/api/admin_auth.py)
can import `limiter` to decorate individual endpoints without creating a
circular import with the FastAPI app itself.

Storage backend: in-memory by default (slowapi/limits' "memory://"),
same as before this module supported anything else. If REDIS_URL
is set, the limiter counts requests in Redis instead, which is what
actually makes "N/minute" mean N per minute across every worker process
or replica -- with in-memory storage, each worker keeps its own count, so
"5/minute" on 3 workers is really 15/minute in aggregate. See
app/api/admin_auth.py's login rate limit, which this specifically matters
for.

Uses REDIS_URL (same as response_cache) when available. A separate
RATE_LIMIT_REDIS_URL can override if needed. If Redis is configured but
unreachable, the limiter will fail -- this is intentional for a login
endpoint where failing closed (denying requests) is safer than failing
open.
"""
import os

from fastapi import Request
from slowapi import Limiter


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting proxy headers when present.

    Behind a reverse proxy (nginx, load balancer, CDN), the real client
    IP is forwarded via X-Forwarded-For or X-Real-IP.  We use the first
    (leftmost) value in X-Forwarded-For, falling back to X-Real-IP, then
    to the direct peer address.  This prevents all requests from sharing
    the same rate-limit bucket when the app sits behind a proxy.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


# Prefer REDIS_URL (shared with response cache), allow override via
# RATE_LIMIT_REDIS_URL for explicit control.
_rate_limit_redis_url = os.getenv("RATE_LIMIT_REDIS_URL") or os.getenv("REDIS_URL")

limiter = Limiter(
    key_func=_get_client_ip,
    storage_uri=_rate_limit_redis_url or "memory://",
)
