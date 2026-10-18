"""Focused tests for scripts/check_test_redis.py (the TEST_REDIS_URL guard).

These are deliberately pure: they exercise URL parsing, the dev-endpoint
rejection, and the liveness helper via an *injected* connector -- they never
open a socket, contact Redis, or touch PostgreSQL. Run with:

    backend/venv/bin/python -m pytest -v scripts/test_check_test_redis.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent / "check_test_redis.py"
_spec = importlib.util.spec_from_file_location("check_test_redis", _MODULE_PATH)
check_test_redis = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(check_test_redis)

validate = check_test_redis.validate_test_redis_url
redact = check_test_redis._redact
ping = check_test_redis.ping_test_redis

SAFE = "redis://:secret@localhost:6380/0"


class TestAccepted:
    def test_dedicated_loopback_port_is_accepted(self):
        assert validate(SAFE) == SAFE

    def test_non_zero_database_index_is_accepted(self):
        url = "redis://:secret@localhost:6380/9"
        assert validate(url) == url

    def test_non_localhost_host_is_accepted(self):
        url = "redis://:secret@test-redis.internal:6379/0"
        assert validate(url) == url

    def test_rediss_scheme_is_accepted(self):
        url = "rediss://:secret@localhost:6380/0"
        assert validate(url) == url

    def test_surrounding_whitespace_is_stripped(self):
        assert validate(f"  {SAFE}  ") == SAFE


class TestRejected:
    def test_missing_is_rejected(self):
        with pytest.raises(ValueError, match="TEST_REDIS_URL is not set"):
            validate(None)

    def test_empty_is_rejected(self):
        with pytest.raises(ValueError, match="TEST_REDIS_URL is not set"):
            validate("   ")

    def test_unsupported_scheme_is_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            validate("http://localhost:6380/0")

    def test_no_scheme_is_rejected(self):
        with pytest.raises(ValueError, match="scheme"):
            validate("localhost:6380/0")

    def test_invalid_port_is_rejected(self):
        with pytest.raises(ValueError, match="invalid port"):
            validate("redis://localhost:notaport/0")

    def test_missing_database_index_is_rejected(self):
        with pytest.raises(ValueError, match="database index"):
            validate("redis://localhost:6380")

    def test_non_integer_database_index_is_rejected(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            validate("redis://localhost:6380/abc")

    def test_negative_database_index_is_rejected(self):
        with pytest.raises(ValueError, match="non-negative integer"):
            validate("redis://localhost:6380/-1")

    def test_out_of_range_database_index_is_rejected(self):
        with pytest.raises(ValueError, match="between"):
            validate("redis://localhost:6380/16")

    def test_query_string_is_rejected(self):
        with pytest.raises(ValueError, match="query or fragment"):
            validate("redis://localhost:6380/0?foo=bar")

    @pytest.mark.parametrize(
        "url",
        [
            "redis://:secret@localhost:6379/0",
            "redis://:secret@127.0.0.1:6379/0",
            "redis://:secret@redis:6379/0",
            "redis://:secret@[::1]:6379/0",
        ],
    )
    def test_development_endpoint_is_rejected(self, url):
        with pytest.raises(ValueError, match="development Redis"):
            validate(url)

    def test_development_endpoint_is_rejected_even_with_other_db_index(self):
        # A different logical DB on the same server is still the shared
        # development Redis and is refused.
        with pytest.raises(ValueError, match="development Redis"):
            validate("redis://:secret@localhost:6379/9")


class TestNoSecretLeakage:
    def test_error_message_does_not_contain_the_password(self):
        secret = "super-secret-password"
        with pytest.raises(ValueError) as excinfo:
            validate(f"redis://:{secret}@localhost:6379/0")
        assert secret not in str(excinfo.value)

    def test_unsupported_scheme_error_does_not_contain_the_password(self):
        secret = "another-secret"
        with pytest.raises(ValueError) as excinfo:
            validate(f"ftp://:{secret}@localhost:6380/0")
        assert secret not in str(excinfo.value)

    def test_redact_strips_credentials(self):
        assert redact("redis://:pw@localhost:6380/3") == "redis://localhost:6380/3"

    def test_redact_handles_garbage(self):
        assert redact("not a url")  # no exception, non-empty


class TestMain:
    def test_main_returns_zero_for_safe_url(self, monkeypatch, capsys):
        monkeypatch.setenv("TEST_REDIS_URL", SAFE)
        assert check_test_redis.main([]) == 0
        captured = capsys.readouterr()
        assert "secret" not in captured.out
        assert "secret" not in captured.err

    def test_main_returns_one_for_dev_endpoint_without_leaking_password(
        self, monkeypatch, capsys
    ):
        monkeypatch.setenv("TEST_REDIS_URL", "redis://:leaky@localhost:6379/0")
        assert check_test_redis.main([]) == 1
        captured = capsys.readouterr()
        assert "leaky" not in captured.out
        assert "leaky" not in captured.err

    def test_main_returns_one_when_unset(self, monkeypatch, capsys):
        monkeypatch.delenv("TEST_REDIS_URL", raising=False)
        assert check_test_redis.main([]) == 1
        captured = capsys.readouterr()
        assert "is not set" in captured.err

    def test_main_without_ping_flag_never_connects(self, monkeypatch, capsys):
        # The default path stays a pure URL check: it must not dial anything
        # (proven by making the liveness helper explode if it is called).
        def _boom(*a, **k):
            raise AssertionError("liveness must not run without --ping")

        monkeypatch.setenv("TEST_REDIS_URL", SAFE)
        monkeypatch.setattr(check_test_redis, "ping_test_redis", _boom)
        assert check_test_redis.main([]) == 0


class TestLiveness:
    def test_ping_succeeds_when_server_answers(self):
        seen = {}

        class _Client:
            def ping(self):
                return True

        def _connect(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return _Client()

        # No exception == success. It connects only to the URL it was given.
        assert ping("redis://:pw@localhost:6380/0", connect=_connect) is None
        assert seen["url"] == "redis://:pw@localhost:6380/0"
        assert seen["timeout"] == check_test_redis.DEFAULT_LIVENESS_TIMEOUT

    def test_ping_honours_a_custom_timeout(self):
        seen = {}

        class _Client:
            def ping(self):
                return True

        def _connect(url, timeout):
            seen["timeout"] = timeout
            return _Client()

        ping("redis://:pw@localhost:6380/0", timeout=0.5, connect=_connect)
        assert seen["timeout"] == 0.5

    def test_ping_raises_when_connection_fails(self):
        def _connect(url, timeout):
            raise ConnectionRefusedError("nope")

        with pytest.raises(ValueError, match="not reachable"):
            ping("redis://:pw@localhost:6380/0", connect=_connect)

    def test_ping_raises_on_falsey_reply(self):
        class _Client:
            def ping(self):
                return False

        with pytest.raises(ValueError, match="did not answer PING"):
            ping("redis://:pw@localhost:6380/0", connect=lambda u, t: _Client())

    def test_ping_error_does_not_leak_the_password(self):
        secret = "s3cr3t-should-not-leak"

        def _connect(url, timeout):
            # Even if the driver's own exception text contains the password,
            # the raised message must not.
            raise RuntimeError(f"auth failed for {url} ({secret})")

        with pytest.raises(ValueError) as excinfo:
            ping(f"redis://:{secret}@localhost:6380/0", connect=_connect)
        assert secret not in str(excinfo.value)


class TestMainLiveness:
    def test_ping_flag_reports_success(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setenv("TEST_REDIS_URL", SAFE)
        monkeypatch.setattr(
            check_test_redis, "ping_test_redis", lambda url, **k: calls.append(url)
        )
        assert check_test_redis.main(["--ping"]) == 0
        # Connected to the validated endpoint only.
        assert calls == [SAFE]
        captured = capsys.readouterr()
        assert "PING answered" in captured.out
        assert "secret" not in captured.out and "secret" not in captured.err

    def test_ping_flag_fails_fast_when_unreachable(self, monkeypatch, capsys):
        def _down(url, **k):
            raise ValueError("TEST_REDIS_URL is configured but not reachable (redis://localhost:6380/0): ConnectionRefusedError.")

        monkeypatch.setenv("TEST_REDIS_URL", SAFE)
        monkeypatch.setattr(check_test_redis, "ping_test_redis", _down)
        assert check_test_redis.main(["--ping"]) == 1
        captured = capsys.readouterr()
        assert "PING" in captured.err
        assert "secret" not in captured.out and "secret" not in captured.err

    def test_ping_flag_still_rejects_unsafe_url_first(self, monkeypatch, capsys):
        # A dev endpoint is refused before any liveness attempt is made.
        def _boom(*a, **k):
            raise AssertionError("must not dial an unvalidated endpoint")

        monkeypatch.setenv("TEST_REDIS_URL", "redis://:pw@localhost:6379/0")
        monkeypatch.setattr(check_test_redis, "ping_test_redis", _boom)
        assert check_test_redis.main(["--ping"]) == 1
        captured = capsys.readouterr()
        assert "development Redis" in captured.err
