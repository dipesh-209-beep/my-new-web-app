"""
API-level tests for POST /admin/login — require a live Postgres/PostGIS
instance (docker compose up -d db) with migration 2f29b3e3e5fd (admin_users)
applied. Skip cleanly if no DB is reachable.

Fully self-contained: creates and tears down its own AdminUser row rather
than depending on any seeded admin account, so it can run against any DB
that just has migrations applied.
"""
import uuid

import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import AdminUser

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The 10/minute limit on POST /admin/login is keyed by remote address,
    and TestClient requests all share the same address -- without this,
    earlier tests in this file would eat into the rate-limit test's
    budget (or vice versa). Reset slowapi's in-memory bucket around every
    test so each one starts with a clean limit window."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    session = SessionLocal()
    try:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()
    return TestClient(app)


@pytest.fixture
def admin_user():
    """Creates a throwaway AdminUser with a unique username, deletes it after."""
    session = SessionLocal()
    username = f"test-admin-{uuid.uuid4().hex[:8]}"
    admin = AdminUser(username=username, password_hash=hash_password(TEST_PASSWORD), role="admin")
    session.add(admin)
    session.commit()
    session.refresh(admin)
    admin_id = admin.admin_id
    session.close()
    try:
        yield username
    finally:
        session = SessionLocal()
        row = session.get(AdminUser, admin_id)
        if row is not None:
            session.delete(row)
            session.commit()
        session.close()


def test_login_succeeds_with_correct_credentials_and_returns_valid_jwt(client, admin_user):
    resp = client.post("/admin/login", json={"username": admin_user, "password": TEST_PASSWORD})
    assert resp.status_code == 200

    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    settings = get_settings()
    payload = jwt.decode(body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["username"] == admin_user
    assert payload["role"] == "admin"


def test_login_rejects_wrong_password(client, admin_user):
    resp = client.post("/admin/login", json={"username": admin_user, "password": "not-the-password"})
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


def test_login_rejects_unknown_username(client):
    resp = client.post(
        "/admin/login",
        json={"username": f"does-not-exist-{uuid.uuid4().hex[:8]}", "password": "whatever"},
    )
    assert resp.status_code == 401


def test_login_does_not_leak_whether_username_exists(client, admin_user):
    """Unknown-username and wrong-password cases must return the same status
    code and detail message, so a caller can't enumerate valid usernames by
    comparing responses."""
    unknown_resp = client.post(
        "/admin/login",
        json={"username": f"does-not-exist-{uuid.uuid4().hex[:8]}", "password": "whatever"},
    )
    wrong_pw_resp = client.post(
        "/admin/login", json={"username": admin_user, "password": "not-the-password"}
    )
    assert unknown_resp.status_code == wrong_pw_resp.status_code == 401
    assert unknown_resp.json()["detail"] == wrong_pw_resp.json()["detail"]


def test_login_rejects_empty_password(client, admin_user):
    resp = client.post("/admin/login", json={"username": admin_user, "password": ""})
    assert resp.status_code == 422


def test_get_current_admin_rejects_garbage_token(client):
    """Exercises get_current_admin (app/core/security.py) directly against
    a garbage token, independent of any specific route wiring."""
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core.security import get_current_admin

    session = SessionLocal()
    try:
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-real-jwt")
        with pytest.raises(HTTPException) as exc_info:
            get_current_admin(credentials=creds, db=session)
        assert exc_info.value.status_code == 401
    finally:
        session.close()


def test_login_token_grants_access_to_admin_write_endpoint(client, admin_user):
    """require_admin (app/core/security.py), the dependency behind
    app/api/admin.py, accepts a bearer token from POST /admin/login as an
    alternative to X-Admin-Api-Key. Previously nothing verified issued
    tokens at all -- this pins that a real login token now actually
    grants access to a protected route."""
    login_resp = client.post("/admin/login", json={"username": admin_user, "password": TEST_PASSWORD})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/stops",
        json={"stop_name": f"Test Stop JWT {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    # Clean up -- this test doesn't use the two_stops fixture, so delete
    # directly.
    from app.db.session import SessionLocal as _SessionLocal
    from app.models import Stop

    session = _SessionLocal()
    try:
        row = session.get(Stop, resp.json()["stop_id"])
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()


def test_garbage_bearer_token_rejected_by_admin_write_endpoint(client):
    """require_admin must reject an invalid bearer token exactly like a
    missing/wrong X-Admin-Api-Key, not silently fall through."""
    resp = client.post(
        "/stops",
        json={"stop_name": "Should Not Be Created", "lat": 27.7, "lng": 85.3},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert resp.status_code == 401


def test_login_rate_limited_after_ten_attempts_per_minute(client, admin_user):
    """@limiter.limit("10/minute") on POST /admin/login. slowapi keys by
    remote address; TestClient requests all share the same address, so
    eleven rapid requests should trip the limit on the eleventh.

    Raised from 5 to 10 deliberately. The old limit was tight enough to be
    an operational hazard: a handful of admins behind one NAT address (a
    shared office, a university VPN) share a single bucket, so a few
    fat-fingered password entries could lock every one of them out of the
    data-entry UI for the rest of the window. Ten still caps an online
    attack at a few hundred guesses an hour per address, since each
    attempt pays a full bcrypt verification, and it remains a *rate*
    limit rather than a lockout: nothing is disabled permanently.
    """
    statuses = []
    for _ in range(11):
        resp = client.post(
            "/admin/login", json={"username": admin_user, "password": "not-the-password"}
        )
        statuses.append(resp.status_code)

    assert statuses[:10] == [401] * 10, f"Expected first 10 attempts to be plain 401s, got {statuses[:10]}"
    assert statuses[10] == 429, f"Expected the 11th attempt within the same minute to be rate-limited, got {statuses[10]}"


def test_successful_login_is_audited(client, admin_user, audit_rows):
    """A successful login is as much of an audit event as a failed one --
    it is the only way to establish that a given account was used at a
    given time from a given address."""
    before_success = len(audit_rows("admin.login.success"))
    before_failure = len(audit_rows("admin.login.failure"))
    resp = client.post("/admin/login", json={"username": admin_user, "password": TEST_PASSWORD})
    assert resp.status_code == 200, resp.text

    successes = audit_rows("admin.login.success")
    assert len(successes) == before_success + 1
    row = successes[-1]
    assert row.success is True
    assert row.actor_type == "admin_user"
    assert row.actor_id == admin_user
    assert row.detail["role"] in ("admin", "editor")
    # The password is never a candidate for the audit row at all.
    assert TEST_PASSWORD not in str(row.detail)
    # ...and neither is the token that was just issued.
    assert resp.json()["access_token"] not in str(row.detail)


def test_failed_login_is_audited_without_the_password(client, admin_user, audit_rows):
    before = len(audit_rows("admin.login.failure"))
    resp = client.post(
        "/admin/login", json={"username": admin_user, "password": "wrong-password"}
    )
    assert resp.status_code == 401

    rows = audit_rows("admin.login.failure")
    assert len(rows) == before + 1
    row = rows[-1]
    assert row.success is False
    # The attempted username is an identifier an operator needs; the
    # submitted password is not recorded anywhere.
    assert row.resource_id == admin_user
    assert "wrong-password" not in str(row.detail)
    assert row.detail["reason"] == "invalid_credentials"


def test_unknown_username_is_indistinguishable_from_wrong_password(client, audit_rows):
    """A distinct reply for "no such user" would let anyone enumerate
    valid admin usernames, which is the first step of a credential
    attack. Both paths must produce the same status and the same body."""
    known = client.post("/admin/login", json={"username": "no-such-admin-xyz", "password": "x"})
    unknown = client.post("/admin/login", json={"username": "no-such-admin-xyz", "password": "y"})
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


def test_login_audit_does_not_break_the_401(client, admin_user):
    """A failure to write the audit row must not change the outcome the
    caller was already going to get. Turning an auth failure into a 500
    because of an audit-table problem would be a worse outcome, not a
    safer one, so this asserts the 401 survives even with the audit table
    made unwritable."""
    session = SessionLocal()
    try:
        session.execute(text("ALTER TABLE admin_audit_log RENAME TO admin_audit_log_hidden"))
        session.commit()
    finally:
        session.close()

    try:
        resp = client.post(
            "/admin/login", json={"username": admin_user, "password": "not-the-password"}
        )
        assert resp.status_code == 401
    finally:
        session = SessionLocal()
        try:
            session.execute(text("ALTER TABLE admin_audit_log_hidden RENAME TO admin_audit_log"))
            session.commit()
        finally:
            session.close()
