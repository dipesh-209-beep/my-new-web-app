import os
import threading
import time
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "http://localhost:5000")

# A single osrm-routed process only serves whichever profile its .osrm file
# was extracted with (see backend/README.md: nepal-latest.osrm is extracted
# with car.lua). Walking directions need a second osrm-routed instance
# extracted with foot.lua, so it gets its own base URL/port rather than
# reusing OSRM_BASE_URL. Falls back to OSRM_BASE_URL if unset so this
# doesn't break setups that haven't added the foot instance yet.
OSRM_FOOT_BASE_URL = os.environ.get("OSRM_FOOT_BASE_URL", OSRM_BASE_URL)

_PROFILE_BASE_URLS = {
    "foot": OSRM_FOOT_BASE_URL,
}

# Timeout configuration (seconds)
CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 8.0
WRITE_TIMEOUT = 5.0
POOL_TIMEOUT = 5.0

# Circuit breaker configuration
_CIRCUIT_FAILURE_THRESHOLD = 5  # failures before opening circuit
_CIRCUIT_RECOVERY_TIME = 30.0  # seconds before half-open
_CIRCUIT_HALF_OPEN_MAX = 3  # successful requests to close circuit


class OSRMError(Exception):
    pass


class CircuitBreaker:
    """Simple circuit breaker for OSRM calls.

    States: CLOSED (normal), OPEN (failing fast), HALF_OPEN (testing recovery).
    Maintains separate state per profile (driving/foot).
    """

    def __init__(self):
        self._lock = threading.Lock()
        # profile -> {"state": "CLOSED"|"OPEN"|"HALF_OPEN", "failures": int, "last_failure": float, "successes": int}
        self._state: dict[str, dict] = {}

    def _get(self, profile: str) -> dict:
        if profile not in self._state:
            self._state[profile] = {"state": "CLOSED", "failures": 0, "last_failure": 0.0, "successes": 0}
        return self._state[profile]

    def can_execute(self, profile: str) -> bool:
        with self._lock:
            st = self._get(profile)
            if st["state"] == "CLOSED":
                return True
            if st["state"] == "OPEN":
                if time.monotonic() - st["last_failure"] >= _CIRCUIT_RECOVERY_TIME:
                    st["state"] = "HALF_OPEN"
                    st["successes"] = 0
                    logger.info("OSRM circuit breaker for %s: OPEN -> HALF_OPEN", profile)
                    return True
                return False
            # HALF_OPEN
            return True

    def record_success(self, profile: str) -> None:
        with self._lock:
            st = self._get(profile)
            if st["state"] == "HALF_OPEN":
                st["successes"] += 1
                if st["successes"] >= _CIRCUIT_HALF_OPEN_MAX:
                    st["state"] = "CLOSED"
                    st["failures"] = 0
                    logger.info("OSRM circuit breaker for %s: HALF_OPEN -> CLOSED", profile)
            elif st["state"] == "CLOSED":
                st["failures"] = 0  # reset on success

    def record_failure(self, profile: str) -> None:
        with self._lock:
            st = self._get(profile)
            st["failures"] += 1
            st["last_failure"] = time.monotonic()
            if st["state"] == "HALF_OPEN":
                st["state"] = "OPEN"
                logger.warning("OSRM circuit breaker for %s: HALF_OPEN -> OPEN", profile)
            elif st["state"] == "CLOSED" and st["failures"] >= _CIRCUIT_FAILURE_THRESHOLD:
                st["state"] = "OPEN"
                logger.warning("OSRM circuit breaker for %s: CLOSED -> OPEN (failures=%d)", profile, st["failures"])


_circuit_breaker = CircuitBreaker()


# Small in-process cache. Road geometry between two fixed points
# (overwhelmingly stop-to-stop pairs, which don't move) is effectively
# static, but the same origin/destination gets searched repeatedly --
# people re-run searches, and different users often want the same
# commute. Without this, every one of those hits OSRM fresh. Bounded
# size + TTL rather than unbounded, since it's a plain in-memory dict
# with no eviction otherwise -- fine for a single-process deployment;
# revisit alongside the graph-cache multi-worker note in
# app/routing/graph_builder.py if this ever runs with multiple workers.
_ROUTE_CACHE_TTL_S = 300
_ROUTE_CACHE_MAX_ENTRIES = 500
_route_cache: dict[tuple, tuple[float, dict]] = {}

# FastAPI runs sync routes (all of ours) in a threadpool, so concurrent
# requests can genuinely hit _cache_get/_cache_set on different threads
# at once. Unlike a plain dict get/set (atomic under the GIL),
# _cache_set's eviction iterates the dict via min(...) -- if another
# thread mutates it mid-iteration, that raises "dictionary changed size
# during iteration". This lock is the same fix graph_builder.py already
# applies to its own cache/version globals, for the same reason.
_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> dict | None:
    with _cache_lock:
        entry = _route_cache.get(key)
        if entry is None:
            return None
        cached_at, value = entry
        if time.monotonic() - cached_at > _ROUTE_CACHE_TTL_S:
            del _route_cache[key]
            return None
        return value


def _cache_set(key: tuple, value: dict) -> None:
    with _cache_lock:
        if len(_route_cache) >= _ROUTE_CACHE_MAX_ENTRIES:
            # Cheap eviction: drop the oldest entry rather than maintaining a
            # full LRU structure -- this cache is a latency/load optimization,
            # not a correctness requirement, so approximate is fine.
            oldest_key = min(_route_cache, key=lambda k: _route_cache[k][0])
            del _route_cache[oldest_key]
        _route_cache[key] = (time.monotonic(), value)


# Per-profile HTTP clients with connection pooling
_http_clients: dict[str, httpx.Client] = {}
_clients_lock = threading.Lock()


def _get_client(profile: str) -> httpx.Client:
    """Get or create a pooled HTTP client for the given profile."""
    with _clients_lock:
        if profile not in _http_clients:
            base_url = _PROFILE_BASE_URLS.get(profile, OSRM_BASE_URL)
            _http_clients[profile] = httpx.Client(
                base_url=base_url,
                timeout=httpx.Timeout(
                    connect=CONNECT_TIMEOUT,
                    read=READ_TIMEOUT,
                    write=WRITE_TIMEOUT,
                    pool=POOL_TIMEOUT,
                ),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _http_clients[profile]


def _should_retry(exc: Exception, response: Optional[httpx.Response] = None) -> bool:
    """Determine if an exception/response warrants a retry.

    Retry on:
    - Connection errors (httpx.ConnectError, ConnectTimeout)
    - Read timeouts (httpx.ReadTimeout)
    - Server errors 502, 503, 504

    Do NOT retry on:
    - Client errors 4xx (invalid request, bad coordinates, etc.)
    - Other unexpected exceptions
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True
    if response is not None and response.status_code in (502, 503, 504):
        return True
    return False


def get_route_geometry(
    coords: list[tuple[float, float]],
    profile: str = "driving",
    bearings: list[tuple[float, int] | None] | None = None,
    radiuses: list[float | None] | None = None,
) -> dict:
    """coords: list of (lat, lon) in travel order, at least 2 points.

    bearings: optional per-waypoint (heading_degrees, range_degrees) hint,
    one entry per coordinate (or None for a given waypoint to leave it
    unconstrained). Restricts OSRM's snap to road segments travelling in
    roughly that direction -- the fix for a waypoint sitting near a
    divided road, where the nearest edge geometrically can be the wrong
    carriageway for the direction actually being travelled. See
    app/api/routing.py::_bearings_for for how these get computed.

    radiuses: optional per-waypoint max snap distance in meters (or None
    for OSRM's default of unlimited). Paired with bearings so a tight
    heading constraint can't push a snap out to some distant edge that
    happens to match -- if nothing satisfying both is within radius,
    OSRM returns an error, which callers already handle by falling back
    (see _attach_road_geometry's `except OSRMError: pass`).

    Both are optional and independent of each other; passing neither
    reproduces the previous unconstrained behavior exactly.
    """
    if len(coords) < 2:
        raise ValueError("Need at least 2 coordinates for OSRM routing")
    if bearings is not None and len(bearings) != len(coords):
        raise ValueError("bearings must have exactly one entry per coordinate")
    if radiuses is not None and len(radiuses) != len(coords):
        raise ValueError("radiuses must have exactly one entry per coordinate")

    # Round coordinates to 6 decimal places (~0.1m) for cache key to avoid
    # cache misses due to floating-point precision differences in DB data.
    rounded_coords = tuple((round(lat, 6), round(lon, 6)) for lat, lon in coords)

    cache_key = (
        profile,
        rounded_coords,
        tuple(bearings) if bearings is not None else None,
        tuple(radiuses) if radiuses is not None else None,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Circuit breaker check
    if not _circuit_breaker.can_execute(profile):
        raise OSRMError(f"Circuit breaker OPEN for {profile} OSRM")

    base_url = _PROFILE_BASE_URLS.get(profile, OSRM_BASE_URL)
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"/route/v1/{profile}/{coord_str}"
    params = {"overview": "full", "geometries": "geojson"}
    if bearings is not None:
        params["bearings"] = ";".join(
            f"{pair[0]:.0f},{pair[1]}" if pair is not None else "" for pair in bearings
        )
    if radiuses is not None:
        params["radiuses"] = ";".join(
            (str(r) if r is not None else "unlimited") for r in radiuses
        )

    client = _get_client(profile)
    last_exc: Optional[Exception] = None
    last_response: Optional[httpx.Response] = None

    # Up to 2 retries (3 attempts total) for transient failures
    for attempt in range(3):
        try:
            resp = client.get(url, params=params)
            last_response = resp
            resp.raise_for_status()
            _circuit_breaker.record_success(profile)
            break
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            last_response = exc.response
            if not _should_retry(exc, exc.response):
                _circuit_breaker.record_failure(profile)
                raise OSRMError(f"OSRM returned HTTP {exc.response.status_code}") from exc
            logger.warning("OSRM %s attempt %d failed (HTTP %d), retrying: %s", profile, attempt + 1, exc.response.status_code, exc)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last_exc = exc
            logger.warning("OSRM %s attempt %d failed (network), retrying: %s", profile, attempt + 1, exc)
        except httpx.RequestError as exc:
            last_exc = exc
            _circuit_breaker.record_failure(profile)
            raise OSRMError(f"OSRM request error: {exc}") from exc
        else:
            # No exception
            break

        if attempt < 2:  # Not the last attempt
            time.sleep(0.2 * (attempt + 1))  # Exponential backoff: 0.2s, 0.4s
    else:
        # All retries exhausted
        _circuit_breaker.record_failure(profile)
        if last_response is not None:
            raise OSRMError(f"OSRM returned HTTP {last_response.status_code} after retries") from last_exc
        raise OSRMError(f"OSRM request failed after retries: {last_exc}") from last_exc

    data = last_response.json()
    if data.get("code") != "Ok":
        raise OSRMError(f"OSRM returned code={data.get('code')}")

    route = data["routes"][0]
    result = {
        "geometry": route["geometry"],   # GeoJSON LineString
        "distance_m": route["distance"],
        "duration_s": route["duration"],
    }
    _cache_set(cache_key, result)
    return result