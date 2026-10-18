"""Test-only helpers for pointing the backend suite at an isolated Redis.

Kept in an importable module rather than inline in conftest.py so the
behaviour can be unit-tested without depending on pytest fixture ordering.

The application's response cache and rate limiter both resolve their store
from `Settings.REDIS_URL` (see app/core/redis_client.py:get_redis and
app/core/rate_limit.py). To keep the suite off the development Redis, the
test entry point (`make test-backend`) exports the dedicated test endpoint as
REDIS_URL for the pytest process, and requires TEST_REDIS_URL to be set and to
pass scripts/check_test_redis.py. These helpers add a second line of defence
for invocation paths that only set TEST_REDIS_URL (e.g. a bare `pytest`).
"""
from __future__ import annotations

import os
from typing import Optional

from app.core import redis_client
from app.core.config import get_settings


def resolve_redis_url() -> Optional[str]:
    """The Redis URL the suite should use.

    `TEST_REDIS_URL` wins; otherwise the process `REDIS_URL` (CI's disposable
    instance). There is deliberately **no** default: an unset value means "no
    Redis" (the Redis-specific tests skip), never the development endpoint at
    localhost:6379.
    """
    return os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL") or None


def pin_test_redis(monkeypatch) -> Optional[str]:
    """Point the cached Settings at TEST_REDIS_URL and drop any memoized client.

    When `TEST_REDIS_URL` is set, the cached `Settings.REDIS_URL` is pinned to
    it (restored automatically by `monkeypatch`) and the memoized Redis client
    is reset, so `get_redis()`, the response cache and the rate limiter all
    re-resolve the same isolated endpoint and cannot retain a client bound to
    the development Redis. When `TEST_REDIS_URL` is unset this is a no-op
    beyond the client reset, preserving CI's REDIS_URL-only configuration.

    Returns the effective URL (TEST_REDIS_URL if set, else the unchanged
    REDIS_URL), or None.
    """
    test_url = os.getenv("TEST_REDIS_URL")
    if test_url:
        monkeypatch.setattr(get_settings(), "REDIS_URL", test_url)
    redis_client._reset_for_tests()
    return test_url or get_settings().REDIS_URL
