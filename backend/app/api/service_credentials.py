"""Management of scoped service credentials.

This is the supported replacement for the old shared
`X-Admin-Api-Key` secret: instead of one bearer token that means
"everything, forever, unrotatable, unrevocable, and attributable to
nobody", an automated caller gets a credential with a name, an explicit
list of permissions, an optional expiry, a revocation switch, and a
last-used timestamp, all stored hashed.

Three endpoints, all human-admin-only:

  POST   /admin/service-credentials          mint one; returns the key once
  GET    /admin/service-credentials          list metadata (never the key)
  DELETE /admin/service-credentials/{key_id} revoke

Why `human_only=True` on all three
-----------------------------------
Every one of these is gated on a *named human role* rather than on a
data permission, and service credentials are refused outright. If a
credential holding, say, stops:write were allowed to mint credentials,
it could mint itself one holding graph:reload and escalate without ever
touching a human account. The one credential type that cannot create
another is the only one that can't be used to bootstrap privilege.

Key handling
------------
* The raw key is generated with `secrets.token_urlsafe` (a CSPRNG), not
  a predictable counter or a PRNG.
* Only `sha256(secret)` is stored (app/core/security.py:
  hash_service_secret). The key cannot be recovered from the database
  or from a dump of it -- rotate by creating a new credential.
* The key appears in exactly one response, the 201 from this POST. It
  is never logged: the `detail` of the accompanying audit row records
  the name and scopes, and `key`/`secret` are on the redaction list in
  app/core/admin_audit.py so even an accidental attachment gets
  replaced with "[redacted]".
* Verification uses hmac.compare_digest, so a caller cannot recover
  the stored digest a character at a time.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.admin_audit import record_audit
from app.core.security import (
    ROLE_ADMIN,
    Principal,
    generate_service_key,
    hash_service_secret,
    require_role,
)
from app.db.session import get_db
from app.models import ServiceCredential
from app.schemas import (
    AdminAuditLogOut,
    ServiceCredentialCreate,
    ServiceCredentialCreated,
    ServiceCredentialOut,
)

from app.models import AdminAuditLog

router = APIRouter(tags=["admin"])

# Human admin only, for every route in this file. See the module
# docstring for why a service credential must never be able to manage
# credentials.
#
# Applied differently per route, deliberately:
#   * The two mutating routes declare it as a *parameter*
#     (`principal: Principal = Depends(...)`) because they need the
#     identity anyway to attribute their audit row. Adding it again via
#     `dependencies=[...]` would be redundant: FastAPI inserts
#     route-level dependencies at position 0 of `dependant.dependencies`
#     (fastapi/routing.py `_build_dependant_with_parameterless_
#     dependencies`) and `solve_dependencies` short-circuits on the first
#     HTTPException, so a denied caller is refused by whichever runs
#     first and the other never evaluates. Redundant, not double-audited
#     -- but redundant, so it is not written down twice.
#   * The two read-only routes have no identity to attribute, so they use
#     the decorator form and record nothing. If one of them ever starts
#     writing audit rows, `record_audit`'s required `principal`
#     argument forces the signature to change with it.
_ADMIN_ONLY = Depends(require_role(ROLE_ADMIN, human_only=True))


def _out(row: ServiceCredential) -> ServiceCredentialOut:
    """Serialise a credential without ever touching key_hash."""
    now = datetime.now(timezone.utc)
    expires_at = row.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return ServiceCredentialOut(
        key_id=row.key_id,
        name=row.name,
        scopes=list(row.scopes or []),
        created_at=row.created_at.isoformat() if row.created_at else "",
        expires_at=expires_at.isoformat() if expires_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        active=(
            row.revoked_at is None
            and (expires_at is None or expires_at > now)
        ),
    )


@router.post(
    "/admin/service-credentials",
    response_model=ServiceCredentialCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_service_credential(
    payload: ServiceCredentialCreate,
    request: Request,
    principal: Principal = Depends(require_role(ROLE_ADMIN, human_only=True)),
    db: Session = Depends(get_db),
) -> ServiceCredentialCreated:
    """Mint a scoped credential for an automated caller.

    The returned `key` is shown exactly once. Store it in the calling
    system's secret store immediately; if it is lost, create a new
    credential and revoke this one.
    """
    if db.scalar(select(ServiceCredential).where(ServiceCredential.name == payload.name)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A service credential named {payload.name!r} already exists.",
        )

    expires_at = payload.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be in the future.",
            )

    presented, key_id, key_hash = generate_service_key()
    row = ServiceCredential(
        key_id=key_id,
        name=payload.name,
        key_hash=key_hash,
        scopes=list(payload.scopes),
        expires_at=expires_at,
        created_by_admin_id=principal.admin_id,
    )
    db.add(row)
    record_audit(
        db,
        principal=principal,
        action="service_credential.create",
        resource_type="service_credential",
        resource_id=key_id,
        success=True,
        request_id=getattr(request.state, "request_id", None),
        client_ip=getattr(request.state, "client_ip", None),
        # Name + scopes only. The key itself is not passed to the audit
        # layer and could not be recorded even if a future change tried:
        # `key`/`secret` are on the redaction list.
        detail={"name": payload.name, "scopes": list(payload.scopes), "expires_at": payload.expires_at.isoformat() if payload.expires_at else None},
    )
    db.commit()
    db.refresh(row)

    result = _out(row)
    return ServiceCredentialCreated(**result.model_dump(), key=presented)


@router.get(
    "/admin/service-credentials",
    response_model=list[ServiceCredentialOut],
    dependencies=[_ADMIN_ONLY],
)
def list_service_credentials(db: Session = Depends(get_db)) -> list[ServiceCredentialOut]:
    """All credentials, newest first, metadata only.

    Revoked credentials are included (not filtered out) on purpose: an
    operator needs to see that a key exists *and* that it was revoked,
    rather than wondering where it went. The `active` field on each
    row says whether it would currently authenticate.
    """
    rows = db.scalars(
        select(ServiceCredential).order_by(ServiceCredential.created_at.desc(), ServiceCredential.credential_id.desc())
    ).all()
    return [_out(row) for row in rows]


@router.delete(
    "/admin/service-credentials/{key_id}",
    status_code=status.HTTP_200_OK,
)
def revoke_service_credential(
    key_id: str,
    request: Request,
    principal: Principal = Depends(require_role(ROLE_ADMIN, human_only=True)),
    db: Session = Depends(get_db),
) -> dict:
    """Revoke a credential. Idempotent: revoking an already-revoked (or
    already-expired) credential reports `already_revoked` and changes
    nothing, so a re-run of an ops script doesn't need to check first.

    The row is kept rather than deleted so existing audit entries that
    reference this key_id still resolve to something meaningful.
    """
    row = db.scalar(select(ServiceCredential).where(ServiceCredential.key_id == key_id))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service credential {key_id} not found.",
        )

    already = row.revoked_at is not None
    if not already:
        row.revoked_at = datetime.now(timezone.utc)
        record_audit(
            db,
            principal=principal,
            action="service_credential.revoke",
            resource_type="service_credential",
            resource_id=key_id,
            success=True,
            request_id=getattr(request.state, "request_id", None),
            client_ip=getattr(request.state, "client_ip", None),
            detail={"name": row.name, "scopes": list(row.scopes or [])},
        )
        db.commit()
        db.refresh(row)

    return {
        "key_id": row.key_id,
        "name": row.name,
        "revoked": True,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "already_revoked": already,
    }


@router.get(
    "/admin/audit-log",
    response_model=list[AdminAuditLogOut],
    dependencies=[_ADMIN_ONLY],
)
def list_audit_log(
    limit: int = 100,
    action: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[AdminAuditLogOut]:
    """Read the admin audit trail, most recent first.

    Read-only on purpose: there is no write, edit, or delete route for
    this table anywhere in the API. That is *application-level*
    append-only, not tamper-proofing -- an account with direct database
    access can still modify these rows, which is called out in
    docs/security.md rather than glossed over here.

    `limit` is capped so a single call can't pull the whole table into
    memory; for anything beyond a few thousand rows, query the table
    directly (see docs/security.md for the intended query).
    """
    limit = max(1, min(limit, 1000))
    stmt = select(AdminAuditLog).order_by(
        AdminAuditLog.timestamp.desc(), AdminAuditLog.id.desc()
    )
    if action:
        # Parameter-bound, not formatted into SQL, so this filter can't
        # be used to inject anything.
        stmt = stmt.where(AdminAuditLog.action == action)
    rows = db.scalars(stmt.limit(limit)).all()
    return [
        AdminAuditLogOut(
            id=row.id,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            success=row.success,
            timestamp=row.timestamp.isoformat() if row.timestamp else "",
            request_id=row.request_id,
            client_ip=row.client_ip,
            detail=row.detail,
        )
        for row in rows
    ]
