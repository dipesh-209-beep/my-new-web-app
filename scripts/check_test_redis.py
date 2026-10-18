#!/usr/bin/env python3
"""Fail-closed validation of TEST_REDIS_URL for the backend test suite.

Why this exists
---------------
`make test-backend` runs pytest with an autouse fixture that calls
`response_cache.invalidate_all()`, which scans and deletes every
`respcache:*` key in the Redis named by REDIS_URL. The application's response
cache and rate limiter read that same REDIS_URL. So if the suite is pointed
at the development Redis (docker-compose.yml's `redis`, host loopback port
6379, database 0), a test run clears the running stack's cache.

To make that impossible, the test target requires an explicitly configured
`TEST_REDIS_URL` and refuses to start pytest unless this script says the URL
is a *dedicated* endpoint, distinct from the development one. The check is
deliberately structural (parse the URL, compare host+port) rather than a
substring match, and every error message is built from a redacted form of the
URL so a password can never leak into output.

A dedicated server (a different host or, for the local stack, a different
port) is the supported design -- `docker-compose.test.yml` provides one on
127.0.0.1:6380. A different *database index* on the shared development Redis
is explicitly rejected: it shares the server's memory and a future FLUSHALL,
and it is too easy to mis-select.

Liveness
--------
Structural URL validation alone is not enough. If TEST_REDIS_URL is *set* but
the server is down, `backend/tests/conftest.py`'s autouse fixture still runs,
but every Redis-backed test skips (`_require_redis`), so the suite reports a
misleading green. `make test-backend` therefore runs this guard with `--ping`,
which dials the *validated* endpoint (never the development Redis, with a
bounded timeout) and exits non-zero if it does not answer PING -- before
pytest starts. Without the flag the check stays a pure, network-free URL
validator, which is what the unit tests exercise.

Exit status: 0 when TEST_REDIS_URL is safe (and, with `--ping`, live); 1
otherwise (with a redacted reason on stderr).
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

# Only the Redis schemes. Anything else (http, redis+unix, ...) is rejected
# rather than guessed at.
ALLOWED_SCHEMES = frozenset({"redis", "rediss"})

# The development Redis is reachable on the host at localhost:6379 (published
# loopback port) and inside compose at redis:6379. Both are refused regardless
# of database index or credentials.
DEV_DEFAULT_PORT = 6379
DEV_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "redis"})

# redis ships 16 logical databases by default (0-15). Requiring an explicit,
# in-range index rejects a URL with a missing or malformed database path
# instead of silently defaulting to database 0.
MIN_DB_INDEX = 0
MAX_DB_INDEX = 15


def _redact(url: str) -> str:
    """A URL safe to print: scheme://host:port/db, with credentials removed.

    Never raises -- an unparseable URL becomes a fixed placeholder, because
    this is only ever used to build an error message and must not itself leak
    (or crash on) the raw value.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        port = parts.port
        path = parts.path.lstrip("/")
        netloc = host if port is None else f"{host}:{port}"
        return f"{parts.scheme}://{netloc}/{path}"
    except Exception:
        return "<unparseable>"


def validate_test_redis_url(url: str | None) -> str:
    """Return `url` unchanged if it is a safe, dedicated test endpoint.

    Raise ValueError with a redacted, actionable message otherwise. This
    function performs no I/O and reaches no network.
    """
    if url is None or not url.strip():
        raise ValueError(
            "TEST_REDIS_URL is not set. The backend suite must run against a "
            "dedicated test Redis, never the development Redis. Start one with "
            "'make test-redis-up' and export "
            "'TEST_REDIS_URL=redis://:test-redis-pass@localhost:6380/0'."
        )
    url = url.strip()

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"TEST_REDIS_URL is not a valid URL ({_redact(url)}).") from exc

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(
            f"TEST_REDIS_URL must use one of {sorted(ALLOWED_SCHEMES)}; "
            f"got scheme '{parts.scheme}' ({_redact(url)})."
        )

    host = parts.hostname
    if not host:
        raise ValueError(f"TEST_REDIS_URL has no host ({_redact(url)}).")

    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"TEST_REDIS_URL has an invalid port ({_redact(url)}).") from exc
    if port is None:
        port = DEV_DEFAULT_PORT

    if "?" in url or "#" in url:
        # Query/fragment carry no meaning for a Redis URL and only create
        # ambiguity for the checks below; refuse rather than ignore them.
        raise ValueError(
            f"TEST_REDIS_URL must not contain a query or fragment ({_redact(url)})."
        )

    db_path = parts.path.lstrip("/")
    if not db_path:
        raise ValueError(
            "TEST_REDIS_URL must name an explicit database index, e.g. "
            f"'.../{MIN_DB_INDEX}' ({_redact(url)})."
        )
    if not db_path.isdigit():
        raise ValueError(
            "TEST_REDIS_URL's database index must be a non-negative integer; "
            f"got '{db_path}' ({_redact(url)})."
        )
    db_index = int(db_path)
    if not (MIN_DB_INDEX <= db_index <= MAX_DB_INDEX):
        raise ValueError(
            f"TEST_REDIS_URL's database index must be between {MIN_DB_INDEX} "
            f"and {MAX_DB_INDEX}; got {db_index} ({_redact(url)})."
        )

    if host.lower() in DEV_HOSTS and port == DEV_DEFAULT_PORT:
        raise ValueError(
            "TEST_REDIS_URL points at the development Redis "
            f"({_redact(url)}). Use a dedicated test server on a different "
            "host or port (see docker-compose.test.yml, port 6380)."
        )

    return url


# Bounded wall-clock budget for the liveness PING. Short so a hung or
# misconfigured endpoint fails fast instead of stalling the test target.
DEFAULT_LIVENESS_TIMEOUT = 3.0


def _connect_client(url: str, timeout: float):
    """Build a Redis client for `url` with a bounded connect/read timeout.

    Imported lazily so the pure URL validator -- and its unit tests -- never
    require the `redis` package (or a live server) to be installed.
    """
    import redis  # local import on purpose: keep URL validation import-light

    return redis.Redis.from_url(
        url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )


def ping_test_redis(
    url: str,
    *,
    timeout: float = DEFAULT_LIVENESS_TIMEOUT,
    connect=_connect_client,
) -> None:
    """Assert that the already-validated `url` answers PING within `timeout`.

    Connects ONLY to `url` -- callers pass the output of
    validate_test_redis_url, so this can never reach the development Redis and
    never falls back to one. Raises ValueError (built from the redacted URL and
    the exception *type* only, so a password can never leak) on any connection,
    authentication or timeout failure, or on a non-truthy PING reply.

    `connect` is injectable so the behaviour is testable without a live server.
    """
    try:
        client = connect(url, timeout)
        pong = client.ping()
    except Exception as exc:  # noqa: BLE001 -- any driver error means "down"
        raise ValueError(
            f"TEST_REDIS_URL is configured but not reachable ({_redact(url)}): "
            f"{type(exc).__name__}."
        ) from exc
    if not pong:
        raise ValueError(
            f"TEST_REDIS_URL is configured but did not answer PING ({_redact(url)})."
        )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # `--ping` additionally proves the endpoint is live. Without it this is a
    # pure, network-free URL check (what the unit tests exercise);
    # `make test-backend` passes it so a configured-but-down test Redis fails
    # here, before pytest turns the Redis-backed tests into skips.
    check_liveness = "--ping" in args or "-p" in args

    url = os.environ.get("TEST_REDIS_URL")
    try:
        safe_url = validate_test_redis_url(url)
    except ValueError as exc:
        print(f"[check:test-redis] {exc}", file=sys.stderr)
        print(
            "[check:test-redis] refusing to run the backend tests against an "
            "unsafe/unspecified Redis.",
            file=sys.stderr,
        )
        return 1
    # Redacted on purpose -- the URL may carry a password.
    print(f"[check:test-redis] OK: using {_redact(safe_url)}")

    if check_liveness:
        try:
            ping_test_redis(safe_url)
        except ValueError as exc:
            print(f"[check:test-redis] {exc}", file=sys.stderr)
            print(
                "[check:test-redis] the test Redis did not answer a PING; "
                "refusing to run the suite (a down Redis turns the Redis-backed "
                "tests into skips and hides the failure).",
                file=sys.stderr,
            )
            return 1
        print(f"[check:test-redis] OK: PING answered by {_redact(safe_url)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
