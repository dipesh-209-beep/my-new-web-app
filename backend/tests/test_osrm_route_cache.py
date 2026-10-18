"""Unit tests for the OSRM road-geometry cache in app.routing.osrm_client.

The cache is only a latency/load optimization, but REVIEW.md flagged the old
eviction (a full min() scan per insert once full) as O(n); these lock in the
OrderedDict replacement: bounded size, oldest-inserted evicted, TTL expiry,
and independent copies on read.
"""
import time

import app.routing.osrm_client as osrm_client


def _key(n: int) -> tuple:
    return ("driving", (0.0, 0.0), (1.0, 1.0), n)


def _fresh_cache(monkeypatch) -> None:
    monkeypatch.setattr(osrm_client, "_route_cache", type(osrm_client._route_cache)())


def test_cache_evicts_oldest_inserted_and_stays_bounded(monkeypatch):
    monkeypatch.setattr(osrm_client, "_ROUTE_CACHE_MAX_ENTRIES", 3)
    _fresh_cache(monkeypatch)

    for i in range(5):
        osrm_client._cache_set(_key(i), {"leg": i})

    assert len(osrm_client._route_cache) == 3
    # The two oldest insertions were evicted; the newest three remain.
    assert osrm_client._cache_get(_key(0)) is None
    assert osrm_client._cache_get(_key(1)) is None
    for i in (2, 3, 4):
        assert osrm_client._cache_get(_key(i)) == {"leg": i}


def test_cache_refresh_of_existing_key_does_not_grow_past_cap(monkeypatch):
    monkeypatch.setattr(osrm_client, "_ROUTE_CACHE_MAX_ENTRIES", 2)
    _fresh_cache(monkeypatch)

    osrm_client._cache_set(_key(1), {"leg": 1})
    osrm_client._cache_set(_key(2), {"leg": 2})
    osrm_client._cache_set(_key(1), {"leg": 1, "refreshed": True})

    assert len(osrm_client._route_cache) == 2
    assert osrm_client._cache_get(_key(1)) == {"leg": 1, "refreshed": True}
    assert osrm_client._cache_get(_key(2)) == {"leg": 2}


def test_cache_hit_returns_independent_copy(monkeypatch):
    _fresh_cache(monkeypatch)
    osrm_client._cache_set(_key(1), {"coords": [[1.0, 2.0]]})

    first = osrm_client._cache_get(_key(1))
    first["coords"][0][0] = 999.0

    assert osrm_client._cache_get(_key(1)) == {"coords": [[1.0, 2.0]]}


def test_cache_expires_after_ttl(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(osrm_client, "_ROUTE_CACHE_TTL_S", -1)
    osrm_client._cache_set(_key(1), {"leg": 1})

    time.sleep(0)
    assert osrm_client._cache_get(_key(1)) is None
