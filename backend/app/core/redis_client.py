"""
core/redis_client.py

Single shared Redis connection, used by response_cache.py (and available
for anything else that wants cross-worker shared state later).

Deliberately absent-by-default: with no REDIS_URL set, get_redis() returns
None and every caller in this codebase treats that as "fall back to the
per-worker in-memory behavior this project already had" -- not an error.
That's what makes adding Redis a purely additive change: a developer
running `pytest` locally, or a deployment that hasn't set REDIS_URL yet,
sees identical behavior to before this module existed.

Connection is lazy (first call to get_redis(), not import time) and
memoized. If Redis is configured but unreachable, callers are expected to
catch redis.RedisError around individual operations and fall back
per-call -- see response_cache.py for that pattern. This module itself
doesn't hide connection errors, since "REDIS_URL is set but wrong" is a
config bug worth surfacing, whereas "Redis blipped mid-request" is a
runtime condition callers should degrade gracefully from.
"""
import os
from typing import Optional

try:
    import redis
except ImportError:  # pragma: no cover - redis is a normal dependency
    # now (see requirements.txt), but this keeps the module importable in
    # environments that haven't installed it yet, degrading to "Redis
    # disabled" rather than crashing at import time.
    redis = None  # type: ignore[assignment]

_client: Optional["redis.Redis"] = None
_attempted = False


def get_redis() -> Optional["redis.Redis"]:
    """Returns the shared Redis client, or None if REDIS_URL isn't set
    (or the redis package isn't installed). Does not itself catch
    connection errors on later use -- a client object is returned as soon
    as REDIS_URL is configured, whether or not the server is actually
    reachable yet (redis-py connects lazily per-command); callers must
    handle redis.RedisError around their own get/set/scan/delete calls.
    """
    global _client, _attempted
    if _attempted:
        return _client
    _attempted = True

    url = os.getenv("REDIS_URL")
    if not url or redis is None:
        return None

    _client = redis.Redis.from_url(url, decode_responses=False)
    return _client


def _reset_for_tests() -> None:
    """Clears the memoized client/attempt flag so a test can change
    REDIS_URL (via monkeypatch) and have get_redis() re-evaluate it.
    Not for use outside tests -- production code never needs to
    re-resolve REDIS_URL after startup."""
    global _client, _attempted
    _client = None
    _attempted = False
