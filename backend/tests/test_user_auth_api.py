"""
API-level tests for POST /auth/register and POST /auth/login (public user
accounts) -- require a live Postgres/PostGIS instance (docker compose up -d
db) with migration c3d4e5f6a7b8 (users table) applied. Skip cleanly if no DB
is reachable.

Fully self-contained: every test creates and tears down its own User row
rather than depending on any seeded account, so it can run against any DB
that just has migrations applied.

The cross-audience tests (a user token must not satisfy require_admin, and
an admin token must not satisfy get_current_user) matter because users and
admin_users both autoincrement their PKs from 1, so sub values collide --
only the "type" claim in app/core/security.py separate the two.
"""
import uuid

import jwt
import pytest
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import SessionLocal
from app.models import User

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Same rationale as test_admin_auth_api.py: the 5/minute limits on
    /auth/register and /auth/login are keyed by remote address and TestClient
    requests all share one, so reset slowapi's in-memory bucket around every
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
def user_account(client):
    """Registers a throwaway user (unique username) via the API, decodes the
    returned token to grab its user_id, and deletes the row afterwards."""
    username = f"test-user-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/auth/register",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    user_id = int(
        jwt.decode(token, get_settings().jwt_secret_key, algorithms=[get_settings().jwt_algorithm])["sub"]
    )
    try:
        yield username, token
    finally:
        session = SessionLocal()
        row = session.get(User, user_id)
        if row is not None:
            session.delete(row)
            session.commit()
        session.close()


def test_register_succeeds_and_returns_user_scoped_token(client):
    username = f"test-user-{uuid.uuid4().hex[:8]}"
    resp = client.post("/auth/register", json={"username": username, "password": TEST_PASSWORD})
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    settings = get_settings()
    payload = jwt.decode(body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["username"] == username
    assert payload["type"] == "user"
    assert "role" not in payload

    # Clean up
    user_id = int(payload["sub"])
    session = SessionLocal()
    try:
        row = session.get(User, user_id)
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()


def test_register_rejects_duplicate_username(client, user_account):
    username, _ = user_account
    resp = client.post("/auth/register", json={"username": username, "password": TEST_PASSWORD})
    assert resp.status_code == 409
    assert "already taken" in resp.json()["detail"]


def test_register_rejects_too_short_username_and_password(client):
    resp = client.post(
        "/auth/register", json={"username": "ab", "password": "correct-horse-battery-staple"}
    )
    assert resp.status_code == 422

    resp = client.post(
        "/auth/register", json={"username": "validusername", "password": "short"}
    )
    assert resp.status_code == 422


def test_login_succeeds_with_correct_credentials(client, user_account):
    username, _ = user_account
    resp = client.post("/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert resp.status_code == 200

    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    settings = get_settings()
    payload = jwt.decode(body["access_token"], settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["username"] == username
    assert payload["type"] == "user"


def test_login_rejects_wrong_password_and_unknown_username_with_same_error(client, user_account):
    """Must not leak whether a username exists -- same 401 + same detail for
    both cases, mirroring the admin login behavior."""
    username, _ = user_account
    unknown_resp = client.post(
        "/auth/login",
        json={"username": f"does-not-exist-{uuid.uuid4().hex[:8]}", "password": "whatever"},
    )
    wrong_pw_resp = client.post(
        "/auth/login", json={"username": username, "password": "not-the-password"}
    )
    assert unknown_resp.status_code == wrong_pw_resp.status_code == 401
    assert unknown_resp.json()["detail"] == wrong_pw_resp.json()["detail"]
    assert "access_token" not in wrong_pw_resp.json()


def test_user_token_rejected_by_admin_write_endpoint(client, user_account):
    """require_admin (app/core/security.py) must reject a public user token:
    users.user_id and admin_users.admin_id both autoincrement from 1, so the
    first user and first admin share sub="1" -- without the "type" claim a
    user token could masquerade as the admin with the same id."""
    _, token = user_account
    resp = client.post(
        "/stops",
        json={"stop_name": f"Should Not Be Created {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_admin_token_rejected_by_user_auth(client, user_account):
    """get_current_user must not accept an admin token. Exercises it directly
    (there are no user-only endpoints until suggestions land) by minting an
    admin JWT and confirming it comes back 401."""
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core.security import create_access_token, get_current_user

    username = f"test-user-{uuid.uuid4().hex[:8]}"
    resp = client.post("/auth/register", json={"username": username, "password": TEST_PASSWORD})
    assert resp.status_code == 201, resp.text
    user_id = int(
        jwt.decode(resp.json()["access_token"], get_settings().jwt_secret_key, algorithms=[get_settings().jwt_algorithm])["sub"]
    )

    admin_token = create_access_token(admin_id=1, username="should-not-matter", role="admin")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=admin_token)
    session = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(credentials=creds, db=session)
        assert exc_info.value.status_code == 401
    finally:
        session.close()
        session = SessionLocal()
        row = session.get(User, user_id)
        if row is not None:
            session.delete(row)
            session.commit()
        session.close()


def test_register_rate_limited_after_five_per_minute(client):
    """@limiter.limit("5/minute") on POST /auth/register. Six rapid requests
    should trip the limit on the sixth (distinct endpoint bucket from
    /admin/login and /auth/login)."""
    statuses = []
    for i in range(6):
        resp = client.post(
            "/auth/register",
            json={"username": f"rate-user-{i}-{uuid.uuid4().hex[:4]}", "password": TEST_PASSWORD},
        )
        statuses.append(resp.status_code)

    assert statuses[:5] == [201] * 5, f"Expected first 5 registrations to succeed, got {statuses[:5]}"
    assert statuses[5] == 429, f"Expected the 6th registration within the same minute to be rate-limited, got {statuses[5]}"


def test_login_rate_limited_after_five_per_minute(client, user_account):
    """@limiter.limit("5/minute") on POST /auth/login -- separate bucket from
    /auth/register and /admin/login."""
    username, _ = user_account
    statuses = []
    for _ in range(6):
        resp = client.post(
            "/auth/login", json={"username": username, "password": "not-the-password"}
        )
        statuses.append(resp.status_code)

    assert statuses[:5] == [401] * 5, f"Expected first 5 attempts to be plain 401s, got {statuses[:5]}"
    assert statuses[5] == 429, f"Expected the 6th attempt within the same minute to be rate-limited, got {statuses[5]}"