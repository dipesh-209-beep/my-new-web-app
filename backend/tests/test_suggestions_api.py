"""API-level tests for the crowd-sourced suggestions feature
(app/api/suggestions.py, models/route_suggestion.py + suggestion_vote.py):
POST /suggestions, GET /suggestions, GET + PATCH /admin/suggestions, and the
auto-apply-at-threshold path. Require a live Postgres/PostGIS instance
(docker compose up -d db) with migrations through d4e5f6a7b8c9 applied.
Skip cleanly if no DB is reachable.

Fully self-contained: everything this file creates (users, stops, routes,
suggestions, votes) is torn down by its fixtures, so it never depends on or
pollutes the shipped dataset.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.db.session import SessionLocal

TEST_PASSWORD = "correct-horse-battery-staple"
# Read via the cached get_settings() rather than a fresh Settings() -- some
# test modules os.environ.setdefault("ADMIN_API_KEY", "test-admin-key") at
# import time, which would otherwise desync this constant from the key the
# endpoints compare against at request time.
ADMIN_API_KEY = get_settings().admin_api_key


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Same rationale as test_admin_auth_api.py: POST /suggestions (10/hour)
    is keyed by remote address and TestClient requests all share one, so
    reset slowapi's buckets around every test."""
    limiter.reset()
    yield
    limiter.reset()


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


@pytest.fixture(autouse=True)
def _cleanup_suggestions():
    """Only this file creates suggestions/votes, so it's safe (and far
    simpler than tracking individual rows) to wipe the tables around every
    test. Doesn't touch users/stops/routes -- their fixtures own those.

    Mirrors the `client` fixture's no-DB handling: if the test itself was
    skipped for lack of a live database, there's nothing to clean up, so
    swallow the same OperationalError here instead of erroring at teardown.
    """
    yield
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM suggestion_votes"))
        session.execute(text("DELETE FROM route_suggestions"))
        session.commit()
    except OperationalError:
        pass
    finally:
        session.close()


@pytest.fixture
def two_users(client):
    """Two registered users, returned as access tokens; accounts deleted after."""
    tokens = []
    for _ in range(2):
        resp = client.post(
            "/auth/register",
            json={"username": f"sug-user-{uuid.uuid4().hex[:8]}", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        tokens.append(resp.json()["access_token"])
    try:
        yield tokens
    finally:
        session = SessionLocal()
        session.execute(text("DELETE FROM users WHERE username LIKE 'sug-user-%'"))
        session.commit()
        session.close()


@pytest.fixture
def one_stop(client):
    """A throwaway stop via the real admin create endpoint, deleted after."""
    resp = client.post(
        "/stops",
        json={"stop_name": f"Suggestion Stop {uuid.uuid4().hex[:6]}", "lat": 27.7, "lng": 85.3},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 201, resp.text
    stop_id = resp.json()["stop_id"]
    try:
        yield stop_id
    finally:
        session = SessionLocal()
        session.execute(text("DELETE FROM stops WHERE stop_id = :sid"), {"sid": stop_id})
        session.commit()
        session.close()


@pytest.fixture
def one_route(client, one_stop):
    """A throwaway route with 3 linked stops, deleted (incl. route_stops)
    after. Yields (route_id, stop_ids)."""
    stop_ids = [one_stop]
    resp = client.post(
        "/stops",
        json={"stop_name": f"Suggestion Stop {uuid.uuid4().hex[:6]}", "lat": 27.71, "lng": 85.31},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 201, resp.text
    stop_ids.append(resp.json()["stop_id"])
    resp = client.post(
        "/stops",
        json={"stop_name": f"Suggestion Stop {uuid.uuid4().hex[:6]}", "lat": 27.72, "lng": 85.32},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 201, resp.text
    stop_ids.append(resp.json()["stop_id"])

    resp = client.post(
        "/routes",
        json={
            "route_name": f"Suggestion Route {uuid.uuid4().hex[:6]}",
            "vehicle_type": "bus",
            "start_stop_id": stop_ids[0],
            "end_stop_id": stop_ids[2],
            "total_stops": 0,
        },
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 201, resp.text
    route_id = resp.json()["route_id"]
    for seq, sid in enumerate(stop_ids, start=1):
        resp = client.post(
            f"/routes/{route_id}/stops",
            json={"stop_id": sid, "sequence_no": seq},
            headers={"X-Admin-Api-Key": ADMIN_API_KEY},
        )
        assert resp.status_code == 201, resp.text

    try:
        yield route_id, stop_ids
    finally:
        session = SessionLocal()
        session.execute(text("DELETE FROM route_stops WHERE route_id = :rid"), {"rid": route_id})
        session.execute(text("DELETE FROM routes WHERE route_id = :rid"), {"rid": route_id})
        session.execute(text("DELETE FROM stops WHERE stop_id = ANY(:ids)"), {"ids": stop_ids})
        session.commit()
        session.close()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stop_name_change(stop_id: str, name: str) -> dict:
    return {
        "target_type": "stop",
        "target_id": stop_id,
        "suggestion_type": "stop_name_change",
        "payload": {"stop_name": name},
    }


# --- POST /suggestions ------------------------------------------------------


def test_submit_requires_user_auth(client, one_stop):
    """get_current_user uses HTTPBearer() (auto_error) so a missing
    Authorization header is rejected by the bearer scheme itself -- 403."""
    resp = client.post("/suggestions", json=_stop_name_change(one_stop, "Whatever"))
    assert resp.status_code == 403


def test_submit_new_suggestion(client, two_users, one_stop):
    resp = client.post(
        "/suggestions",
        json=_stop_name_change(one_stop, f"Better Name {uuid.uuid4().hex[:4]}"),
        headers=_auth(two_users[0]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["vote_count"] == 1
    assert body["suggestion_type"] == "stop_name_change"
    assert body["submitted_by"] is not None
    assert body["voted_by_me"] is True


def test_submit_rejects_duplicate_by_same_user(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, "One Name")
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    resp = client.post("/suggestions", json=payload, headers=_auth(two_users[0]))
    assert resp.status_code == 409


def test_vote_on_existing_suggestion(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, "Shared Name")
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    resp = client.post("/suggestions", json=payload, headers=_auth(two_users[1]))
    assert resp.status_code == 200, resp.text
    assert resp.json()["vote_count"] == 2
    assert resp.json()["suggestion_id"] != 0


def test_second_vote_by_same_user_is_409(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, "Voted Twice")
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[1])).status_code == 200
    resp = client.post("/suggestions", json=payload, headers=_auth(two_users[1]))
    assert resp.status_code == 409


def test_submit_rejects_mismatched_payload(client, two_users, one_stop):
    """A stop_name_change payload carrying sequence fields must be a 422 --
    the discriminated union rejects it."""
    resp = client.post(
        "/suggestions",
        json={
            "target_type": "stop",
            "target_id": one_stop,
            "suggestion_type": "stop_name_change",
            "payload": {"sequence": [1, 2]},
        },
        headers=_auth(two_users[0]),
    )
    assert resp.status_code == 422


def test_submit_404s_for_unknown_target(client, two_users):
    resp = client.post(
        "/suggestions",
        json=_stop_name_change("S_DOES_NOT_EXIST", "Nope"),
        headers=_auth(two_users[0]),
    )
    assert resp.status_code == 404


def test_submit_rate_limited_after_ten_per_hour(client, two_users, one_stop):
    """@limiter.limit("10/hour") on POST /suggestions -- the 11th request
    from the same client IP is a 429."""
    statuses = []
    for i in range(11):
        resp = client.post(
            "/suggestions",
            json=_stop_name_change(one_stop, f"Rated {i}"),
            headers=_auth(two_users[0]),
        )
        statuses.append(resp.status_code)
    assert statuses[:10] == [201] * 10, f"Expected first 10 submits to succeed, got {statuses[:10]}"
    assert statuses[10] == 429, f"Expected the 11th submit to be rate-limited, got {statuses[10]}"


# --- GET /suggestions -------------------------------------------------------


def test_list_suggestions_is_public(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, f"Listed Name {uuid.uuid4().hex[:4]}")
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    resp = client.get("/suggestions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["status"] == "pending"
    assert resp.json()[0]["voted_by_me"] is None  # no auth -> not enriched


def test_list_suggestions_marks_voted_by_me_with_auth(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, f"Voted List {uuid.uuid4().hex[:4]}")
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[1])).status_code == 200

    resp = client.get("/suggestions", headers=_auth(two_users[1]))
    assert resp.status_code == 200
    assert resp.json()[0]["voted_by_me"] is True


# --- GET /admin/suggestions -------------------------------------------------


def test_admin_list_requires_admin_credentials(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, f"Admin List {uuid.uuid4().hex[:4]}")
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    resp = client.get("/admin/suggestions")
    assert resp.status_code in (401, 403)

    resp = client.get("/admin/suggestions", headers={"X-Admin-Api-Key": ADMIN_API_KEY})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_admin_list_filters_by_status(client, two_users, one_stop):
    resp = client.get(
        "/admin/suggestions?status=bogus",
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 422


# --- PATCH /admin/suggestions/{id} ------------------------------------------


def test_admin_approve_applies_stop_name_change(client, two_users, one_stop):
    new_name = f"Approved Name {uuid.uuid4().hex[:4]}"
    payload = _stop_name_change(one_stop, new_name)
    created = client.post("/suggestions", json=payload, headers=_auth(two_users[0])).json()
    resp = client.patch(
        f"/admin/suggestions/{created['suggestion_id']}",
        json={"action": "approve"},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    # The shared X-Admin-Api-Key carries no per-admin identity (see
    # require_admin in security.py), so reviewed_by stays NULL on that path.
    assert body["reviewed_by"] is None
    assert body["vote_count"] == 1

    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT stop_name FROM stops WHERE stop_id = :sid"), {"sid": one_stop}
        ).scalar_one()
        assert row == new_name
    finally:
        session.close()


def test_admin_reject_leaves_data_unchanged(client, two_users, one_stop):
    original_name = client.get(f"/stops/{one_stop}").json()["stop_name"]
    payload = _stop_name_change(one_stop, "Rejected Name")
    created = client.post("/suggestions", json=payload, headers=_auth(two_users[0])).json()
    resp = client.patch(
        f"/admin/suggestions/{created['suggestion_id']}",
        json={"action": "reject"},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT stop_name FROM stops WHERE stop_id = :sid"), {"sid": one_stop}
        ).scalar_one()
        assert row == original_name
    finally:
        session.close()


def test_admin_review_rejects_non_pending_suggestion(client, two_users, one_stop):
    payload = _stop_name_change(one_stop, "Already Done")
    created = client.post("/suggestions", json=payload, headers=_auth(two_users[0])).json()
    assert client.patch(
        f"/admin/suggestions/{created['suggestion_id']}",
        json={"action": "approve"},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    ).status_code == 200
    resp = client.patch(
        f"/admin/suggestions/{created['suggestion_id']}",
        json={"action": "reject"},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 409


def test_admin_review_404s_for_unknown_suggestion(client):
    resp = client.patch(
        "/admin/suggestions/42424242",
        json={"action": "approve"},
        headers={"X-Admin-Api-Key": ADMIN_API_KEY},
    )
    assert resp.status_code == 404


def test_admin_review_with_login_token_records_reviewer(client, two_users, one_stop):
    """Reviewing via a /admin/login bearer JWT (instead of the shared key)
    attaches the specific AdminUser's username to reviewed_by -- the shared-
    key path has no identity to record (see require_admin docstring)."""
    from app.core.security import hash_password
    from app.models import AdminUser

    session = SessionLocal()
    username = f"reviewer-{uuid.uuid4().hex[:8]}"
    admin = AdminUser(username=username, password_hash=hash_password(TEST_PASSWORD), role="admin")
    session.add(admin)
    session.commit()
    admin_id = admin.admin_id
    session.close()

    login = client.post("/admin/login", json={"username": username, "password": TEST_PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    try:
        payload = _stop_name_change(one_stop, "Reviewed By Admin")
        created = client.post("/suggestions", json=payload, headers=_auth(two_users[0])).json()
        resp = client.patch(
            f"/admin/suggestions/{created['suggestion_id']}",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reviewed_by"] == username
    finally:
        session = SessionLocal()
        row = session.get(AdminUser, admin_id)
        if row is not None:
            session.delete(row)
            session.commit()
        session.close()


# --- Auto-apply at AUTO_APPLY_VOTE_THRESHOLD --------------------------------


@pytest.fixture
def low_threshold(monkeypatch):
    """Wind AUTO_APPLY_VOTE_THRESHOLD down to 2 (any two distinct users back
    a change and it applies itself). Subclassing keeps the real .env settings;
    only the threshold differs. Restored afterwards."""
    import app.api.suggestions as sugmod

    class _LowThresholdSettings(Settings):
        AUTO_APPLY_VOTE_THRESHOLD: int = 2

    monkeypatch.setattr(sugmod, "get_settings", lambda: _LowThresholdSettings())


def test_auto_applies_stop_name_change_at_threshold(client, two_users, one_stop, low_threshold):
    new_name = f"Auto Name {uuid.uuid4().hex[:4]}"
    payload = _stop_name_change(one_stop, new_name)
    created = client.post("/suggestions", json=payload, headers=_auth(two_users[0])).json()
    assert created["status"] == "pending"

    voted = client.post("/suggestions", json=payload, headers=_auth(two_users[1]))
    assert voted.status_code == 200, voted.text
    body = voted.json()
    assert body["status"] == "auto_applied"
    assert body["vote_count"] == 2

    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT stop_name FROM stops WHERE stop_id = :sid"), {"sid": one_stop}
        ).scalar_one()
        assert row == new_name
    finally:
        session.close()


def test_auto_applies_route_name_change_at_threshold(client, two_users, one_route, low_threshold):
    route_id, _ = one_route
    new_name = f"Auto Route {uuid.uuid4().hex[:4]}"
    payload = {
        "target_type": "route",
        "target_id": route_id,
        "suggestion_type": "route_name_change",
        "payload": {"route_name": new_name},
    }
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    voted = client.post("/suggestions", json=payload, headers=_auth(two_users[1]))
    assert voted.status_code == 200, voted.text
    assert voted.json()["status"] == "auto_applied"

    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT route_name FROM routes WHERE route_id = :rid"), {"rid": route_id}
        ).scalar_one()
        assert row == new_name
    finally:
        session.close()


def test_auto_applies_stop_sequence_change_at_threshold(client, two_users, one_route, low_threshold):
    route_id, stop_ids = one_route
    payload = {
        "target_type": "route",
        "target_id": route_id,
        "suggestion_type": "stop_sequence_change",
        "payload": {"sequence": [3, 1, 2]},
    }
    assert client.post("/suggestions", json=payload, headers=_auth(two_users[0])).status_code == 201
    voted = client.post("/suggestions", json=payload, headers=_auth(two_users[1]))
    assert voted.status_code == 200, voted.text
    assert voted.json()["status"] == "auto_applied"

    session = SessionLocal()
    try:
        rows = session.execute(
            text("SELECT stop_id FROM route_stops WHERE route_id = :rid ORDER BY sequence_no"),
            {"rid": route_id},
        ).scalars().all()
        assert rows == [stop_ids[2], stop_ids[0], stop_ids[1]]
    finally:
        session.close()


def test_auto_apply_does_not_apply_below_threshold(client, two_users, one_stop, low_threshold):
    """One vote below threshold: still pending, data untouched."""
    original_name = client.get(f"/stops/{one_stop}").json()["stop_name"]
    payload = _stop_name_change(one_stop, "Too Little Support")
    created = client.post("/suggestions", json=payload, headers=_auth(two_users[0]))  # vote_count 1 < 2
    assert created.status_code == 201
    assert created.json()["status"] == "pending"

    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT stop_name FROM stops WHERE stop_id = :sid"), {"sid": one_stop}
        ).scalar_one()
        assert row == original_name
    finally:
        session.close()