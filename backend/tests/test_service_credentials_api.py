"""
API-level tests for the service-credential lifecycle and the audit-log
reader (app/api/service_credentials.py) -- require a live
Postgres/PostGIS instance with migration b1c2d3e4f5a6 applied. Skip
cleanly if no DB is reachable.

The point of these endpoints is that a credential is a *managed* thing:
scoped at creation, revocable, attributable, and stored hashed. Each of
those is a separate property and each gets its own coverage below,
because a credential scheme can be scoped-but-unrevocable, or
revocable-but-attributable-only-to-nobody, or hashed-but-logged.

Self-contained: every credential minted here is revoked and deleted in
the fixture teardown.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import (
    PERM_GRAPH_RELOAD,
    PERM_ROUTES_STATUS,
    PERM_SUGGESTIONS_REVIEW,
    PERM_STOPS_WRITE,
    ROLE_ADMIN,
    create_access_token,
    generate_service_key,
    hash_password,
    hash_service_secret,
    parse_service_key,
)
from app.db.session import SessionLocal
from app.models import AdminUser


@pytest.fixture
def client():
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("No live database available — run `docker compose up -d db` first")
    finally:
        session.close()
    return TestClient(app)


@pytest.fixture
def mint(client, admin_headers):
    """POST /admin/service-credentials, with teardown."""
    created: list[str] = []

    def _mint(name: str | None = None, scopes=None, expires_at: str | None = None):
        body = {
            "name": name or f"test-cred-{uuid.uuid4().hex[:8]}",
            "scopes": scopes if scopes is not None else [PERM_STOPS_WRITE],
        }
        if expires_at is not None:
            body["expires_at"] = expires_at
        resp = client.post("/admin/service-credentials", json=body, headers=admin_headers)
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["key_id"])
        return resp.json()

    try:
        yield _mint
    finally:
        session = SessionLocal()
        try:
            for key_id in created:
                session.execute(
                    text("DELETE FROM service_credentials WHERE key_id = :kid"),
                    {"kid": key_id},
                )
            session.commit()
        finally:
            session.close()


def mint_admin() -> dict:
    """A short-lived human admin JWT.

    Needed wherever a test wants a *second* admin view of the same tables
    (minting a credential, then listing it) so the assertion isn't made
    with the same call that created the row. Direct token issue rather
    than POST /admin/login: the login path is rate-limited at
    10/minute, and a suite that logs in for every test would trip it.
    """
    session = SessionLocal()
    username = f"test-admin-{uuid.uuid4().hex[:8]}"
    try:
        row = AdminUser(
            username=username, password_hash=hash_password("x"), role=ROLE_ADMIN
        )
        session.add(row)
        session.commit()
        admin_id = row.admin_id
    finally:
        session.close()
    token = create_access_token(admin_id=admin_id, username=username, role=ROLE_ADMIN)
    return {"Authorization": f"Bearer {token}"}


# --- creating -------------------------------------------------------------


def test_create_returns_the_key_exactly_once(client, mint):
    body = mint(scopes=[PERM_STOPS_WRITE])
    key = body["key"]
    key_id = body["key_id"]

    # The presented value is the full scheme + key_id.secret, and the
    # public half is recoverable from it.
    assert key.startswith("SvcKey ")
    parsed = parse_service_key(key)
    assert parsed is not None
    assert parsed[0] == key_id
    assert parsed[1]  # a non-empty secret

    # The listing never returns it, because it is not recoverable.
    listing = client.get("/admin/service-credentials", headers=mint_admin())
    row = next(r for r in listing.json() if r["key_id"] == key_id)
    assert "key" not in row
    assert "key_hash" not in row
    assert row["scopes"] == [PERM_STOPS_WRITE]
    assert row["active"] is True


def test_only_the_hash_is_stored(client, mint):
    """The secret must not be recoverable from the database. A fast hash
    is right here precisely because the input is 256 bits of CSPRNG
    output, so what protects it is access control, not KDF stretching."""
    body = mint()
    secret = parse_service_key(body["key"])[1]

    session = SessionLocal()
    try:
        stored = session.execute(
            text("SELECT key_hash FROM service_credentials WHERE key_id = :kid"),
            {"kid": body["key_id"]},
        ).scalar_one()
    finally:
        session.close()

    assert stored == hash_service_secret(secret)
    assert secret not in stored
    # No column anywhere in the row holds the raw key.
    assert len(stored) == 64  # SHA-256 hex


def test_rejects_unknown_scope(client, admin_headers):
    """A typo'd scope is a 422, not a credential that silently can do
    nothing -- or, if a permission is ever added later, something
    unintended."""
    resp = client.post(
        "/admin/service-credentials",
        json={"name": f"typo-{uuid.uuid4().hex[:6]}", "scopes": ["stops:wrte"]},
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert "stops:wrte" in resp.text


def test_rejects_empty_scope_list(client, admin_headers):
    """A credential with no permissions is never intentional."""
    resp = client.post(
        "/admin/service-credentials",
        json={"name": f"empty-{uuid.uuid4().hex[:6]}", "scopes": []},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_rejects_duplicate_name(client, mint):
    name = f"dup-{uuid.uuid4().hex[:8]}"
    mint(name=name)
    resp = client.post(
        "/admin/service-credentials",
        json={"name": name, "scopes": [PERM_STOPS_WRITE]},
        headers=mint_admin(),
    )
    assert resp.status_code == 409


def test_rejects_past_expiry(client, admin_headers):
    resp = client.post(
        "/admin/service-credentials",
        json={
            "name": f"past-{uuid.uuid4().hex[:6]}",
            "scopes": [PERM_STOPS_WRITE],
            "expires_at": "2000-01-01T00:00:00Z",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_deduplicates_repeated_scopes(client, mint):
    body = mint(
        scopes=[PERM_STOPS_WRITE, PERM_STOPS_WRITE, PERM_SUGGESTIONS_REVIEW]
    )
    assert body["scopes"] == [PERM_STOPS_WRITE, PERM_SUGGESTIONS_REVIEW]


# --- who may manage credentials -------------------------------------------


def test_requires_credentials(client):
    resp = client.post(
        "/admin/service-credentials",
        json={"name": "anon", "scopes": [PERM_STOPS_WRITE]},
    )
    assert resp.status_code == 401


def test_editor_cannot_mint_credentials(client, editor_headers):
    """Credential minting is a `require_role`, not a permission: there is
    no scope an automated caller can hold that lets it create more
    credentials. Otherwise a `stops:write` key could mint itself a
    `graph:reload` one and escalate without a human ever being involved.
    """
    resp = client.post(
        "/admin/service-credentials",
        json={"name": f"editor-{uuid.uuid4().hex[:6]}", "scopes": [PERM_STOPS_WRITE]},
        headers=editor_headers,
    )
    assert resp.status_code == 403


def test_service_credential_cannot_mint_credentials(client, mint):
    """The escalation path this closes, asserted directly: a credential
    with real scopes still cannot create another credential."""
    body = mint(scopes=[PERM_STOPS_WRITE, PERM_GRAPH_RELOAD, PERM_ROUTES_STATUS])
    resp = client.post(
        "/admin/service-credentials",
        json={"name": f"escalate-{uuid.uuid4().hex[:6]}", "scopes": [PERM_GRAPH_RELOAD]},
        headers={"Authorization": body["key"]},
    )
    assert resp.status_code == 403
    assert "human administrator" in resp.json()["detail"]


def test_credential_cannot_list_or_revoke_credentials(client, mint):
    body = mint()
    key = {"Authorization": body["key"]}
    assert client.get("/admin/service-credentials", headers=key).status_code == 403
    assert client.delete(f"/admin/service-credentials/{body['key_id']}", headers=key).status_code == 403
    assert client.get("/admin/audit-log", headers=key).status_code == 403


# --- the credential works -------------------------------------------------


def test_minted_credential_authenticates_with_exactly_its_scopes(client, mint):
    body = mint(scopes=[PERM_STOPS_WRITE])
    key = {"Authorization": body["key"]}

    allowed = client.post(
        "/stops",
        json={"stop_name": f"Svc Stop {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers=key,
    )
    assert allowed.status_code == 201, allowed.text
    stop_id = allowed.json()["stop_id"]

    # Cleanup, then confirm the same key is refused for anything it wasn't
    # scoped for.
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM stops WHERE stop_id = :sid"), {"sid": stop_id})
        session.commit()
    finally:
        session.close()

    assert client.post("/routes", json={"route_name": "n"}, headers=key).status_code == 403
    assert client.post("/graph/reload", headers=key).status_code == 403


def test_last_used_at_is_stamped(client, mint):
    body = mint(scopes=[PERM_SUGGESTIONS_REVIEW])
    assert body["last_used_at"] is None

    assert client.get("/admin/suggestions", headers={"Authorization": body["key"]}).status_code == 200

    listing = client.get("/admin/service-credentials", headers=mint_admin())
    row = next(r for r in listing.json() if r["key_id"] == body["key_id"])
    # Operational signal only: "spot a key nobody has used in months", not
    # an audit trail. The audit log is the audit trail.
    assert row["last_used_at"] is not None


# --- revoking -------------------------------------------------------------


def test_revoke_stops_the_credential_working(client, mint):
    body = mint(scopes=[PERM_SUGGESTIONS_REVIEW])
    key = {"Authorization": body["key"]}
    assert client.get("/admin/suggestions", headers=key).status_code == 200

    resp = client.delete(
        f"/admin/service-credentials/{body['key_id']}", headers=mint_admin()
    )
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
    assert resp.json()["already_revoked"] is False

    assert client.get("/admin/suggestions", headers=key).status_code == 401


def test_revoke_is_idempotent(client, mint):
    """An ops script re-running must not need to check first."""
    body = mint()
    headers = mint_admin()
    first = client.delete(f"/admin/service-credentials/{body['key_id']}", headers=headers)
    second = client.delete(f"/admin/service-credentials/{body['key_id']}", headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json()["already_revoked"] is True


def test_revoke_unknown_key_is_404(client, admin_headers):
    resp = client.delete("/admin/service-credentials/svc_000000000000", headers=admin_headers)
    assert resp.status_code == 404


def test_revoked_row_is_kept_not_deleted(client, mint):
    """The row is kept so audit entries referencing this key_id still
    resolve, and so an operator can see that a key existed and was
    revoked rather than wondering where it went."""
    body = mint()
    client.delete(f"/admin/service-credentials/{body['key_id']}", headers=mint_admin())
    listing = client.get("/admin/service-credentials", headers=mint_admin())
    row = next(r for r in listing.json() if r["key_id"] == body["key_id"])
    assert row["active"] is False
    assert row["revoked_at"] is not None


# --- the credential lifecycle is audited ----------------------------------


def test_credential_lifecycle_is_audited(client, mint, audit_rows):
    before_create = len(audit_rows("service_credential.create"))
    before_revoke = len(audit_rows("service_credential.revoke"))

    body = mint(scopes=[PERM_STOPS_WRITE], name=f"audited-{uuid.uuid4().hex[:6]}")
    client.delete(f"/admin/service-credentials/{body['key_id']}", headers=mint_admin())

    creates = audit_rows("service_credential.create")
    assert len(creates) == before_create + 1
    assert creates[-1].resource_id == body["key_id"]
    assert creates[-1].success is True
    assert creates[-1].detail["scopes"] == [PERM_STOPS_WRITE]

    revokes = audit_rows("service_credential.revoke")
    assert len(revokes) == before_revoke + 1
    assert revokes[-1].resource_id == body["key_id"]


def test_audit_never_records_the_key(client, mint, audit_rows):
    """The create row records the name and the scopes. The key is not
    passed to the audit layer at all, and could not be recorded even if a
    future change tried -- `key` is on the redaction list."""
    body = mint(name=f"nokey-{uuid.uuid4().hex[:6]}")
    secret = parse_service_key(body["key"])[1]
    rows = audit_rows("service_credential.create")
    assert rows, "expected a create row"
    assert secret not in str(rows[-1].detail)
    assert body["key"] not in str(rows[-1].detail)


# --- the audit reader -----------------------------------------------------


def test_audit_log_requires_admin(client):
    assert client.get("/admin/audit-log").status_code == 401
    assert client.get("/admin/audit-log", headers=mint_admin()).status_code == 200


def test_audit_log_is_newest_first_and_capped(client, mint, admin_headers):
    mint(scopes=[PERM_STOPS_WRITE])
    body = client.get("/admin/audit-log?limit=5", headers=admin_headers).json()
    assert len(body) <= 5
    timestamps = [row["timestamp"] for row in body]
    assert timestamps == sorted(timestamps, reverse=True)


def test_audit_log_filters_by_action(client, mint, admin_headers, audit_rows):
    mint(scopes=[PERM_STOPS_WRITE])
    rows = client.get(
        "/admin/audit-log?action=service_credential.create&limit=50", headers=admin_headers
    ).json()
    assert rows, "expected at least the create row just written"
    assert {r["action"] for r in rows} == {"service_credential.create"}


def test_audit_log_limit_is_clamped(client, admin_headers):
    """A single call must not be able to pull the whole table."""
    body = client.get("/admin/audit-log?limit=100000", headers=admin_headers).json()
    assert len(body) <= 1000
    assert client.get("/admin/audit-log?limit=0", headers=admin_headers).status_code == 200


def test_audit_log_has_no_write_path(client, admin_headers):
    """Append-only is enforced by the API having no mutating route here at
    all. This is application-level, not database-level: an account with
    direct table access can still modify these rows, which docs/security.md
    records as remaining work."""
    for method in ("POST", "PATCH", "DELETE", "PUT"):
        resp = client.request(method, "/admin/audit-log", json={}, headers=admin_headers)
        assert resp.status_code == 405, f"{method} /admin/audit-log returned {resp.status_code}"


# --- key format ------------------------------------------------------------


@pytest.mark.parametrize(
    "presented",
    [
        "",
        "SvcKey",
        "SvcKey ",
        "SvcKey nodot",
        "SvcKey .secret",
        "SvcKey keyid.",
        "Bearer abc.def",
        "svckey svc_abc.def",     # wrong case on the scheme
        "SvcKey  noid.abc",       # wrong whitespace
    ],
)
def test_parse_rejects_malformed_keys(presented):
    """Strict parsing is what lets require_admin treat a malformed SvcKey
    as a failed credential rather than falling through to interpret the
    same header as something else."""
    assert parse_service_key(presented) is None


def test_generated_keys_are_unique():
    ids = {generate_service_key()[1] for _ in range(200)}
    assert len(ids) == 200
