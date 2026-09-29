"""Login endpoint for AdminUser accounts (JWT-based).

Separate from app/api/admin.py: that router is protected by
require_admin, which accepts this endpoint's JWT as well as a scoped
service credential. This router itself is intentionally unprotected
(you need to log in before you have a token).

Both success and failure are written to admin_audit_log. A failure row
records the attempted username, the source address, and the outcome --
and never the password. `verify_password` is only reached for a
username that exists, but the failure path is written identically
either way so the response can't be used to distinguish "no such user"
from "wrong password" (and neither can the audit log).

Rate limiting: 10 attempts/minute per client IP, in addition to the
nginx-level limit in deploy/nginx.prod.conf. The previous 5/minute was
tight enough to be a real operational hazard -- a handful of admins
behind one NAT address (a shared office, a university VPN) share a
single bucket, and a handful of fat-fingered password entries could
lock every one of them out of the data-entry UI for the rest of the
window. 10/minute against bcrypt (which is deliberately slow per
attempt) still caps an online attack at a few hundred guesses an hour
per address, and it is a *rate* limit, not a lockout: nothing is
disabled permanently and no counter survives the window, so a real
admin can always get back in by waiting.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.admin_audit import record_login_attempt
from app.core.rate_limit import limiter
from app.core.request_context import get_client_ip
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models import AdminUser

from app.schemas import AdminLoginRequest, AdminTokenResponse

router = APIRouter()

ADMIN_LOGIN_LIMIT = "10/minute"


def _client_ip(request: Request) -> str | None:
    """Source address for the audit row. Uses the same resolution as the
    rate limiter (core.request_context), so the two can't disagree
    about who "a client" is."""
    if not isinstance(request, Request):  # pragma: no cover - defensive
        return None
    return get_client_ip(request)


@router.post("/admin/login", response_model=AdminTokenResponse)
@limiter.limit(ADMIN_LOGIN_LIMIT)
def login(request: Request, payload: AdminLoginRequest, db: Session = Depends(get_db)) -> AdminTokenResponse:
    client_ip = _client_ip(request)
    request_id = getattr(request.state, "request_id", None)

    admin = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if admin is None or not verify_password(payload.password, admin.password_hash):
        record_login_attempt(
            username=payload.username,
            success=False,
            client_ip=client_ip,
            request_id=request_id,
            detail={"reason": "invalid_credentials"},
        )
        # Single message for both cases: a distinct "no such user" reply
        # would let anyone enumerate valid admin usernames.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

    token = create_access_token(admin_id=admin.admin_id, username=admin.username, role=admin.role)
    record_login_attempt(
        username=admin.username,
        success=True,
        client_ip=client_ip,
        request_id=request_id,
        detail={"role": admin.role},
    )
    return AdminTokenResponse(access_token=token)
