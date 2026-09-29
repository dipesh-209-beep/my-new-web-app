"""Admin audit trail.

Every privileged action is recorded in `admin_audit_log`
(app/models/admin_audit_log.py) with enough detail to answer "who did
what to which resource, when, and did it actually happen" -- without
ever recording the credential that authenticated it.

Two entry points, and the difference between them is the point:

  record_audit(db, ...)        Joins the caller's *open transaction*.
                              Used for successful mutations. Because
                              the row is inserted before the commit,
                              a mutation that rolls back takes its
                              audit row with it, and an audit insert
                              that fails rolls the mutation back --
                              so a security-sensitive change can never
                              succeed while silently going unlogged.

  record_security_event(...)  Commits on its own session. Used for
                              events that happen *outside* any
                              transaction the caller owns: failed
                              logins, 401/403 denials, and the parts
                              of a request that happen before/without
                              a successful write. Those are recorded
                              best-effort -- if the insert itself
                              fails, the failure is logged loudly at
                              ERROR and the caller's original 401/403
                              still stands, because turning an auth
                              failure into a 500 by virtue of an
                              audit-table problem would be a worse
                              outcome, not a safer one.

What is deliberately never written here: passwords, password hashes,
JWTs, service-credential secrets, raw Authorization / X-Admin-Api-Key
headers, request bodies, or end-user coordinates. scrub_detail()
enforces the first four structurally rather than by convention, and
callers are expected to pass field *names* rather than values wherever
a choice exists.
"""

import json
import logging
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ..models import (
    ACTOR_ADMIN_USER,
    ACTOR_ANONYMOUS,
    ACTOR_SERVICE_CREDENTIAL,
    AdminAuditLog,
)
from .security import Principal

logger = logging.getLogger(__name__)

# Keys whose *values* are dropped from an audit row's `detail`, matched
# case-insensitively and as substrings, so "raw_authorization_header",
# "newPassword", and "service_key" are all caught. This is a
# backstop, not the primary control: callers pass field names and
# non-identifying context in the first place. It exists so that a
# future endpoint that helpfully attaches "the thing the user typed"
# to its audit detail cannot turn that into a credential leak.
#
# The list errs toward over-redaction, deliberately. A field whose value
# is replaced with "[redacted]" costs an operator one piece of context; a
# field that isn't costs them a live credential, and there is no
# supported way to recover a redacted one. That asymmetry is why bare
# "key" is in the list even though it also catches "key_id" and
# "cache_key" -- a public key handle is already recorded in the
# actor_id/resource_id columns, where the scrubber doesn't reach it, so
# losing it from `detail` costs nothing.
_REDACTED_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "token",
    "key",
    "authorization",
    "credential",
    "hash",
    "lat",
    "lng",
    "latitude",
    "longitude",
    "coord",
)
_REDACTION_PLACEHOLDER = "[redacted]"

# Hard cap on how much can go in one `detail` blob, so a caller that
# loops over a large list can't bloat the table.
_MAX_DETAIL_CHARS = 4000


def scrub_detail(detail: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Drop any `detail` key whose name looks like a secret or a
    coordinate, and clamp the result to a sane size. Returns None for
    an empty input so the column stays NULL rather than '{}'."""
    if not detail:
        return None
    scrubbed: dict[str, Any] = {}
    for key, value in detail.items():
        lowered = str(key).lower()
        if any(needle in lowered for needle in _REDACTED_SUBSTRINGS):
            scrubbed[key] = _REDACTION_PLACEHOLDER
            continue
        scrubbed[key] = _clamp(value)
    serialised = json.dumps(scrubbed, default=str)
    if len(serialised) > _MAX_DETAIL_CHARS:
        return {"truncated": True, "keys": sorted(str(k) for k in scrubbed)[:50]}
    return scrubbed


def _clamp(value: Any) -> Any:
    """Coerce a detail value into something JSONB can hold without
    dragging a whole object graph in behind it."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clamp(item) for item in list(value)[:50]]
    if isinstance(value, dict):
        return {str(k)[:100]: _clamp(v) for k, v in list(value.items())[:50]}
    return str(value)[:500]


def record_audit(
    db: Session,
    *,
    principal: Optional[Principal],
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    success: bool = True,
    request_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> AdminAuditLog:
    """Stage one audit row on the caller's transaction. Does NOT commit.

    Call this *before* the commit that persists the mutation, so the
    two are atomic. A caller that rolls back for any reason -- a
    failed write, an IntegrityError, an early return after a
    validation failure -- should not have staged a success row.
    """
    if principal is None:
        actor_type, actor_id = ACTOR_ANONYMOUS, None
    else:
        actor_type, actor_id = principal.actor_type, principal.actor_id

    entry = AdminAuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action[:100],
        resource_type=resource_type[:50],
        resource_id=(resource_id[:100] if resource_id is not None else None),
        success=success,
        request_id=request_id,
        client_ip=client_ip,
        detail=scrub_detail(detail),
    )
    db.add(entry)
    return entry


def record_security_event(
    *,
    actor_type: str,
    actor_id: Optional[str],
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    success: bool = False,
    request_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> Optional[AdminAuditLog]:
    """Write one audit row on its own session and commit it immediately.

    For events that are not part of an open transaction: failed
    logins, authorization denials, and other "nothing was changed but
    it should be on the record" cases. Never raises -- see the module
    docstring for why an audit failure must not change the outcome the
    caller was already going to produce.
    """
    # Imported here rather than at module scope: db/session.py imports
    # core/config.py, and admin_audit is imported by core/security.py.
    from ..db.session import SessionLocal

    if actor_type not in (ACTOR_ADMIN_USER, ACTOR_SERVICE_CREDENTIAL, ACTOR_ANONYMOUS):
        actor_type = ACTOR_ANONYMOUS
        actor_id = None

    session = SessionLocal()
    try:
        entry = AdminAuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action[:100],
            resource_type=resource_type[:50],
            resource_id=(resource_id[:100] if resource_id is not None else None),
            success=success,
            request_id=request_id,
            client_ip=client_ip,
            detail=scrub_detail(detail),
        )
        session.add(entry)
        session.commit()
        return entry
    except Exception:
        session.rollback()
        # Loud on purpose. The caller's original outcome (a 401, a 403)
        # is unaffected, but an operator needs to know the audit trail
        # has holes.
        logger.error(
            "Failed to write admin audit event action=%s actor=%s:%s -- "
            "the audit trail is now incomplete for this event",
            action,
            actor_type,
            actor_id,
            exc_info=True,
        )
        return None
    finally:
        session.close()


def record_login_attempt(
    *,
    username: Optional[str],
    success: bool,
    client_ip: Optional[str] = None,
    request_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Audit one attempt against POST /admin/login.

    `username` is recorded because a username is an identifier an
    operator needs to see, and it is not a secret. The submitted
    password is never passed here and never stored -- the caller
    deliberately drops it as soon as verification fails, and nothing
    downstream of this call receives it.
    """
    merged = dict(detail or {})
    merged["outcome"] = "success" if success else "failure"
    record_security_event(
        actor_type=ACTOR_ADMIN_USER if success else ACTOR_ANONYMOUS,
        actor_id=username if success else None,
        action="admin.login.success" if success else "admin.login.failure",
        resource_type="admin_user",
        resource_id=username,
        success=success,
        request_id=request_id,
        client_ip=client_ip,
        # The attempted username on a failure is kept out of actor_id
        # (which is NULL for an unknown caller) but is still worth
        # having, so it goes in detail.
        detail=merged | ({"attempted_username": username} if (not success and username) else {}),
    )


def record_authorization_denial(
    principal: Optional[Principal],
    *,
    required_permissions: Iterable[str],
    request: Any = None,
    required_roles: Iterable[str] = (),
    reason: str | None = None,
) -> None:
    """Audit a 403 -- authenticated, but not permitted.

    Kept separate from the raise sites so every gate produces the same
    shape of row, and so this can also be called from a route that
    checks permissions itself.

    `required_roles` and `reason` exist because not every gate is a
    permission check. `require_role(ROLE_ADMIN, human_only=True)` has no
    permission set to report at all, and a row saying
    `required_permissions: []` is the difference between "refused because
    automation cannot do this" being answerable from the audit trail
    six months later and not. The HTTP response body is not a substitute:
    it is transient, and it is not retained.
    """
    detail: dict[str, Any] = {
        "required_permissions": list(required_permissions),
        "held_permissions": sorted(principal.permissions) if principal else [],
        "method": getattr(request, "method", None),
    }
    roles = list(required_roles)
    if roles:
        detail["required_roles"] = roles
        detail["held_role"] = (
            principal.admin_user.role
            if principal is not None and principal.admin_user is not None
            else None
        )
    if reason:
        detail["reason"] = reason
    record_security_event(
        actor_type=principal.actor_type if principal else ACTOR_ANONYMOUS,
        actor_id=principal.actor_id if principal else None,
        action="authorization.denied",
        resource_type="admin_endpoint",
        resource_id=getattr(getattr(request, "url", None), "path", None),
        success=False,
        request_id=getattr(getattr(request, "state", None), "request_id", None),
        client_ip=getattr(getattr(request, "state", None), "client_ip", None),
        detail=detail,
    )
