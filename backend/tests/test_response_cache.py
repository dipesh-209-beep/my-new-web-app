"""
Tests for app/core/response_cache.py's two-tier caching (Redis + the
original in-memory dict fallback).

The Redis-specific tests require a real Redis reachable at REDIS_URL (or
localhost:6379 by default) -- skip cleanly if that's not available,
same convention as this project's live-Postgres tests.
"""
import pytest
import redis as redis_module

from app.core import redis_client
from app.core.redis_client import get_redis
from app.core.response_cache import cached_response, invalidate, invalidate_all


@pytest.fixture(autouse=True)
def _reset_redis_client_memoization(monkeypatch):
    """redis_client.get_redis() memoizes its result for the life of the
    process (by design -- see its docstring), which would otherwise leak
    the REDIS_URL/no-REDIS_URL choice from one test into the next. Reset
    before and after every test in this file so each test's monkeypatched
    env actually takes effect."""
    redis_client._reset_for_tests()
    yield
    redis_client._reset_for_tests()


def _make_counted_fn(call_count: dict):
    @cached_response("test_ns", ttl_seconds=30, key_params=("x",))
    def expensive(x=None):
        call_count["n"] += 1
        return {"x": x, "call": call_count["n"]}

    return expensive


class TestWithoutRedis:
    def test_caches_and_serves_repeat_calls_without_recomputing(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)

        r1 = expensive(x=1)
        r2 = expensive(x=1)
        assert r1 == r2
        assert call_count["n"] == 1

    def test_different_keys_do_not_share_a_cache_entry(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)

        expensive(x=1)
        expensive(x=2)
        assert call_count["n"] == 2

    def test_invalidate_forces_recomputation(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)

        expensive(x=1)
        invalidate("test_ns")
        expensive(x=1)
        assert call_count["n"] == 2


class TestWithRedis:
    @pytest.fixture(autouse=True)
    def _require_redis(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client._reset_for_tests()
        client = get_redis()
        try:
            client.ping()
        except redis_module.RedisError:
            pytest.skip("No live Redis available at redis://localhost:6379/0")
        invalidate_all()
        yield
        invalidate_all()

    def test_caches_and_serves_repeat_calls_without_recomputing(self):
        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)

        r1 = expensive(x=1)
        r2 = expensive(x=1)
        assert r1 == r2
        assert call_count["n"] == 1

    def test_entry_actually_lands_in_redis_not_only_the_in_memory_tier(self):
        expensive = _make_counted_fn({"n": 0})
        expensive(x=1)

        client = get_redis()
        keys = list(client.scan_iter(match="respcache:test_ns:*"))
        assert len(keys) == 1

    def test_invalidate_removes_the_redis_side_entry_too(self):
        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)
        expensive(x=1)

        invalidate("test_ns")

        client = get_redis()
        keys = list(client.scan_iter(match="respcache:test_ns:*"))
        assert len(keys) == 0

        expensive(x=1)
        assert call_count["n"] == 2, "expected a real recomputation after invalidate()"

    def test_a_second_process_style_cache_miss_still_hits_redis(self):
        """Simulates two workers: the in-memory dict is empty for the
        'second worker', but Redis already has the entry from the
        'first worker's call -- this is the actual bug the Redis tier
        exists to fix (see response_cache.py's module docstring)."""
        from app.core import response_cache

        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)
        expensive(x=1)

        # Simulate a fresh worker process that has never seen this key by
        # clearing only the in-memory tier, leaving Redis untouched.
        response_cache._store.pop("test_ns", None)

        expensive(x=1)
        assert call_count["n"] == 1, "expected the Redis-side entry to serve this, not a recompute"


class TestRedisConfiguredButUnreachable:
    def test_falls_back_to_in_memory_cache_without_raising(self, monkeypatch):
        # A port nothing is listening on -- redis-py connects lazily, so
        # this only fails once a command is actually attempted, which is
        # exactly the scenario cached_response's try/except is for.
        monkeypatch.setenv("REDIS_URL", "redis://localhost:1/0")
        redis_client._reset_for_tests()

        call_count = {"n": 0}
        expensive = _make_counted_fn(call_count)

        r1 = expensive(x=1)
        r2 = expensive(x=1)
        assert r1 == r2
        assert call_count["n"] == 1, "expected the in-memory fallback to still cache correctly"

    def test_invalidate_does_not_raise_when_redis_is_unreachable(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:1/0")
        redis_client._reset_for_tests()

        invalidate("test_ns")  # must not raise
        invalidate_all()  # must not raise
