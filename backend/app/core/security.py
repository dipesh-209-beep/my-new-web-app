"""Auth for admin endpoints: a shared API key for scripted/ETL access,
plus per-admin JWT login for interactive use.

Originally just a single shared admin API key checked against a
request header. The per-admin login flow below (AdminUser + JWT) was
added afterward but, per the 2026-08-24 audit, was never actually
wired into any route -- POST /admin/login issued real tokens that
nothing ever verified. require_admin() below is the fix: it accepts
*either* credential, so existing scripted callers (X-Admin-Api-Key)
keep working unchanged, and a bearer token from /admin/login now
actually grants access too, with the specific AdminUser attached to
the request for future per-admin authorization/audit use.
"""

import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_admin_key(x_admin_api_key: str = Header(..., alias="X-Admin-Api-Key")) -> None:
    """FastAPI dependency: raise 401 unless the header matches the
    configured admin key. Use `secrets.compare_digest` instead of `==`
    so the comparison runs in constant time and doesn't leak length
    information via a timing side-channel.

    Kept standalone (rather than folded into require_admin below) since
    a few call sites -- e.g. scripts/ETL -- may want the shared-key-only
    check with no JWT fallback. Most routes should use require_admin.
    """
    settings = get_settings()
    if not secrets.compare_digest(x_admin_api_key, settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing admin API key.")


# ---------------------------------------------------------------------------
# AdminUser login (password hashing + JWT) -- separate from require_admin_key
# above. require_admin_key is still exported for callers that specifically
# want shared-key-only access; require_admin() (below) is what admin routes
# actually use, and accepts a JWT from this login flow as an alternative.
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AdminUser, User

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_scheme = HTTPBearer()
# auto_error=False: a missing Authorization header must fall through to
# the X-Admin-Api-Key check in require_admin() below, not raise on its
# own -- only require_admin() decides when both have failed.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _pwd_context.verify(plain_password, password_hash)


# Tokens are scoped by a "type" claim ("admin" for AdminUser JWTs,
# "user" for public-user JWTs). Without it, a user token (whose `sub` is a
# users.user_id) and an admin token (whose `sub` is an admin_users.admin_id)
# are indistinguishable -- and since both are serially autoincrementing from
# 1, the first user and first admin share sub="1", which would let a user
# token slip through require_admin's db.get(AdminUser, sub). Every decoder
# checks the claim matches its audience.
_ADMIN_TYPE = "admin"
_USER_TYPE = "user"


def create_access_token(admin_id: int, username: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(admin_id), "username": username, "role": role, "type": _ADMIN_TYPE, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_user_token(user_id: int, username: str) -> str:
    """JWT for a public User (see app/models/user.py and app/api/auth.py).
    Separate from scoped so a leaked user token can never satisfy an
    admin endpoint, even over the same secret key."""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "username": username, "type": _USER_TYPE, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_admin_token(token: str, db: Session) -> Optional[AdminUser]:
    """Shared decode logic for get_current_admin and require_admin.
    Returns None (never raises) on any invalid/expired/unknown token,
    so callers that want to fall back to another credential can do so;
    get_current_admin turns a None back into a 401 itself."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    # A token signed for a different audience (i.e. a public user token)
    # is not an admin credential, regardless of sub.
    if payload.get("type") != _ADMIN_TYPE:
        return None
    return db.get(AdminUser, int(payload["sub"]))


def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AdminUser:
    """FastAPI dependency: decode the bearer token, load and return the
    AdminUser it names. Raises 401 on any invalid/expired/unknown token."""
    admin = _decode_admin_token(credentials.credentials, db)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    return admin


# ---------------------------------------------------------------------------
# Public-user auth (app/models/user.py) -- separate audience from the
# AdminUser JWTs above. get_current_user is what suggestion/vote endpoints
# use; get_current_user_optional handles endpoints that work anonymously
# and only need the identity when one is present.
# ---------------------------------------------------------------------------

def _decode_user_token(token: str, db: Session) -> Optional[User]:
    """Shared decode logic for get_current_user / get_current_user_optional.
    Returns None (never raises) on any invalid/expired/unknown token, or
    on a token minted for a different audience (an admin token is not a
    user credential, regardless of sub)."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != _USER_TYPE:
        return None
    return db.get(User, int(payload["sub"]))


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: decode the bearer token, load and return the
    public User it names. Raises 401 on any invalid/expired/unknown token
    (or on an admin token -- different audience)."""
    user = _decode_user_token(credentials.credentials, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    return user


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """FastAPI dependency like get_current_user, but returns None when no
    (or no valid) Authorization header is present instead of raising.
    Callers must only use this to *enrich* an otherwise-anonymous request
    (e.g. "did the logged-in user already vote on this suggestion?"), never
    to gate access on it."""
    if credentials is None:
        return None
    return _decode_user_token(credentials.credentials, db)


def require_admin(
    x_admin_api_key: Optional[str] = Header(None, alias="X-Admin-Api-Key"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[AdminUser]:
    """FastAPI dependency for admin routes: accepts EITHER the shared
    X-Admin-Api-Key header OR a bearer JWT from POST /admin/login.

    - Valid shared key -> returns None (scripted/ETL caller, no single
      admin identity -- this is the same access existing callers have
      always had, unchanged).
    - Valid bearer token -> returns the AdminUser it names, so routes
      can use `admin.username` / `admin.role` for logging or
      authorization once that's needed.
    - Neither present or both invalid -> 401.

    This is what actually wires get_current_admin's JWT verification
    into a live endpoint (see app/api/admin.py) -- previously
    POST /admin/login issued tokens that nothing ever checked.
    """
    settings = get_settings()
    if x_admin_api_key is not None and secrets.compare_digest(x_admin_api_key, settings.admin_api_key):
        return None

    if credentials is not None:
        admin = _decode_admin_token(credentials.credentials, db)
        if admin is not None:
            return admin

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing admin credentials (X-Admin-Api-Key header or bearer token).",
    )


# ---------------------------------------------------------------------------
# Role-based authorization on top of require_admin above.
#
# Two roles (matches AdminUser.role, a free-text column that already
# defaults to "admin" -- see app/models/admin_user.py):
#
#   editor: create_stop, create_route, add_route_stop -- routine dataset
#     growth/fixes. Additive and easy to undo if wrong.
#   admin:  everything editor can do, plus update_route_status and
#     reload_graph_cache -- these can immediately change what
#     /route-finder returns to real users, so they're held to a
#     narrower set of accounts.
#
# See app/api/admin.py for which endpoint requires which role.
# ---------------------------------------------------------------------------
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"


def require_role(*allowed_roles: str):
    """FastAPI dependency factory: gate a route to callers whose
    AdminUser.role is one of allowed_roles. Use as e.g.
    `Depends(require_role(ROLE_EDITOR, ROLE_ADMIN))` on a route.

    The shared X-Admin-Api-Key path (require_admin returns None for it
    -- see above) carries no per-admin identity at all, so it has no
    role to check and keeps the same unrestricted access it has always
    had; this only adds enforcement on the JWT/AdminUser path. Splitting
    the shared key itself into role-scoped keys would be a bigger change
    than this taxonomy covers -- a reasonable follow-up if scripted/ETL
    callers ever need restricting too, but out of scope here since that
    key has always meant "full access" for those callers.
    """

    def _check(admin: Optional[AdminUser] = Depends(require_admin)) -> Optional[AdminUser]:
        if admin is not None and admin.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}.",
            )
        return admin

    return _check
