"""
API-level tests for permission enforcement on the privileged routes
(app/core/security.py's require_permissions, wired into app/api/admin.py)
-- require a live Postgres/PostGIS instance (docker compose up -d db) with
migration b1c2d3e4f5a6 applied. Skip cleanly if no DB is reachable.

What changed and why this file was rewritten
--------------------------------------------
Authorization used to be expressed as "which of the two roles is this?"
(require_role(ROLE_EDITOR, ROLE_ADMIN) vs require_role(ROLE_ADMIN)) and
the shared X-Admin-Api-Key carried no role at all -- it was a full-access
bearer token. Both halves of that are now gone:

  * Endpoints declare a *permission* (app/core/security.py's PERM_*),
    and a caller's permissions come from their role if they're a human
    or from their stored scopes if they're a service credential. One
    vocabulary, checked the same way for both.
  * The shared key grants nothing by default.

So the interesting test surface is now three axes, and this file covers
all three:

  1. role -> permission mapping   (an editor may create a stop)
  2. a credential is limited to its own scopes, regardless of how much
     its holder "needs"
  3. admin-only permissions (routes:status, graph:reload) are refused to
     everyone else -- before the handler body runs, so the refusal
     doesn't leak whether the target route exists
"""
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import (
    ADMIN_ONLY_PERMISSIONS,
    ALL_PERMISSIONS,
    EDITOR_PERMISSIONS,
    PERM_GRAPH_RELOAD,
    PERM_ROUTES_STATUS,
    PERM_STOPS_WRITE,
    create_access_token,
    hash_password,
)
from app.db.session import SessionLocal
from app.models import AdminAuditLog, AdminUser, Stop


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


def _delete_stop(stop_id: str) -> None:
    session = SessionLocal()
    try:
        row = session.get(Stop, stop_id)
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()


def _create_stop(client, headers, name: str | None = None) -> str:
    resp = client.post(
        "/stops",
        json={
            "stop_name": name or f"Perm Test Stop {uuid.uuid4().hex[:6]}",
            "lat": 27.7,
            "lng": 85.3,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["stop_id"]


# --- 1. role -> permission mapping -----------------------------------------


def test_editor_can_create_stop(client, editor_headers):
    stop_id = _create_stop(client, editor_headers)
    _delete_stop(stop_id)


def test_admin_can_create_stop(client, admin_headers):
    """admin is a strict superset of editor, not a separate lane, so it
    has to satisfy the same routine-data-entry gate."""
    stop_id = _create_stop(client, admin_headers)
    _delete_stop(stop_id)


def test_editor_permission_set_excludes_admin_only_permissions():
    """The role table is the contract, so assert it directly rather than
    inferring it from endpoint behaviour. An editor gaining routes:status
    or graph:reload by accident is exactly the regression worth catching:
    both change what /route-finder returns to real users immediately."""
    assert EDITOR_PERMISSIONS & ADMIN_ONLY_PERMISSIONS == frozenset()
    assert EDITOR_PERMISSIONS | ADMIN_ONLY_PERMISSIONS == ALL_PERMISSIONS


# --- 2. service credentials are limited to their own scopes ----------------


def test_credential_with_matching_scope_is_allowed(client, service_key_headers):
    stop_id = _create_stop(client, service_key_headers([PERM_STOPS_WRITE]))
    _delete_stop(stop_id)


def test_credential_without_matching_scope_is_refused(client, service_key_headers):
    resp = client.post(
        "/stops",
        json={"stop_name": f"Out of Scope {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers=service_key_headers(["routes:write"]),
    )
    assert resp.status_code == 403
    assert PERM_STOPS_WRITE in resp.json()["detail"]


def test_credential_with_narrow_scope_cannot_reload_graph(client, service_key_headers):
    """A credential holding the entire editor set still cannot reload the
    graph. Scope is a ceiling, not a suggestion."""
    resp = client.post("/graph/reload", headers=service_key_headers(sorted(EDITOR_PERMISSIONS)))
    assert resp.status_code == 403
    assert PERM_GRAPH_RELOAD in resp.json()["detail"]


def test_credential_with_admin_only_scope_cannot_do_data_entry(
    client, service_key_headers
):
    """The mirror image: a credential scoped to the admin-only permission
    doesn't thereby gain the data-entry ones. Permissions don't imply
    each other in either direction."""
    resp = client.post(
        "/stops",
        json={"stop_name": f"Wrong Direction {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers=service_key_headers([PERM_GRAPH_RELOAD]),
    )
    assert resp.status_code == 403


def test_revoked_credential_is_refused(client, service_key_factory):
    from datetime import datetime, timezone

    from app.models import ServiceCredential

    presented = service_key_factory([PERM_STOPS_WRITE])
    key_id = presented.split(" ")[1].split(".")[0]

    session = SessionLocal()
    try:
        row = session.scalar(select(ServiceCredential).where(ServiceCredential.key_id == key_id))
        row.revoked_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()

    resp = client.post(
        "/stops",
        json={"stop_name": f"Revoked {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers={"Authorization": presented},
    )
    assert resp.status_code == 401


def test_expired_credential_is_refused(client, service_key_factory):
    from datetime import datetime, timedelta, timezone

    from app.models import ServiceCredential

    presented = service_key_factory([PERM_STOPS_WRITE])
    key_id = presented.split(" ")[1].split(".")[0]
    session = SessionLocal()
    try:
        row = session.scalar(select(ServiceCredential).where(ServiceCredential.key_id == key_id))
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    finally:
        session.close()

    resp = client.post(
        "/stops",
        json={"stop_name": f"Expired {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers={"Authorization": presented},
    )
    assert resp.status_code == 401


# --- 3. admin-only permissions --------------------------------------------


def test_editor_forbidden_from_route_status_update(client, editor_headers):
    resp = client.patch(
        "/routes/R_DOES_NOT_EXIST/status",
        json={"status": "active"},
        headers=editor_headers,
    )
    # The permission gate must fire before the route lookup, so the status
    # code is 403 and not 404: an editor must not be able to learn whether
    # a given route_id exists through this endpoint.
    assert resp.status_code == 403, resp.text
    assert PERM_ROUTES_STATUS in resp.json()["detail"]


def test_editor_forbidden_from_graph_reload(client, editor_headers):
    resp = client.post("/graph/reload", headers=editor_headers)
    assert resp.status_code == 403
    assert PERM_GRAPH_RELOAD in resp.json()["detail"]


def test_admin_allowed_to_reload_graph(client, admin_headers):
    resp = client.post("/graph/reload", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "nodes" in body and "edges" in body


# --- 4. refusals are recorded ---------------------------------------------


def test_authorization_denial_is_audited(client, editor_headers, audit_rows):
    """A 403 is a more interesting event than a 401: the caller proved who
    they are and were still refused. That is what an escalation attempt
    looks like, so it has to be on the record."""
    before = len(audit_rows("authorization.denied"))
    resp = client.post("/graph/reload", headers=editor_headers)
    assert resp.status_code == 403

    rows = audit_rows("authorization.denied")
    assert len(rows) == before + 1
    row = rows[-1]
    assert row.success is False
    assert row.actor_type == "admin_user"
    assert row.detail["required_permissions"] == [PERM_GRAPH_RELOAD]
    assert row.detail["held_permissions"] == sorted(EDITOR_PERMISSIONS)


def test_failed_authentication_is_audited(client, audit_rows):
    before = len(audit_rows("auth.missing_credentials"))
    resp = client.post("/graph/reload")
    assert resp.status_code == 401

    rows = audit_rows("auth.missing_credentials")
    assert len(rows) == before + 1
    assert rows[-1].success is False
    assert rows[-1].actor_type == "anonymous"
    assert rows[-1].actor_id is None


def test_invalid_service_credential_is_audited(client, service_key_factory, audit_rows):
    before = len(audit_rows("auth.invalid_service_credential"))
    presented = service_key_factory([PERM_STOPS_WRITE])
    tampered = presented[:-4] + "zzzz"
    resp = client.post(
        "/stops",
        json={"stop_name": f"Bad Key {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers={"Authorization": tampered},
    )
    assert resp.status_code == 401

    rows = audit_rows("auth.invalid_service_credential")
    assert len(rows) == before + 1
    assert rows[-1].success is False
    # The public key_id is recorded so an operator can see which key is
    # being guessed at. The secret is not, and cannot be -- it is not
    # passed to the audit layer at all.
    assert rows[-1].actor_id == presented.split(" ")[1].split(".")[0]
    assert tampered.split(".")[-1] not in (rows[-1].detail or {}).values()


# --- 5. a user token is not an admin token -------------------------------


def test_public_user_token_cannot_reach_admin_endpoints(client):
    """Both audiences are signed with the same key and both `sub` values
    are serially autoincrementing from 1, so without a `type` claim the
    first user and the first admin would be the same subject string.
    This pins the audience separation that the `type` claim provides."""
    from app.core.security import create_user_token

    token = create_user_token(user_id=1, username="not-an-admin")
    resp = client.post("/graph/reload", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_admin_token_cannot_reach_user_endpoints(client, _admin_user_factory):
    """...and the same in the other direction: an admin token presented to
    a public-user endpoint must not authenticate as a user."""
    from app.core.security import ROLE_ADMIN

    username, admin_id = _admin_user_factory(ROLE_ADMIN)
    token = create_access_token(admin_id=admin_id, username=username, role=ROLE_ADMIN)
    resp = client.post(
        "/suggestions",
        json={
            "target_type": "stop",
            "target_id": "S0018",
            "suggestion_type": "stop_name_change",
            "payload": {"stop_name": "Should Not Work"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


# --- 6. no-credential paths stay open -------------------------------------


def test_public_endpoints_need_no_credential(client):
    """The permission model is additive: hardening the admin surface must
    not have closed the public API along with it."""
    assert client.get("/stops", params={"limit": 1}).status_code == 200
    assert client.get("/routes", params={"limit": 1}).status_code == 200
    assert client.get("/health").status_code == 200


# --- 7. the audit row and the mutation are one transaction ----------------


def test_audit_row_is_written_with_the_mutation(client, admin_headers, db_session, audit_rows):
    """Staged on the same session as the INSERT, so a rollback takes the
    audit row with it -- there is no window where a stop exists with no
    record of who added it."""
    before = len(audit_rows("stop.create"))
    stop_id = _create_stop(client, admin_headers)
    try:
        rows = audit_rows("stop.create")
        assert len(rows) == before + 1
        row = rows[-1]
        assert row.success is True
        assert row.actor_type == "service_credential" or row.actor_type == "admin_user"
        assert row.resource_type == "stop"
        assert row.resource_id == stop_id
        # The audit row's own commit landed with the stop.
        assert db_session.get(Stop, stop_id) is not None
    finally:
        _delete_stop(stop_id)


def test_audit_detail_never_contains_a_credential(client, admin_headers, audit_rows):
    """The scrubber is the backstop that stops a future endpoint from
    helpfully attaching the thing the user typed into its audit detail."""
    from app.core.admin_audit import scrub_detail

    scrubbed = scrub_detail(
        {
            "stop_name": "Kalighat",
            "password": "hunter2",
            "service_key": "SvcKey svc_abc.def",
            "new_token": "eyJhbGciOi...",
            "raw_authorization": "Bearer eyJ...",
            "lat": 27.7,
            "lng": 85.3,
            "api_key": "sk-live-1234",
        }
    )
    assert scrubbed["stop_name"] == "Kalighat"
    for key in ("password", "service_key", "new_token", "raw_authorization", "lat", "lng", "api_key"):
        assert scrubbed[key] == "[redacted]", key


def test_denial_audit_distinguishes_why(client, admin_headers, service_key_headers, audit_rows):
    """A 403 is the event you most want to explain afterwards, and the
    explanation is not in the HTTP body -- bodies are transient, the audit
    row is not. So the row has to say which gate refused, because
    "missing_permissions" and "service_credential_not_permitted" call for
    completely different responses: the first is a mis-scoped key, the
    second is either a mis-configuration or an escalation attempt.
    """
    stops_only = service_key_headers(["stops:write"])
    before = len(audit_rows())

    # Out of scope: authenticated, but missing a permission.
    resp = client.patch("/routes/R1/status", json={"status": "suspended"}, headers=stops_only)
    assert resp.status_code == 403

    # In scope for data, but automation is not permitted at all.
    resp = client.post(
        "/admin/service-credentials",
        json={"name": f"probe-{uuid.uuid4().hex[:6]}", "scopes": ["stops:write"]},
        headers=stops_only,
    )
    assert resp.status_code == 403

    denials = [r for r in audit_rows()[before:]]
    assert len(denials) == 2, [r.action for r in denials]

    rows_by_reason: dict[str, list] = {}
    for row in denials:
        rows_by_reason.setdefault((row.detail or {}).get("reason"), []).append(row)

    missing = rows_by_reason["missing_permissions"]
    assert missing[0].detail["required_permissions"] == ["routes:status"]
    assert missing[0].detail["held_permissions"] == ["stops:write"]
    assert "required_roles" not in missing[0].detail

    automation = rows_by_reason["service_credential_not_permitted"]
    assert automation[0].detail["required_roles"] == ["admin"]
    assert automation[0].actor_type == "service_credential"


def test_legacy_key_attempt_is_distinguished_from_no_credentials(
    client, audit_rows
):
    """Both are 401s with an identical body, so nothing in the response
    tells you which happened. In the audit trail they must be
    distinguishable: "is anyone still trying the retired shared secret?"
    is one of the first questions asked after an incident, and answering it
    from a table that only says `auth.missing_credentials` is impossible.
    """
    from app.core.config import get_settings

    before = len(audit_rows())

    legacy = client.post(
        "/stops",
        json={"stop_name": "Nope", "lat": 27.7, "lng": 85.3},
        headers={"X-Admin-Api-Key": get_settings().admin_api_key},
    )
    assert legacy.status_code == 401

    anon = client.post("/stops", json={"stop_name": "Nope", "lat": 27.7, "lng": 85.3})
    assert anon.status_code == 401

    # Indistinguishable to the caller...
    assert legacy.json() == anon.json()

    # ...and distinguishable to whoever reads the trail.
    reasons = {r.action for r in audit_rows()[before:]}
    assert "auth.legacy_shared_key_disabled" in reasons, [
        r.action for r in audit_rows()[before:]
    ]
    assert "auth.missing_credentials" in reasons
