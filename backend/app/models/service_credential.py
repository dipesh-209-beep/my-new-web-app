"""
ORM model for `service_credentials`.

Replaces the single shared `X-Admin-Api-Key` secret as the way an
*automated* caller (ETL script, scheduled import, monitoring probe)
authenticates. The old shared key was a bearer token for "everything,
forever, unrotatable, unrevocable, and unattributable to anyone"; a
service credential is the same idea with the blast radius written down
and stored in the database, so it can be scoped, expired, revoked, and
audited.

Shape of a credential, as presented to a caller:

    Authorization: SvcKey svc_1a2b3c4d5e6f....<secret>

`key_id` (the `svc_...` prefix) is the public lookup handle and is safe
to log, show in a UI, and put in a CI config. `key_hash` is the only
thing stored -- a SHA-256 of the secret half. The secret half is 32
bytes from `secrets.token_urlsafe`, so there is no feasible offline
recovery of it from the hash (a fast hash is the right primitive here
precisely because the input already has ~256 bits of entropy; see
app/core/security.py::hash_service_secret for the reasoning).
"""

from datetime import datetime

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Index, Integer, String, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ServiceCredential(Base):
    """A scoped, revocable API credential for an automated caller."""

    __tablename__ = "service_credentials"

    # ----------------------------------------
    # Primary Key
    # ----------------------------------------
    credential_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # ----------------------------------------
    # Identity
    # ----------------------------------------
    # Public lookup handle, e.g. "svc_9f2c1ab34de5". Not a secret: it is
    # safe to log, display, and reference in audit rows. UNIQUE so a
    # lookup by handle is a single-row index hit.
    key_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )

    # Human label for whoever has to work out what this key is for in
    # six months, e.g. "nightly OSM stop import". Also UNIQUE so two
    # credentials can't be told apart by name in the UI.
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )

    # ----------------------------------------
    # Secret material -- hash only, never the key itself
    # ----------------------------------------
    # SHA-256 (hex) of the secret half of the presented credential. The
    # raw key is returned exactly once, at creation time, and is not
    # recoverable from this column afterwards.
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # ----------------------------------------
    # Authorization
    # ----------------------------------------
    # The explicit permission set this credential may exercise. Every
    # value must be one of app/core/security.py's PERM_* constants --
    # enforced there at creation time (a credential is created through
    # the API, never by hand-editing rows). An empty list is rejected,
    # so a credential always has a non-empty purpose.
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )

    # ----------------------------------------
    # Lifecycle
    # ----------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # NULL = never expires. When set, authentication rejects the
    # credential past this instant (see
    # app/core/security.py::_load_service_credential).
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # NULL = active. Set when an admin revokes it; the row is kept (not
    # deleted) so the audit trail and any credential_id references stay
    # resolvable.
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Best-effort operational signal -- updated on each successful
    # authenticated request, so an operator can spot a key nobody has
    # used in months and retire it. Not a reliable audit trail (see
    # admin_audit_log.py for that); an update-per-request would be a
    # write on the hot path for an admin-only endpoint, so a
    # best-effort flush is a reasonable trade here.
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Which human admin minted this credential, for accountability.
    # ON DELETE SET NULL rather than CASCADE: deleting the admin
    # account must not silently destroy the record of a credential that
    # may still be usable.
    created_by_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("admin_users.admin_id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # Anything other than a NULL (live) or set (revoked/expired) state
        # would make "is this revoked?" ambiguous in application code.
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_service_credential_revoked_after_created",
        ),
        # A credential with no permissions is a credential that can do
        # nothing, which is never intentional and usually means the
        # caller meant to grant a scope and typo'd it. Enforced here as
        # well as in the API so a hand-inserted row can't bypass it.
        CheckConstraint(
            "array_length(scopes, 1) > 0",
            name="ck_service_credential_scopes_non_empty",
        ),
        # SHA-256 hex is exactly 64 characters. Catching a wrong-length
        # hash at write time beats debugging why every SvcKey comparison
        # silently failed.
        CheckConstraint(
            "char_length(key_hash) = 64",
            name="ck_service_credential_key_hash_length",
        ),
        Index("ix_service_credential_active", "key_id", postgresql_where=text("revoked_at IS NULL")),
    )

    def __repr__(self) -> str:  # pragma: no cover
        # Never renders key_hash: a repr that ends up in a log or a
        # traceback frame is one more copy of the secret to protect.
        return (
            f"<ServiceCredential key_id={self.key_id!r} name={self.name!r} "
            f"scopes={list(self.scopes or [])} revoked={self.revoked_at is not None}>"
        )
