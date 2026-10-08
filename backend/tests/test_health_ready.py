"""
tests/test_health_ready.py

Tests for GET /health/ready in app/main.py.

Readiness is the one endpoint whose entire job is to distinguish "this
process can serve" from "this process is up but useless", and the
distinction that matters is between the *three* Redis states, not two:

  * not configured  -> reported as skipped, and still ready. Development
    legitimately runs without Redis, and failing readiness there would
    make the status lie.
  * configured and healthy -> ready.
  * configured and unreachable -> 503. In production a missing shared
    rate-limit store is refused at startup precisely because the
    per-worker fallback multiplies an attacker's login guesses by the
    worker count, so "configured but down" is that same degradation
    appearing at runtime.

Beyond the four required states, three tests pin the properties that are
easy to break while changing the probes -- each of these caught a real
bug during development:

  * the healthy-Redis case catches a false 503. An earlier version of
    the probe passed socket_connect_timeout/socket_timeout to client.ping()
    expecting them to bound the call. They do not -- ConnectionPool
    .get_connection() takes no options and the RESP parser reads
    connection.socket_timeout, fixed at construction -- and they are
    forwarded to the PING response callback, which is <lambda>(r). So a
    *healthy* Redis raised TypeError and readiness reported 503. This
    test fails loudly if anyone reinstates those kwargs.

  * the concurrent-probes case exists because running the two probes in
    sequence made the both-dependencies-hung case take 2s + 2s = 4.01s.
    The bound that matters to a load balancer is the total.

  * the deadline cases exist because the failure this endpoint reports
    is most often a saturated pool, where a request spends its whole
    time waiting on pool checkout (pool_timeout=10). A server-side
    statement_timeout does not bound that, so the bound is applied with
    a thread deadline -- the 2s assertions below are what keep that
    mechanism honest.

    The pool-saturation and pool-recovery tests do use the real engine,
    and so do need a live database; they skip cleanly when there isn't
    one. Everything else stubs the engine and needs no database.
"""
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import redis as redis_module
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

import app.main as main_module
from app.core import redis_client
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.db import session as session_module
from app.main import app


def _require_db() -> None:
    """Skip a test that drives the real engine when no database is up.
    Same guard, and same wording, as the one in tests/conftest.py."""
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()


# A port nothing listens on: connecting is refused immediately, so a
# dependency failure is instant rather than a 10s connect timeout.
DEAD_URL = "postgresql://nobody:nobody@127.0.0.1:1/nothing"


@pytest.fixture(autouse=True)
def _restore_redis_config(monkeypatch):
    """Point the app at a specific REDIS_URL for one test, then put it back.

    monkeypatch.setattr on the Settings singleton rather than a bare
    assignment: get_settings() returns the same cached object for the
    life of the process, so `get_settings().REDIS_URL = ...` would leak
    into every later test in the run. It is monkeypatch's job to undo it.

    The reset of get_redis()'s memo is the other half -- it caches the
    client and the cache outlives the test. This is the same pair of
    hazards app/core/redis_client.py documents at the definition of
    _reset_for_tests(), and the same reason that function exists.
    """
    redis_client._reset_for_tests()
    yield
    redis_client._reset_for_tests()


def _set_redis_url(monkeypatch, url: str | None) -> None:
    """Configure Redis for the current test, with automatic restoration."""
    monkeypatch.setattr(get_settings(), "REDIS_URL", url)
    redis_client._reset_for_tests()


class _StubConnection:
    """A Connection that does the minimum the DB probe asks of one."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def execute(self, *_args, **_kwargs):
        return None


class _StubEngine:
    """Enough of an Engine for `with engine.connect() as conn: ...`.

    The three Redis-state tests are about the Redis distinction, and they
    assert `db.ok is True` to prove Redis is the only thing being judged.
    A real database would turn them into database tests that fail wherever
    no database is up. A stub keeps them focused on what they are for --
    a fake, not a private-API monkeypatch, and no socket involved.
    """

    def connect(self):
        return _StubConnection()

    def dispose(self):
        return None


@contextmanager
def _stub_healthy_db():
    """Satisfy the DB leg without a database, restoring it after."""
    previous = main_module.engine
    main_module.engine = _StubEngine()
    try:
        yield
    finally:
        main_module.engine = previous


@pytest.fixture
def client():
    return TestClient(app)


def _ready(client) -> tuple[int, dict]:
    resp = client.get("/health/ready")
    return resp.status_code, resp.json()


# --- the four states ------------------------------------------------------


def test_both_healthy_reports_ready(client, redis_url, monkeypatch):
    """The regression that motivated this file: a healthy Redis must not
    report unhealthy. Guards the PING call itself, not just the status."""
    _set_redis_url(monkeypatch, redis_url)

    try:
        redis_client.get_redis().ping()
    except (redis_module.RedisError, AttributeError):
        pytest.skip(f"No live Redis available at {redis_url}")

    with _stub_healthy_db():
        status, body = _ready(client)
    assert status == 200, f"healthy dependencies reported {status}: {body}"
    assert body["status"] == "ok"
    assert body["checks"]["db"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is True
    # A real probe reports how long it took; a stubbed one would not.
    assert isinstance(body["checks"]["db"]["ms"], int)
    assert isinstance(body["checks"]["redis"]["ms"], int)


def test_db_ok_redis_not_configured_is_still_ready(client, monkeypatch):
    """The distinction the whole handler turns on. get_redis() returning
    None is a legitimate configuration, not a failure."""
    _set_redis_url(monkeypatch, None)
    assert redis_client.get_redis() is None, "precondition: Redis unset"

    with _stub_healthy_db():
        status, body = _ready(client)
    assert status == 200
    assert body["status"] == "ok"
    assert body["checks"]["db"]["ok"] is True
    # Reported, not silently omitted and not failed.
    assert body["checks"]["redis"] == {"ok": None, "skipped": "not configured"}


def test_db_ok_redis_unreachable_is_unhealthy(client, monkeypatch):
    """Configured but down is the case production cares about: the shared
    rate-limit store has silently become per-worker."""
    _set_redis_url(monkeypatch, "redis://127.0.0.1:1/0")
    assert redis_client.get_redis() is not None, "precondition: Redis configured"

    with _stub_healthy_db():
        status, body = _ready(client)
    assert status == 503
    assert body["status"] == "unhealthy"
    assert body["checks"]["db"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is False
    # The exception TYPE only. The message would leak the Redis host,
    # and /health/ready is unauthenticated by design.
    assert body["checks"]["redis"]["error"] == "ConnectionError"


def test_db_unreachable_is_unhealthy(client, monkeypatch):
    """No DB means no endpoint that matters can work, so the process is up
    but useless. Redis is left unconfigured to keep this test focused on
    the DB leg."""
    _set_redis_url(monkeypatch, None)

    with _using_engine(_dead_engine()):
        status, body = _ready(client)

    assert status == 503
    assert body["status"] == "unhealthy"
    assert body["checks"]["db"]["ok"] is False
    assert body["checks"]["db"]["error"] == "OperationalError"


# --- the bounds the endpoint exists to provide ---------------------------


def test_both_dependencies_hung_is_bounded_by_one_deadline(client, monkeypatch):
    """Both legs hung: the total is what a load balancer waits on, and
    with sequential probes this took 4.01s. It must stay at one deadline."""
    # Both legs point at a server that ACCEPTS and never answers. A
    # refused connection (port 1) would fail instantly and prove nothing
    # about the deadline; only a hung peer exercises it. Redis and the
    # DB can share one such server -- they never speak past the silence.
    with _silent_tcp_server() as (hung_port, _connections):
        _set_redis_url(monkeypatch, f"redis://127.0.0.1:{hung_port}/0")

        with _using_engine(_hang_db_probe(hung_port)):
            started = time.monotonic()
            status, body = _ready(client)
            elapsed = time.monotonic() - started

    assert status == 503
    assert body["checks"]["db"] == {"ok": False, "error": "timeout"}
    assert body["checks"]["redis"] == {"ok": False, "error": "timeout"}
    # Generous upper bound: the point is that it is ~2s and not ~4s, so
    # this tolerates a slow CI box while still failing if the probes
    # become sequential again.
    assert elapsed < 3.0, f"two sequential 2s deadlines: took {elapsed:.2f}s"


def test_probe_reports_timeout_under_a_saturated_pool(client, monkeypatch):
    """A saturated pool is the failure this endpoint most often has to
    report, and it is invisible to a SQL statement_timeout because the
    wait happens before the connection is acquired. Saturating the real
    pool is the only way to prove the thread deadline is doing the work.
    """
    _require_db()
    ceiling = session_module.engine.pool.size() + session_module.engine.pool._max_overflow
    held = [session_module.engine.connect() for _ in range(ceiling)]
    try:
        _set_redis_url(monkeypatch, None)

        started = time.monotonic()
        status, body = _ready(client)
        elapsed = time.monotonic() - started

        assert status == 503
        assert body["checks"]["db"] == {"ok": False, "error": "timeout"}
        # pool_timeout is 10s; without the deadline this would be ~10s.
        assert elapsed < 3.0, f"probe not bounded under pool exhaustion: {elapsed:.2f}s"
    finally:
        for conn in held:
            conn.close()


def test_abandoned_probe_connections_return_to_the_pool(client, monkeypatch):
    """A probe that times out is abandoned, not cancelled, so its thread
    is still blocked in libpq when the response is sent. The claim in
    readiness_check's docstring is that such a thread closes its own
    connection when it unblocks. If that were false, sustained polling
    during an outage would permanently erode the pool -- a worse failure
    than the one the endpoint exists to report.
    """
    _require_db()
    ceiling = session_module.engine.pool.size() + session_module.engine.pool._max_overflow
    held = [session_module.engine.connect() for _ in range(ceiling)]
    try:
        _set_redis_url(monkeypatch, None)
        assert _ready(client)[0] == 503
    finally:
        for conn in held:
            conn.close()

    # The abandoned probe gives up at pool_timeout. Outlast it, then the
    # pool must be whole again.
    deadline = time.monotonic() + session_module.engine.pool._timeout + 5
    while time.monotonic() < deadline and session_module.engine.pool.checkedout():
        time.sleep(0.5)

    assert session_module.engine.pool.checkedout() == 0, (
        "probe threads leaked connections into the pool"
    )
    assert _ready(client)[0] == 200, "pool did not recover after the probes unwound"


# --- liveness must stay independent --------------------------------------


def test_liveness_stays_ok_while_readiness_fails(client, monkeypatch):
    """The reason /health exists separately: an orchestrator restarts on
    liveness, and restarting cannot fix a database that is down. If
    liveness started reflecting dependency state, one outage would
    become a restart loop across every replica.
    """
    _set_redis_url(monkeypatch, None)

    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}

    with _using_engine(_dead_engine()):
        assert client.get("/health/ready").status_code == 503


# --- helpers --------------------------------------------------------------


def _dead_engine():
    """An engine aimed at a port nothing listens on, so the DB leg fails
    fast with OperationalError. This is the "DB is gone" case; the
    "DB is hung" case is _hang_db_probe."""
    return create_engine(
        DEAD_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        pool_timeout=10,
        pool_recycle=1800,
        future=True,
    )


@contextmanager
def _silent_tcp_server():
    """A TCP server that accepts connections and never says anything.

    Yields (port, live_connections). This is how a *hung* peer is
    simulated, for either dependency. A refused connection is not a
    substitute: it fails at connect() in microseconds, so it can never
    prove a read deadline is honoured -- it only proves the port is
    closed.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    port = sock.getsockname()[1]

    accepted: list = []
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            accepted.append(conn)  # held open, never written to

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, accepted
    finally:
        stop.set()
        sock.close()
        for conn in accepted:
            try:
                conn.close()
            except OSError:
                pass


def _hang_db_probe(port: int):
    """An engine whose connect blocks forever, so the DB probe hits its
    deadline without needing a live database.

    Aimed at a _silent_tcp_server: the TCP connect succeeds and then the
    Postgres startup handshake blocks reading a greeting that never
    arrives. (A pool of size 0 was the first attempt and does not work --
    QueuePool treats "nothing to hand out" as a reason to try creating
    one, so it fails fast with Connection refused rather than waiting.)
    """
    return create_engine(
        f"postgresql://nobody:nobody@127.0.0.1:{port}/nothing",
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        pool_timeout=10,
        pool_recycle=1800,
        future=True,
    )


@contextmanager
def _using_engine(replacement):
    """Point app.main at `replacement` for the duration of the block.

    app.main binds `engine` at import time, so swapping session.engine is
    not enough: readiness_check closes over the module-level name in its
    own module. A context manager rather than monkeypatch because the
    swap must not outlive the test, and because these tests hand in a
    purpose-built engine that also needs disposing.
    """
    previous = main_module.engine
    main_module.engine = replacement
    try:
        yield
    finally:
        main_module.engine = previous
        replacement.dispose()
