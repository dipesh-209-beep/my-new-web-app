"""Authentication and authorization for privileged endpoints.

Three kinds of credential can act on admin routes, and *all three* are
reduced to the same thing before an endpoint runs: a `Principal`
carrying an explicit permission set.

    human admin       JWT from POST /admin/login
                      -> permissions from the AdminUser's role
                         (editor / admin -- see ROLE_PERMISSIONS)
    automated caller  SvcKey from POST /admin/service-credentials
                      -> permissions are exactly the scopes stored on
                         that credential, no more
    legacy shared key X-Admin-Api-Key
                      -> DISABLED by default and *always* rejected in
                         production (see ALLOW_LEGACY_SHARED_ADMIN_KEY
                         in core/config.py). Development-only escape
                         hatch; see require_admin() for the details.

Endpoints gate on `require_permissions(...)`, never on "is this the
right kind of credential" and never on a UI decision. A Principal
whose permission set doesn't include what the endpoint needs gets a
403 regardless of how it authenticated, which is what stops an editor
from flipping a route's status and what stops a narrowly-scoped
service credential from doing anything it wasn't minted for.

The two audiences that must never be confused stay separately typed
and separately decodable: AdminUser tokens carry type="admin", public
User tokens type="user" (see _ADMIN_TYPE/_USER_TYPE below), and
service credentials are not JWTs at all -- they are looked up by
key_id and hash-compared. A leaked public-user token therefore cannot
satisfy an admin dependency under any configuration.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import get_settings
from ..db.session import get_db
from ..models import ACTOR_ADMIN_USER, ACTOR_SERVICE_CREDENTIAL, AdminUser, ServiceCredential, User

logger = logging.getLogger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_scheme = HTTPBearer()
# auto_error=False: a missing Authorization header must fall through to
# the other credentials in require_principal() below, not raise on its
# own -- only require_principal() decides when all of them have failed.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return _pwd_context.verify(plain_password, password_hash)


# ---------------------------------------------------------------------------
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
    """Shared decode logic for get_current_admin and require_principal.
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
    try:
        admin_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return db.get(AdminUser, admin_id)


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
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return db.get(User, user_id)


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


# ---------------------------------------------------------------------------
# Service credentials -- scoped, revocable, hashed-at-rest API keys for
# automated callers. See app/models/service_credential.py for the schema
# and app/api/service_credentials.py for the create/list/revoke endpoints.
# ---------------------------------------------------------------------------

SVC_KEY_SCHEME = "SvcKey"
SVC_KEY_ID_PREFIX = "svc_"
# Bytes of entropy in the secret half. token_urlsafe(32) -> ~43 chars.
SVC_SECRET_BYTES = 32


def generate_service_key() -> tuple[str, str, str]:
    """Mint a new service credential.

    Returns `(presented_key, key_id, key_hash)`:
      * `presented_key`  -- "SvcKey <key_id>.<secret>"; shown to the
        operator exactly once, at creation time, and never stored.
      * `key_id`         -- the public "svc_..." handle, stored and
        safe to log.
      * `key_hash`       -- SHA-256 hex of the secret half; the only
        secret-derived thing that touches the database.

    secrets.token_urlsafe is a CSPRNG (SystemRandom-backed), not
    random.getrandbits, so the handle is not predictable from a guess
    of how many credentials exist.
    """
    # 12 hex chars of handle: long enough that a collision is
    # implausible and short enough to read in a config file.
    key_id = f"{SVC_KEY_ID_PREFIX}{secrets.token_hex(6)}"
    secret = secrets.token_urlsafe(SVC_SECRET_BYTES)
    return f"{SVC_KEY_SCHEME} {key_id}.{secret}", key_id, hash_service_secret(secret)


def hash_service_secret(secret: str) -> str:
    """SHA-256 hex digest of a service-credential secret half.

    A fast unsalted hash is the correct primitive here, unlike for a
    human password: the input is 256 bits of CSPRNG output, so there
    is no dictionary to attack and no need for a deliberately slow KDF
    to stretch entropy that isn't there. What protects the stored
    digest in transit/at rest is database access control and volume
    encryption, which is a different (and enforced) control.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_service_key(presented: str) -> Optional[tuple[str, str]]:
    """Split "SvcKey <key_id>.<secret>" into (key_id, secret).

    Returns None for anything that isn't that shape, so a caller can
    treat a malformed header as "this credential type wasn't
    presented" rather than as an auth failure it must report. Parsing
    is deliberately strict: exactly one space, exactly one dot, both
    halves non-empty, key_id carrying the expected prefix.
    """
    if not presented:
        return None
    scheme, _, rest = presented.partition(" ")
    if scheme != SVC_KEY_SCHEME or not rest:
        return None
    key_id, dot, secret = rest.partition(".")
    if not dot or not key_id or not secret:
        return None
    if not key_id.startswith(SVC_KEY_ID_PREFIX) or len(key_id) <= len(SVC_KEY_ID_PREFIX):
        return None
    return key_id, secret


def _load_service_credential(key_id: str, secret: str, db: Session) -> Optional[ServiceCredential]:
    """Look up a service credential and verify the secret half.

    Returns the row only when the key exists, is not revoked, is not
    expired, and the presented secret matches. Everything else is
    None -- deliberately indistinguishable to the caller, so this
    can't be used as an oracle for "does this key_id exist?".
    """
    row = db.scalar(select(ServiceCredential).where(ServiceCredential.key_id == key_id))
    if row is None:
        return None
    # Constant-time comparison of the digests, so a caller can't learn
    # the stored hash a character at a time. (The row lookup above
    # already means the key_id must be right; this guards the secret.)
    if not hmac.compare_digest(hash_service_secret(secret), row.key_hash):
        return None
    if row.revoked_at is not None:
        return None
    if row.expires_at is not None:
        # expires_at is timestamptz; normalise the comparison operand
        # so a naive value can't raise TypeError mid-request.
        now = datetime.now(timezone.utc)
        expiry = row.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            return None
    return row


def touch_service_credential(credential: ServiceCredential) -> None:
    """Best-effort last_used_at stamp, on its own session.

    Deliberately *not* on the request's session. Three reasons, all of
    which cost a correctness bug if the obvious implementation is used:

      * A savepoint (`session.begin_nested()`) is not enough. Releasing
        a savepoint folds the write into the outer transaction, and a
        read-only request (GET /admin/suggestions, say) never commits
        that transaction -- so the stamp was silently discarded. It has
        to be committed by someone.
      * Committing the *request's* session here would couple an
        operational nicety to the request's transaction, and a
        subsequent rollback would be ambiguous about whether the stamp
        was meant to survive.
      * Any failed statement on the request's session aborts the whole
        transaction until rollback, turning a cosmetic nicety into a 500
        from an unrelated query further down the request.

    Its own session makes all three impossible. The cost is one extra
    short-lived connection per authenticated service-credential request,
    which only happens on admin endpoints -- never on the public hot
    path. Never raises: a failure to record "this key was used" must not
    fail a request that has already been authorized.
    """
    # Imported here rather than at module scope: db/session.py imports
    # core/config.py, and core/security.py is imported by it.
    from ..db.session import SessionLocal

    session = SessionLocal()
    try:
        with session.begin():
            session.execute(
                update(ServiceCredential)
                .where(ServiceCredential.credential_id == credential.credential_id)
                .values(last_used_at=datetime.now(timezone.utc))
                .execution_options(synchronize_session=False)
            )
    except Exception:  # pragma: no cover - defensive
        logger.warning(
            "Failed to update last_used_at for service credential %s; request continues",
            credential.key_id,
            exc_info=True,
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Authorization model.
#
# Two human roles, matching AdminUser.role (see app/models/admin_user.py):
#
#   editor: routine dataset growth/fixes -- stops, routes, route-stop
#     membership/order, and reviewing crowd-sourced suggestions.
#     Additive and easy to undo if wrong.
#   admin:  everything editor can do, plus flipping a route's status and
#     reloading the routing graph. Both change what /route-finder returns
#     to real users immediately, so they're held to a narrower set of
#     accounts.
#
# Those roles are expressed as permission sets rather than as role names
# at every call site, because a service credential has no role -- only
# scopes. Keeping one vocabulary (permissions) means an endpoint states
# exactly what it needs and both credential types are checked the same
# way. See app/api/admin.py for which endpoint requires which set.
# ---------------------------------------------------------------------------
PERM_STOPS_WRITE = "stops:write"
PERM_ROUTES_WRITE = "routes:write"
PERM_ROUTE_STOPS_WRITE = "route_stops:write"
PERM_SUGGESTIONS_REVIEW = "suggestions:review"
PERM_ROUTES_STATUS = "routes:status"
PERM_GRAPH_RELOAD = "graph:reload"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        PERM_STOPS_WRITE,
        PERM_ROUTES_WRITE,
        PERM_ROUTE_STOPS_WRITE,
        PERM_SUGGESTIONS_REVIEW,
        PERM_ROUTES_STATUS,
        PERM_GRAPH_RELOAD,
    }
)

# What each human role may do. admin is a strict superset of editor.
EDITOR_PERMISSIONS: frozenset[str] = frozenset(
    {PERM_STOPS_WRITE, PERM_ROUTES_WRITE, PERM_ROUTE_STOPS_WRITE, PERM_SUGGESTIONS_REVIEW}
)
ADMIN_ONLY_PERMISSIONS: frozenset[str] = frozenset({PERM_ROUTES_STATUS, PERM_GRAPH_RELOAD})
ADMIN_PERMISSIONS: frozenset[str] = EDITOR_PERMISSIONS | ADMIN_ONLY_PERMISSIONS

ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_EDITOR: EDITOR_PERMISSIONS,
    ROLE_ADMIN: ADMIN_PERMISSIONS,
}

# What a role of an unrecognised value is allowed to do. Empty on
# purpose: if someone hand-edits admin_users.role to "superuser" the
# safe reading is "no permissions I don't have a mapping for", not
# "all of them". Unknown roles are logged by the caller-facing
# dependency so a typo is visible rather than silently restrictive.
DEFAULT_ROLE_PERMISSIONS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Principal:
    """Whoever is making the current request, and what they may do.

    Exactly one of `admin_user` / `credential` is set. `actor_type` and
    `actor_id` are the identity as it should appear in the audit log
    (app/core/admin_audit.py) and as the `principal` attached to
    `request.state` by the audit middleware.
    """

    actor_type: str
    actor_id: str | None
    permissions: frozenset[str]
    admin_user: Optional[AdminUser] = None
    credential: Optional[ServiceCredential] = None
    # Free-form provenance for the audit log, e.g. {"reason": "..."}
    # for a denial. Never holds a secret.
    context: dict = field(default_factory=dict)

    @property
    def is_human(self) -> bool:
        return self.admin_user is not None

    @property
    def admin_id(self) -> Optional[int]:
        return self.admin_user.admin_id if self.admin_user is not None else None

    def has(self, *required: str) -> bool:
        return all(permission in self.permissions for permission in required)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Principal {self.actor_type}:{self.actor_id} perms={sorted(self.permissions)}>"


def require_admin(
    request: Request,
    x_admin_api_key: Optional[str] = Header(None, alias="X-Admin-Api-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> Principal:
    """FastAPI dependency: authenticate a privileged request and return
    the Principal it belongs to. Raises 401 unless *some* valid
    credential is presented.

    Credentials tried, in order:
      1. `Authorization: SvcKey <key_id>.<secret>` -> that credential's
         scopes.
      2. `Authorization: Bearer <jwt>` -> the named AdminUser's role
         permissions.
      3. `X-Admin-Api-Key: <shared secret>` -> FULL_PERMISSIONS, but
         only when `ALLOW_LEGACY_SHARED_ADMIN_KEY` is enabled, which
         defaults to False and is rejected outright by
         validate_production_settings() in production. This is the
         replacement for the old unconditional behaviour: the key no
         longer grants anything unless someone deliberately turns the
         escape hatch on for a local dev session, and turning it on in
         production stops the process from starting at all.

    This dependency only establishes *who* the caller is. It does not
    decide what they may do -- that is require_permissions below, and
    every privileged route must use one or the other. Keeping
    authentication and authorization as separate steps is what lets
    the same principal be checked against a fine-grained permission
    set without re-parsing credentials per endpoint.
    """
    settings = get_settings()

    if authorization:
        scheme, _, rest = authorization.partition(" ")
        rest = rest.strip()

        parsed = parse_service_key(authorization)
        if parsed is not None:
            credential = _load_service_credential(parsed[0], parsed[1], db)
            if credential is not None:
                touch_service_credential(credential)
                return Principal(
                    actor_type=ACTOR_SERVICE_CREDENTIAL,
                    actor_id=credential.key_id,
                    permissions=frozenset(credential.scopes or ()),
                    credential=credential,
                )
            # A well-formed SvcKey that doesn't resolve is a failed
            # credential, not a reason to fall through and try
            # interpreting the same header as something else.
            _reject(request, "service_credential", "invalid_service_credential", rest.partition(".")[0])
        elif scheme.lower() == SVC_KEY_SCHEME.lower():
            # "SvcKey" with a malformed body: a genuine attempt to use
            # this scheme, so reject rather than fall through.
            _reject(request, "service_credential", "malformed_service_credential", None)

        if scheme.lower() == "bearer":
            admin = _decode_admin_token(rest, db)
            if admin is not None:
                permissions = ROLE_PERMISSIONS.get(admin.role, DEFAULT_ROLE_PERMISSIONS)
                return Principal(
                    actor_type=ACTOR_ADMIN_USER,
                    actor_id=admin.username,
                    permissions=permissions,
                    admin_user=admin,
                )

    if (
        x_admin_api_key is not None
        and settings.ALLOW_LEGACY_SHARED_ADMIN_KEY
        and secrets.compare_digest(x_admin_api_key, settings.admin_api_key)
    ):
        # Development-only escape hatch, off by default and refused
        # outright by validate_production_settings() in production. No
        # human identity to attribute, so the actor is recorded as
        # actor_id="shared-api-key" rather than pretending to be a person.
        return Principal(
            actor_type=ACTOR_ADMIN_USER,
            actor_id="shared-api-key",
            permissions=ALL_PERMISSIONS,
        )

    if x_admin_api_key is not None:
        # A caller who presented the retired shared secret. Worth its own
        # audit reason rather than being folded into
        # "missing_credentials": after this change, any use of
        # X-Admin-Api-Key is either a mistake or an attempt to use a
        # credential that is supposed to be dead, and "is anyone still
        # trying the old key?" is one of the first questions asked after a
        # breach. The response is deliberately identical to the
        # no-credentials case so this reveals nothing to the caller, but
        # the audit row says exactly what happened.
        _reject(
            request,
            "anonymous",
            (
                "legacy_shared_key_disabled"
                if not settings.ALLOW_LEGACY_SHARED_ADMIN_KEY
                else "legacy_shared_key_invalid"
            ),
            None,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Missing or invalid credentials. Supply a bearer token from POST /admin/login, "
                "or a scoped service credential via 'Authorization: SvcKey <key_id>.<secret>'."
            ),
        )

    _reject(
        request,
        "anonymous",
        "missing_credentials",
        None,
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Missing or invalid credentials. Supply a bearer token from POST /admin/login, "
            "or a scoped service credential via 'Authorization: SvcKey <key_id>.<secret>'."
        ),
    )
    raise AssertionError("unreachable: _reject always raises")  # pragma: no cover


def _reject(
    request: Request,
    actor_type: str,
    reason: str,
    key_id: str | None,
    status_code: int = status.HTTP_401_UNAUTHORIZED,
    detail: str = "Invalid or missing credentials.",
) -> None:
    """Record a failed authentication attempt and raise 401.

    Writes the audit row on its own session (the request's session is
    about to be torn down by the failed dependency, and there is no
    transaction to join). Never lets an audit-write problem turn a
    clean 401 into a 500: a failure to log is itself logged at ERROR.
    """
    from .admin_audit import record_security_event  # local import: avoids an import cycle

    record_security_event(
        actor_type=actor_type,
        actor_id=key_id,
        action=f"auth.{reason}",
        resource_type="admin_endpoint",
        resource_id=request.url.path[:100],
        success=False,
        request_id=getattr(request.state, "request_id", None),
        client_ip=getattr(request.state, "client_ip", None),
        detail={"method": request.method, "reason": reason},
    )
    raise HTTPException(status_code=status_code, detail=detail)


def require_permissions(*required: str, human_only: bool = False):
    """FastAPI dependency factory: authenticate, then gate on an explicit
    permission set.

    Use as e.g. `Depends(require_permissions(PERM_STOPS_WRITE))` on a
    route. The caller must hold *every* permission in `required`.

    Works identically for both credential types: an AdminUser's
    permissions come from their role (ROLE_PERMISSIONS), a service
    credential's from its stored scopes. Neither can exceed the other
    -- an admin token can't invent permissions, and a credential can't
    be granted a permission it wasn't scoped with at creation.

    `human_only=True` additionally rejects service credentials, for
    endpoints that mint or destroy *other* credentials. Without it a
    credential holding e.g. PERM_STOPS_WRITE could mint itself a
    credential with PERM_GRAPH_RELOAD and escalate; with it, only a
    logged-in admin account can do that.
    """
    unknown = set(required) - ALL_PERMISSIONS
    if unknown:  # pragma: no cover - a programming error, caught at import
        raise ValueError(f"Unknown permission(s) requested: {sorted(unknown)}")

    def _check(
        request: Request,
        principal: Principal = Depends(require_admin),
    ) -> Principal:
        if human_only and not principal.is_human:
            _deny(
                request,
                principal,
                required_permissions=required,
                reason="service_credential_not_permitted",
                detail="This action requires a human administrator account, not a service credential.",
            )
        missing = [permission for permission in required if permission not in principal.permissions]
        if missing:
            _deny(
                request,
                principal,
                required_permissions=required,
                reason="missing_permissions",
                detail=(
                    "This action requires the following permission(s): "
                    f"{', '.join(missing)}."
                ),
            )
        return principal

    return _check


def require_role(*allowed_roles: str, human_only: bool = True):
    """FastAPI dependency factory: gate a route to *named human roles*
    rather than to a permission set.

    Prefer require_permissions(...) for ordinary data endpoints -- it
    says what the endpoint needs, and it lets a scoped service
    credential satisfy it. Use require_role where the check is really
    "must be this person/role", e.g. managing administrator accounts,
    which is not expressible as a data permission and should not be
    reachable by automation at all.

    A role with no entry in ROLE_PERMISSIONS is refused rather than
    treated as privileged; a service credential is refused outright
    (human_only defaults True here).
    """

    def _check(request: Request, principal: Principal = Depends(require_admin)) -> Principal:
        if human_only and not principal.is_human:
            _deny(
                request,
                principal,
                required_permissions=(),
                required_roles=allowed_roles,
                reason="service_credential_not_permitted",
                detail="This action requires a human administrator account, not a service credential.",
            )
        role = principal.admin_user.role if principal.admin_user is not None else None
        if role not in allowed_roles:
            _deny(
                request,
                principal,
                required_permissions=(),
                required_roles=allowed_roles,
                reason="role_not_permitted",
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}.",
            )
        return principal

    return _check


def _deny(
    request: Request,
    principal: Principal,
    *,
    required_permissions: tuple[str, ...],
    detail: str,
    required_roles: tuple[str, ...] = (),
    reason: str | None = None,
) -> None:
    """Audit a 403 and raise it.

    A 403 is a more interesting event than a 401: the caller proved
    *who they are* and were still refused, which is what an escalation
    attempt or a misconfigured service credential looks like. Both get
    written to admin_audit_log on a separate session (there is no
    request transaction to join -- nothing was changed), and a failure
    to write is logged rather than allowed to turn a 403 into a 500.
    """
    from .admin_audit import record_authorization_denial  # local import: avoids an import cycle

    record_authorization_denial(
        principal,
        required_permissions=required_permissions,
        request=request,
        required_roles=required_roles,
        reason=reason,
    )
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
