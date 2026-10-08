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

Values are JSON-serialized for Redis storage (safer than pickle).
The in-memory tier keeps raw Python objects. Pydantic models are
serialized via model_dump() for JSON compatibility.
"""

import json
import time
from collections import defaultdict
from functools import wraps
from typing import Any, Callable

from app.core.redis_client import get_redis

def _jsonable(obj: Any) -> Any:
    """Recursively convert Pydantic models (and containers of them) to
    plain JSON-able structures.

    Handles three shapes the in-memory tier stores as raw objects:
      * a bare model (e.g. RouteOut for GET /routes/{id})
      * a list of models (e.g. GET /routes/{id}/stops returns a bare
        list[RouteStopOut] -- previously json.dumps raised TypeError here
        because it does not want model_dump called for it)
      * a dict containing models (e.g. RouteListOut.model_dump() output)

    For RouteOut and similar models with a validation_alias/serialization_alias
    mismatch on the operator field, a *top-level* operator key is renamed to
    operator_ref for storage so that model_validate can reconstruct the
    object on a cache hit. Nested operator keys inside a container are left
    alone: RouteOut accepts them via populate_by_name (see schemas.py).
    """
    if isinstance(obj, dict):
        return {key: _jsonable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(value) for value in obj]
    if hasattr(obj, "model_dump"):
        data = obj.model_dump()
        if "operator" in data and data["operator"] is not None:
            data["operator_ref"] = data.pop("operator")
        return _jsonable(data)
    if hasattr(obj, "dict"):  # Pydantic v1
        data = obj.dict()
        if "operator" in data and data["operator"] is not None:
            data["operator_ref"] = data.pop("operator")
        return _jsonable(data)
    return obj


def _json_dumps(obj: Any) -> str:
    """Serialize to JSON, handling Pydantic models via model_dump()."""
    return json.dumps(_jsonable(obj))


def _json_loads(data: str) -> Any:
    """Deserialize from JSON. Returns plain dicts/lists, not Pydantic models.
    Callers that need Pydantic models should re-validate."""
    return json.loads(data)


# Max entries per namespace in the in-memory tier to prevent unbounded growth.
# LRU eviction is applied when this limit is reached.
_MAX_ENTRIES_PER_NAMESPACE = 500


# namespace -> OrderedDict[key, (expires_at_monotonic, value)] -- the fallback tier,
# always present regardless of whether Redis is configured. OrderedDict maintains
# insertion/access order for LRU eviction.
from collections import OrderedDict

_store: dict[str, OrderedDict[tuple, tuple[float, object]]] = defaultdict(OrderedDict)

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
            # Did Redis answer "this key does not exist" rather than error?
            # A healthy-miss must NOT be satisfied by the per-process
            # in-memory overlay: the overlay can only go stale *cross-worker*
            # (another worker's invalidate() deleted the shared Redis key but
            # this process still holds the old value), which is exactly the
            # staleness the shared tier exists to prevent. The in-memory tier
            # is reserved for the two cases where it cannot be stale --
            # Redis unconfigured (dev/pytest) and Redis erroring (downgrade
            # to per-worker caching).
            redis_healthy_miss = False
            if client is not None:
                redis_key = _redis_key(namespace, key)
                try:
                    cached_str = client.get(redis_key)
                    if cached_str is not None:
                        return _json_loads(cached_str)
                    redis_healthy_miss = True
                except Exception:
                    # Redis configured but unreachable/erroring this call --
                    # fall through to the in-memory tier below rather than
                    # failing the request over a cache problem.
                    pass

            bucket = _store[namespace]
            now = time.monotonic()
            cached = bucket.get(key)
            if cached is not None and cached[0] > now:
                if redis_healthy_miss:
                    # Healthy Redis has no entry for this key. Serving the
                    # local overlay here would resurrect a cross-worker-stale
                    # copy after another worker invalidated the namespace, so
                    # drop it and recompute instead.
                    del bucket[key]
                else:
                    # LRU: move to end (most recently used)
                    bucket.move_to_end(key)
                    return cached[1]
            elif cached is not None:
                # Expired entry
                del bucket[key]

            result = fn(*args, **kwargs)

            if client is not None:
                try:
                    client.set(_redis_key(namespace, key), _json_dumps(result), ex=int(ttl_seconds))
                except Exception:
                    pass  # same reasoning as above -- degrade, don't fail

            # LRU eviction: if namespace exceeds max, remove oldest entry
            if len(bucket) >= _MAX_ENTRIES_PER_NAMESPACE:
                bucket.popitem(last=False)  # Remove first (oldest) item

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
