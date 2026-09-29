"""
tests/conftest.py

Shared pytest fixtures across the whole backend test suite.
"""
import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.response_cache import invalidate_all
from app.core.security import (
    EDITOR_PERMISSIONS,
    PERM_SUGGESTIONS_REVIEW,
    ROLE_ADMIN,
    ROLE_EDITOR,
    create_access_token,
    generate_service_key,
    hash_password,
)
from app.db.session import SessionLocal
from app.models import AdminAuditLog, AdminUser, ServiceCredential

TEST_PASSWORD = "correct-horse-battery-staple"

# The Redis the cache tests should talk to. Previously hardcoded as
# "redis://localhost:6379/0" in three places, which silently skipped those
# tests the moment Redis was given a password -- and "the tests skip" is
# exactly the kind of thing nobody notices until the code is broken.
#
# Resolution order:
#   1. TEST_REDIS_URL -- for CI, where the host/port differ from a dev box
#   2. REDIS_URL      -- what the running stack is actually configured with
#   3. the local default, for a bare `redis-server` on a dev machine
#
# The compose stack runs Redis with --requirepass (see
# docker-compose.yml), so on a dev machine `make test` needs REDIS_URL set
# to the authenticated URL -- `make test` does that for you. Anyone
# running pytest by hand will see the cache tests skip, which is the
# correct and intended behaviour rather than a failure.
DEFAULT_TEST_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture
def redis_url() -> str:
    """The Redis the cache tests should talk to.

    Previously hardcoded as "redis://localhost:6379/0" in three places,
    which silently skipped those tests the moment Redis was given a
    password -- and "the tests skip" is exactly the kind of thing nobody
    notices until the code is broken.

    Resolution order:
      1. TEST_REDIS_URL -- for CI, where host/port differ from a dev box
      2. REDIS_URL      -- what the running stack is actually configured with
      3. the local default, for a bare `redis-server` on a dev machine

    The compose stack runs Redis with --requirepass (see
    docker-compose.yml), so `make test` sets REDIS_URL to the
    authenticated URL. Anyone running pytest by hand will see the cache
    tests skip, which is the intended behaviour rather than a failure.
    """
    return os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL") or DEFAULT_TEST_REDIS_URL


@pytest.fixture(autouse=True)
def _reset_response_cache():
    """The in-process response cache (app/core/response_cache.py) is a
    module-level dict that persists for the life of the test process --
    same as it does in production, since that's the whole point of it.

    In production that's fine: writes go through the admin endpoints,
    which call invalidate() on the right namespace. In tests, several
    files call endpoints directly, mutate the DB via monkeypatch or raw
    SQL without going through those admin endpoints, or intentionally
    trigger a downstream failure (e.g. monkeypatching OSRM to raise) for
    a route_id an earlier test already cached a success for. Without a
    reset, that earlier success gets served instead of ever calling the
    monkeypatched code, and the test fails for a reason that has nothing
    to do with what it's actually checking.

    Clearing on both sides of every test keeps this cache from ever
    leaking state across tests, the same way test_pathfinder_alternatives.py
    already resets the routing-graph cache around each of its tests.
    """
    invalidate_all()
    yield
    invalidate_all()


# ---------------------------------------------------------------------------
# Privileged-auth helpers
#
# The old `X-Admin-Api-Key` shared secret is disabled by default (see
# app/core/config.py::ALLOW_LEGACY_SHARED_ADMIN_KEY), so a test that wants
# to exercise a privileged endpoint has to authenticate the way a real
# caller does. Two ways, both available here:
#
#   admin_headers / editor_headers  -- a human AdminUser logging in via
#       POST /admin/login and sending the resulting JWT. Exercises the
#       role -> permission mapping.
#   service_key_headers             -- a scoped ServiceCredential, the
#       supported replacement for scripted/ETL callers. Exercises scope
#       enforcement, including the denials.
#
# Both clean up after themselves.
# ---------------------------------------------------------------------------


def _require_db() -> None:
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()


@pytest.fixture
def db_session() -> Session:
    """A real session for fixtures and assertions that need to read or
    write rows directly. Closed on teardown."""
    _require_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def _admin_user_factory():
    """Create an AdminUser, hand back its (username, admin_id), and delete
    it afterwards. Kept as a factory so a test can make both an editor and
    an admin without paying for two logins it doesn't need.
    """
    created: list[int] = []
    _require_db()

    def _make(role: str) -> tuple[str, int]:
        session = SessionLocal()
        try:
            username = f"test-{role}-{uuid.uuid4().hex[:8]}"
            row = AdminUser(username=username, password_hash=hash_password(TEST_PASSWORD), role=role)
            session.add(row)
            session.commit()
            created.append(row.admin_id)
            return username, row.admin_id
        finally:
            session.close()

    try:
        yield _make
    finally:
        session = SessionLocal()
        try:
            for admin_id in created:
                row = session.get(AdminUser, admin_id)
                if row is not None:
                    session.delete(row)
            session.commit()
        finally:
            session.close()


def _token_for(username: str, admin_id: int, role: str) -> str:
    """Mint an AdminUser JWT directly rather than via POST /admin/login.

    The login endpoint is rate limited to 10/minute per client IP (and
    every test in this process shares one in-memory limiter bucket and
    one TestClient address), so using it as the fixture's credential
    source would make unrelated tests fail with 429s depending on run
    order. The token is identical either way -- same claims, same
    signing key, same decoder in app/core/security.py -- so a fixture
    built this way exercises exactly the same authorization path as one
    that logged in. The endpoint itself is covered by
    tests/test_admin_auth_api.py, including its rate limit.
    """
    return create_access_token(admin_id=admin_id, username=username, role=role)


@pytest.fixture
def admin_headers(client, _admin_user_factory) -> dict:
    """Bearer headers for a human with the `admin` role."""
    username, admin_id = _admin_user_factory(ROLE_ADMIN)
    return {"Authorization": f"Bearer {_token_for(username, admin_id, ROLE_ADMIN)}"}


@pytest.fixture
def editor_headers(client, _admin_user_factory) -> dict:
    """Bearer headers for a human with the `editor` role.

    Note `admin` is a strict superset, so this is the only human role
    that meaningfully tests a refusal: an `admin` token is refused by
    nothing. Where a test needs "authenticated but not permitted", it
    either uses this fixture or a narrower service credential.
    """
    username, admin_id = _admin_user_factory(ROLE_EDITOR)
    return {"Authorization": f"Bearer {_token_for(username, admin_id, ROLE_EDITOR)}"}


@pytest.fixture
def service_key_factory():
    """Mint scoped service credentials directly in the database.

    Minted through the model rather than through
    POST /admin/service-credentials so a test can create a credential
    with an arbitrary scope set without first needing an admin token --
    which would make every scope test depend on the credential endpoints
    working. The credential endpoints themselves are covered directly in
    tests/test_service_credentials_api.py.

    Yields a callable taking a list of scopes and returning the full
    "SvcKey <key_id>.<secret>" value. Rows are deleted on teardown.
    """
    _require_db()
    created: list[str] = []

    def _mint(scopes: list[str], name: str | None = None) -> str:
        presented, key_id, key_hash = generate_service_key()
        session = SessionLocal()
        try:
            session.add(
                ServiceCredential(
                    key_id=key_id,
                    name=name or f"test-cred-{uuid.uuid4().hex[:8]}",
                    key_hash=key_hash,
                    scopes=list(scopes),
                )
            )
            session.commit()
        finally:
            session.close()
        created.append(key_id)
        return presented

    try:
        yield _mint
    finally:
        session = SessionLocal()
        try:
            for key_id in created:
                session.execute(
                    text("DELETE FROM service_credentials WHERE key_id = :kid"), {"kid": key_id}
                )
            session.commit()
        finally:
            session.close()


@pytest.fixture
def service_key_headers(service_key_factory):
    """Factory turning a scope list into request headers:
    ``svc("stops:write")["Authorization"]`` is ready to pass to a client
    call. Defaults to the full editor set, which is what the data-entry
    tests that used to send the shared key actually need.
    """
    from app.core.security import EDITOR_PERMISSIONS

    def _headers(scopes=None) -> dict:
        presented = service_key_factory(sorted(scopes or EDITOR_PERMISSIONS))
        return {"Authorization": presented}

    return _headers


@pytest.fixture
def data_headers(service_key_headers):
    """Scoped credential holding the full editor permission set.

    This is the credential an ETL / data-import caller should be given:
    it can grow and correct the dataset but cannot flip a route's status
    or reload the graph. Tests that used to send the old shared
    `X-Admin-Api-Key` (which was all-access) use this instead, so the
    whole data-entry suite now also proves a credential grants exactly
    what it claims.
    """
    return service_key_headers(sorted(EDITOR_PERMISSIONS))


@pytest.fixture
def review_headers(service_key_headers):
    """Scoped credential holding only `suggestions:review`.

    Deliberately narrower than `data_headers`: suggestion review is a
    separate permission, so a moderation key needn't be able to edit the
    dataset. Several tests assert this credential is refused on
    /stops and allowed on /admin/suggestions.
    """
    return service_key_headers([PERM_SUGGESTIONS_REVIEW])


@pytest.fixture
def audit_rows():
    """Read admin_audit_log, newest last. Returns a list of AdminAuditLog
    rows so a test can assert on action/actor/success/detail.

    Only rows written *during this test* are visible: tests that care
    about isolation should call this after the action they care about and
    filter by the action name, since the table is shared and other test
    files write to it in the same process.
    """
    _require_db()

    def _read(action: str | None = None) -> list[AdminAuditLog]:
        session = SessionLocal()
        try:
            stmt = select(AdminAuditLog).order_by(AdminAuditLog.id)
            if action is not None:
                stmt = stmt.where(AdminAuditLog.action == action)
            return list(session.scalars(stmt))
        finally:
            session.close()

    return _read


@pytest.fixture
def clear_audit_log():
    """Empty admin_audit_log before and after a test.

    Only safe against a disposable database: this is a DELETE, not a
    transaction-scoped view. Tests that need it are asserting on
    "how many rows does this action produce", which is meaningless
    against a table other test files are also writing to. For everything
    else, use the `audit_rows` fixture and filter by action.
    """
    _require_db()
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM admin_audit_log"))
        session.commit()
    finally:
        session.close()
    yield
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM admin_audit_log"))
        session.commit()
    finally:
        session.close()
