"""
core/response_cache.py

TTL cache for read-mostly GET endpoints (GET /stops, GET /routes, GET
/congestion, etc). Two tiers, tried in order on every operation:

  1. Redis, if REDIS_URL is configured and reachable -- shared across
     every worker process/replica, so an admin write's invalidate() call
     actually takes effect everywhere immediately, and so a cache hit on
     one worker benefits requests handled by another.
  2. The original per-process in-memory dict -- used whenever Redis isn't
     configured (REDIS_URL unset: local dev, `pytest`, anyone who hasn't
     opted in yet) *and* as a live fallback if a configured Redis call
     itself fails (connection refused, timeout, etc). This is why the
     dict-backed path was kept rather than replaced: it means enabling
     Redis is purely additive risk-wise. Worst case if Redis is down,
     behavior degrades to exactly what this module has always done, on
     a per-worker basis -- never to "no caching" or a hard error.

Values are pickled for Redis storage. That's an intentional choice, not
an oversight: the values cached here are Python objects built entirely by
this codebase's own endpoint functions (Pydantic models, plain dicts) --
nothing here ever deserializes untrusted external input, which is the
actual risk pickle carries. It also means callers keep returning whatever
plain Python objects they already return; nothing about the
@cached_response call sites in stops.py/routes.py/etc needs to change to
be JSON-serialization-safe.
"""

import pickle
import time
from collections import defaultdict
from functools import wraps
from typing import Callable

from app.core.redis_client import get_redis

# namespace -> {key: (expires_at_monotonic, value)} -- the fallback tier,
# always present regardless of whether Redis is configured.
_store: dict[str, dict[tuple, tuple[float, object]]] = defaultdict(dict)

_REDIS_KEY_PREFIX = "respcache"


def _redis_key(namespace: str, key: tuple) -> str:
    return f"{_REDIS_KEY_PREFIX}:{namespace}:{key!r}"


def cached_response(namespace: str, ttl_seconds: float, key_params: tuple[str, ...]):
    """Cache a FastAPI endpoint's return value for `ttl_seconds`, keyed on
    the named query/path params in `key_params`.

    Only list the actual request-identifying kwargs (offset, limit,
    route_id, ...) in `key_params` -- never `db`, which is a per-request
    Session and isn't part of what makes two calls "the same request".

    `namespace` groups related endpoints so invalidate() can drop just
    "stops" or "routes" after a write, without wiping caches that write
    didn't affect. Uses functools.wraps so FastAPI's signature
    introspection (for query-param parsing / OpenAPI) still sees the
    original function, not this wrapper -- decorate *under* @router.get,
    as in the call sites.
    """

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = tuple(kwargs.get(name) for name in key_params)

            client = get_redis()
            if client is not None:
                redis_key = _redis_key(namespace, key)
                try:
                    cached_bytes = client.get(redis_key)
                    if cached_bytes is not None:
                        return pickle.loads(cached_bytes)
                except Exception:
                    # Redis configured but unreachable/erroring this call --
                    # fall through to the in-memory tier below rather than
                    # failing the request over a cache problem.
                    pass

            bucket = _store[namespace]
            now = time.monotonic()
            cached = bucket.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]

            result = fn(*args, **kwargs)

            if client is not None:
                try:
                    client.set(_redis_key(namespace, key), pickle.dumps(result), ex=int(ttl_seconds))
                except Exception:
                    pass  # same reasoning as above -- degrade, don't fail

            bucket[key] = (now + ttl_seconds, result)
            return result

        return wrapper

    return decorator


def invalidate(namespace: str) -> None:
    """Drop every cached entry in `namespace`, in both tiers. Call this
    from any admin write endpoint that changes the data that namespace
    serves, the same way bump_graph_version()/get_cached_graph(refresh=True)
    are already called after writes that change routing-graph shape."""
    _store.pop(namespace, None)

    client = get_redis()
    if client is None:
        return
    try:
        # Redis has no prefix-delete; SCAN (not KEYS -- KEYS blocks the
        # server on a large keyspace, SCAN doesn't) then delete in one
        # batch. This namespace's keyspace is small (one entry per
        # distinct set of query params ever seen within the TTL window),
        # so a single batch is fine -- no need to chunk the delete.
        keys = list(client.scan_iter(match=f"{_REDIS_KEY_PREFIX}:{namespace}:*"))
        if keys:
            client.delete(*keys)
    except Exception:
        pass  # best-effort -- a stale Redis entry expires via its own TTL
        # regardless, so failing to proactively clear it here isn't a
        # correctness problem, just a slower-to-recover one.


def invalidate_all() -> None:
    """Mainly for tests -- clears every namespace, in both tiers."""
    _store.clear()

    client = get_redis()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match=f"{_REDIS_KEY_PREFIX}:*"))
        if keys:
            client.delete(*keys)
    except Exception:
        pass
