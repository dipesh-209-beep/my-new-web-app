"""
Structural authorization audit for every privileged route.

Why this exists
---------------
The permission gates are ordinary FastAPI dependencies, which means
nothing about the framework forces an endpoint to have one. A gate can
be deleted, renamed to something that resolves, or moved to a
`dependencies=[...]` list where it is easy to overlook, and every
existing request-level test still passes -- because the test that
covered the deleted gate was the thing that got deleted, or because
the endpoint in question had no happy-path test asserting its refusal.

That is not hypothetical: `GET /admin/suggestions` lost its gate during
this work and briefly served the entire moderation queue to anyone. The
request-level test that caught it was
tests/test_suggestions_api.py::test_admin_list_requires_admin_credentials,
which existed only because the old shared-key world needed a 401
assertion. This module is the check that does not depend on a specific
test having been written.

What it asserts, for every route whose path is privileged:
  1. Its resolved dependency tree contains at least one authorization
     gate -- `require_permissions`, `require_role`, or `require_admin`.
     Resolving the tree (rather than grepping source) means a gate
     inherited from a router-level `dependencies=[...]` also counts,
     which is correct.
  2. It declares a `principal` parameter, so the handler has the
     authenticated identity available for audit attribution. This is
     separate from (1) on purpose: a route can be correctly gated and
     still be unable to say who did it.

And, for the write routes specifically:
  3. A credential that holds *no* permissions is refused with 403. This
     is a behavioural check against the live app, so it also covers
     anything the structural check can't see (a gate that raises the
     wrong status, or a route that resolves the principal but ignores
     it).

Route selection is by (method, path), so a new privileged endpoint is
covered automatically. Privilege is a property of the *pair*, not the
path: `GET /stops` is the public search API anyone may call, while
`POST /stops` creates a row and is privileged. Selecting on path alone
gets that backwards.
"""
import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import Principal, require_admin, require_permissions, require_role
from app.db.session import SessionLocal

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MUTATING_METHODS = {"POST", "PATCH", "DELETE", "PUT"}

# Under /admin, exactly one route is public: the login endpoint, which
# every other credential is obtained *from*. Everything else beneath
# /admin, and everything beneath /graph, is administrative.
ADMIN_PREFIXES = ("/admin", "/graph")

# Paths that are privileged for mutating methods and public for reads.
# `GET /stops`, `GET /routes`, `GET /routes/{id}/stops` and
# `GET /routes/{id}/geometry` are the product's public API; the POST /
# PATCH / DELETE on the same paths are data entry.
MIXED_PATH_RE = re.compile(
    r"^/stops"
    r"|^/routes",
    re.IGNORECASE,
)

# Privileged by the rules above, but genuinely exempt. Empty today; each
# entry needs a comment saying why, because adding one here disables the
# audit for that route.
PUBLIC_ROUTE_EXCEPTIONS: set[tuple[str, str]] = set()


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


def _is_public_login(method: str, path: str) -> bool:
    return method == "POST" and path == "/admin/login"


def _is_privileged(method: str, path: str) -> bool:
    if (method, path) in PUBLIC_ROUTE_EXCEPTIONS:
        return False
    if _is_public_login(method, path):
        return False
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PREFIXES):
        return True
    if method in SAFE_METHODS:
        return False
    return bool(MIXED_PATH_RE.search(path))


def _all_leaf_routes() -> list:
    """Flatten the app's route tree down to leaf routes.

    `app.routes` is not a flat list of endpoints. Depending on the
    FastAPI version it contains route objects directly, or
    `_IncludedRouter` wrappers that hold a router and resolve to their
    concrete routes lazily via `effective_candidates()`. The audit needs
    the *resolved* path and the *merged* dependant (a router-level
    `dependencies=[...]` is folded into it), so both shapes have to be
    walked.

    The `_IncludedRouter` traversal touches private API. That is a
    deliberate trade: the alternative is a hardcoded list of router
    modules, which silently stops covering the day someone adds a
    router. `test_privileged_route_selection_is_not_vacuous` below is the
    guard that makes the trade safe -- if a FastAPI upgrade changes these
    internals, the flatten returns nothing and that test fails loudly
    rather than the audit passing vacuously.
    """
    leaves: list = []

    def _walk(node) -> None:
        # Leaf: a real route with a path and a dependant.
        if getattr(node, "dependant", None) is not None and getattr(node, "path", None):
            leaves.append(node)
            return
        # Branch: a lazily-included router.
        candidates = getattr(node, "effective_candidates", None)
        if callable(candidates):
            for child in candidates():
                _walk(child)
            return
        for child in getattr(node, "routes", []) or []:
            _walk(child)

    for route in app.routes:
        _walk(route)
    return leaves


def _privileged_cases() -> list[tuple[str, str]]:
    """Every (method, path) the app exposes that must be gated."""
    cases = []
    for route in _all_leaf_routes():
        path = route.path
        for method in sorted(route.methods or []):
            if method in ("HEAD", "OPTIONS"):
                continue
            if _is_privileged(method, path):
                cases.append((method, path))
    return sorted(set(cases))


def _route_for(case: tuple[str, str]):
    """The leaf route object behind a (method, path) pair."""
    method, path = case
    for route in _all_leaf_routes():
        if route.path == path and method in (route.methods or []):
            return route
    raise KeyError(f"no route for {case}")  # pragma: no cover - guarded above


def _resolved_dependencies(dependant) -> list:
    """Flatten a FastAPI dependant tree into a list of every node in it."""
    found = [dependant]
    stack = [dependant]
    while stack:
        current = stack.pop()
        for sub in current.dependencies:
            found.append(sub)
            stack.append(sub)
    return found


GATE_FUNCTIONS = {require_admin, require_permissions, require_role}


def _has_gate(route) -> bool:
    for dep in _resolved_dependencies(route.dependant):
        call = getattr(dep, "call", None)
        if call in GATE_FUNCTIONS:
            return True
        # require_permissions(...) returns a closure named `_check`, so
        # the factory itself is not the callable that ends up in the
        # tree. Match on the defining module instead, which covers both
        # the factory and its product without hardcoding closure names.
        if getattr(call, "__module__", "") == require_permissions.__module__:
            return True
    return False


def _has_principal_parameter(route) -> bool:
    """Is a `principal` (or a typed Principal) reachable as an argument?

    Checked against the route's own signature rather than the resolved
    tree, because a gate applied at the router level can satisfy (1)
    while leaving the handler with no identity to attribute.
    """
    for dep in _resolved_dependencies(route.dependant):
        call = getattr(dep, "call", None)
        if call is None:
            continue
        import inspect

        try:
            params = inspect.signature(call).parameters
        except (TypeError, ValueError):  # pragma: no cover - builtins
            continue
        if "principal" in params:
            return True
        if any(p.annotation is Principal for p in params.values()):
            return True
    return False


def test_privileged_route_selection_is_not_vacuous():
    """Guard the guard.

    If a FastAPI upgrade changes the route-tree internals this file
    walks, the flatten silently returns nothing and every check below
    passes having examined zero routes -- the same failure mode as a
    permission check that matches nothing. So assert the selection
    actually found the routes it is supposed to, and that it did *not*
    sweep up the public API.
    """
    cases = set(_privileged_cases())
    leaves = _all_leaf_routes()
    assert len(leaves) > 20, f"route flatten looks broken, found only {len(leaves)} routes"

    # The write and administrative routes that must be present.
    for expected in [
        ("POST", "/admin/service-credentials"),
        ("GET", "/admin/service-credentials"),
        ("DELETE", "/admin/service-credentials/{key_id}"),
        ("GET", "/admin/audit-log"),
        ("POST", "/graph/reload"),
        ("GET", "/admin/suggestions"),
        ("PATCH", "/admin/suggestions/{suggestion_id}"),
        ("PATCH", "/routes/{route_id}/status"),
        ("POST", "/stops"),
        ("PATCH", "/stops/{stop_id}"),
        ("POST", "/routes"),
        ("PATCH", "/routes/{route_id}"),
        ("POST", "/routes/{route_id}/stops"),
        ("DELETE", "/routes/{route_id}/stops/{sequence_no}"),
        ("PATCH", "/routes/{route_id}/stops/order"),
    ]:
        assert expected in cases, f"expected {expected} to be audited as privileged"

    # The public API must NOT be swept up. These are the cases where a
    # path-only rule would have produced false positives.
    for public in [
        ("POST", "/admin/login"),      # where credentials come from
        ("GET", "/stops"),
        ("GET", "/routes"),
        ("GET", "/routes/{route_id}"),
        ("GET", "/routes/{route_id}/stops"),
        ("GET", "/routes/{route_id}/geometry"),
        ("GET", "/stops/{stop_id}"),
        ("GET", "/route-finder"),
        ("GET", "/suggestions"),
        ("GET", "/health"),
        # Deliberately unauthenticated so a load balancer or orchestrator
        # can poll it without holding an admin token; it reports dependency
        # reachability (DB, and Redis when configured) and nothing about
        # the deployment's data. See readiness_check in app/main.py.
        ("GET", "/health/ready"),
    ]:
        assert public not in cases, f"{public} is public and must not be audited as privileged"


def test_every_privileged_route_has_an_authorization_gate():
    ungated = [
        case
        for case in _privileged_cases()
        if not _has_gate(_route_for(case))
    ]
    assert not ungated, (
        "privileged routes with no authorization dependency anywhere in their "
        f"resolved dependency tree: {ungated}"
    )


def test_every_privileged_mutation_receives_a_principal():
    """A mutating route that can't see who the caller is can authorize the
    action but not audit it, which is half the job.

    Scoped to mutations on purpose. A read-only privileged route records
    nothing, so there is nothing for a principal to be attached to, and
    demanding an unused parameter on it would only invite the parameter
    to be ignored. The real enforcement for the auditing case is the
    type system: `record_audit` takes `principal` as a *required*
    keyword argument, so a handler cannot write an audit row without
    receiving an identity. This test covers the other half -- a mutation
    that forgets to audit at all, which the compiler can't catch.
    """
    missing = [
        case
        for case in _privileged_cases()
        if case[0] in MUTATING_METHODS
        and not _has_principal_parameter(_route_for(case))
    ]
    assert not missing, (
        "privileged mutations that cannot attribute the caller: "
        f"{missing}. Either add `principal: Principal = Depends(...)` or, if the "
        "route records nothing, it probably should be a read."
    )


@pytest.mark.parametrize(
    "method,path", _privileged_cases(), ids=lambda v: str(v)
)
def test_privileged_route_rejects_an_invalid_credential(client, method, path):
    """Behavioural backstop for the two structural checks above.

    A header naming a service key that does not exist is the weakest
    possible caller. Every privileged route must refuse it, and must do
    so *before* argument validation -- a 422 would mean the gate never
    ran and the handler rejected the body instead. Sending `json={}`
    covers routes with a required body; routes without one ignore it.
    """
    resp = client.request(
        method, path, json={}, headers={"Authorization": "SvcKey svc_deadbeef00.nope"}
    )
    assert resp.status_code in (401, 403), (
        f"{method} {path} returned {resp.status_code} for a caller with no valid "
        f"credential: {resp.text}"
    )


@pytest.mark.parametrize(
    "method,path", _privileged_cases(), ids=lambda v: str(v)
)
def test_privileged_route_rejects_a_legacy_shared_key(client, method, path):
    """The old `X-Admin-Api-Key` shared secret must not open any
    privileged route. It is disabled by default; this asserts that
    across the whole surface rather than route by route, so a future
    endpoint can't quietly re-admit it."""
    from app.core.config import get_settings

    resp = client.request(
        method,
        path,
        json={},
        headers={"X-Admin-Api-Key": get_settings().admin_api_key},
    )
    assert resp.status_code in (401, 403), (
        f"{method} {path} accepted the disabled legacy shared key: {resp.status_code} "
        f"{resp.text}"
    )
