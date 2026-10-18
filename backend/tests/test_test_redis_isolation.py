"""Focused tests for the backend test-Redis isolation helpers.

These verify that the suite resolves an isolated Redis endpoint, propagates
it to the app's cache/rate-limit configuration, and never retains a memoized
client bound to a previous endpoint -- without contacting any Redis server.
get_redis() builds its client lazily, so inspecting the connection pool's
kwargs does not open a socket.

The endpoint used here is loopback port 6399 (nothing listens on it), never
the development Redis on 6379.
"""
from __future__ import annotations

from app.core import redis_client
from app.core.config import get_settings
from tests import redis_test_support

TEST_ENDPOINT = "redis://127.0.0.1:6399/7"


class TestResolve:
    def test_prefers_test_redis_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://ignored.example:6379/0")
        monkeypatch.setenv("TEST_REDIS_URL", TEST_ENDPOINT)
        assert redis_test_support.resolve_redis_url() == TEST_ENDPOINT

    def test_falls_back_to_redis_url(self, monkeypatch):
        monkeypatch.delenv("TEST_REDIS_URL", raising=False)
        monkeypatch.setenv("REDIS_URL", TEST_ENDPOINT)
        assert redis_test_support.resolve_redis_url() == TEST_ENDPOINT

    def test_has_no_development_default(self, monkeypatch):
        monkeypatch.delenv("TEST_REDIS_URL", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        # Never localhost:6379 -- an unconfigured suite means "no Redis".
        assert redis_test_support.resolve_redis_url() is None


class TestPin:
    def test_pins_settings_and_clears_memoized_client(self, monkeypatch):
        monkeypatch.setenv("TEST_REDIS_URL", TEST_ENDPOINT)
        # Simulate a client already memoized against something else.
        redis_client._reset_for_tests()
        redis_client.get_redis()  # no REDIS_URL configured -> None, but sets _attempted
        assert redis_client._attempted is True

        effective = redis_test_support.pin_test_redis(monkeypatch)

        assert effective == TEST_ENDPOINT
        assert get_settings().REDIS_URL == TEST_ENDPOINT
        # Memoization cleared, so the next get_redis() re-resolves the pinned URL.
        assert redis_client._attempted is False

    def test_noop_without_test_redis_url(self, monkeypatch):
        monkeypatch.delenv("TEST_REDIS_URL", raising=False)
        monkeypatch.setattr(get_settings(), "REDIS_URL", None)
        assert redis_test_support.pin_test_redis(monkeypatch) is None
        assert get_settings().REDIS_URL is None

    def test_propagates_to_app_client_and_rate_limit_config(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_REDIS_URL", raising=False)
        monkeypatch.setenv("TEST_REDIS_URL", TEST_ENDPOINT)
        redis_test_support.pin_test_redis(monkeypatch)

        # The rate limiter resolves its store through Settings (see
        # app/core/rate_limit.py), so it must see the same isolated endpoint.
        assert get_settings().rate_limit_redis_url == TEST_ENDPOINT

        # The app's Redis client targets the pinned endpoint. Building the
        # client is lazy, so this does not connect.
        client = redis_client.get_redis()
        assert client is not None
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs.get("host") == "127.0.0.1"
        assert kwargs.get("port") == 6399
        assert kwargs.get("db") == 7

        # Do not leak this client into the next test.
        redis_client._reset_for_tests()
