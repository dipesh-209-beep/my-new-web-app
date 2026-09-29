"""
ORM model for `admin_audit_log`.

Append-only record of every security-relevant administrative action:
successful and failed admin logins, every admin write to stops/routes/
route-stops/status, graph reloads, suggestion reviews, and service
credential lifecycle changes.

What this table is and is not
-----------------------------
* It IS an application-level audit trail: written inside the same
  transaction as the mutation it describes (so a rolled-back mutation
  cannot leave a "success" row behind, and a failed audit INSERT rolls
  the mutation back), attributable to a named admin or a named service
  credential, and readable by an operator.
* It is NOT tamper-proof. Any account able to write to this table can
  also update or delete rows in it, and the rows live in the same
  database as everything else. An attacker who reaches the database
  with application credentials could rewrite history here. Making it
  tamper-evident or immutable requires something outside this schema
  (append-only/WORM storage, `pgcrypto` signatures, or shipping rows
  to an external log sink) and is called out as remaining work in
  docs/security.md rather than being implied by the table's existence.

Privacy: rows deliberately never contain passwords, password hashes,
JWTs, API keys, raw Authorization/X-Admin-Api-Key headers, or
end-user coordinates. See app/core/admin_audit.py for the field-level
scrubbing that enforces that on `detail`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

# actor_type values. Kept as module constants because both the writer
# (app/core/admin_audit.py) and the reader-side tests key off them.
ACTOR_ADMIN_USER = "admin_user"
ACTOR_SERVICE_CREDENTIAL = "service_credential"
ACTOR_ANONYMOUS = "anonymous"
ACTOR_TYPES = (ACTOR_ADMIN_USER, ACTOR_SERVICE_CREDENTIAL, ACTOR_ANONYMOUS)


class AdminAuditLog(Base):
    """One row per security-relevant administrative action."""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # Who acted.
    #   admin_user         -> actor_id is the AdminUser.username
    #   service_credential -> actor_id is the ServiceCredential.key_id
    #   anonymous          -> actor_id is NULL (no valid credential was
    #                          presented, e.g. a failed login)
    actor_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    actor_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # What happened, as a stable dotted verb: "admin.login.success",
    # "admin.login.failure", "stop.create", "route.status.update",
    # "graph.reload", "service_credential.create", ...
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # What was acted upon. resource_type is a coarse noun ("stop",
    # "route", "route_stop", "graph", "admin_user",
    # "service_credential", "suggestion"); resource_id is that
    # resource's own identifier, NULL for actions that aren't scoped to
    # a single row (e.g. a graph reload).
    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    resource_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # True only when the action actually took effect. Auth failures and
    # authorization denials are recorded with success=False rather than
    # being dropped -- a denied attempt is exactly what an operator
    # wants to see.
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    # When it happened (DB clock, so it can't be back-dated by a client).
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # Correlates every row produced by one HTTP request, and matches the
    # X-Request-ID response header, so a report or a bug report can be
    # traced to the exact log lines. See app/core/request_id.py.
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Source address of the request. Recorded for administrative
    # actions only -- this is a security log, and "which address made
    # this change" is the whole point of a failed-login row. Not used
    # anywhere else in the app and never joined to end-user data.
    client_ip: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Small, scrubbed JSON object with non-identifying context: field
    # names that changed, counts, a rejection reason, HTTP status.
    # Never coordinates, credentials, or free-text bodies. Scrubbed by
    # app/core/admin_audit.py::scrub_detail before it gets here.
    detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('admin_user', 'service_credential', 'anonymous')",
            name="ck_admin_audit_actor_type",
        ),
        # Every row must say *who* acted and *what* they did; only the
        # anonymous actor_type is allowed a NULL actor_id.
        CheckConstraint(
            "actor_type = 'anonymous' OR actor_id IS NOT NULL",
            name="ck_admin_audit_actor_id_present",
        ),
        # Default read pattern is "most recent first", occasionally
        # filtered to one action type.
        Index("ix_admin_audit_timestamp", text("timestamp DESC")),
        Index("ix_admin_audit_action_timestamp", "action", text("timestamp DESC")),
        Index("ix_admin_audit_actor", "actor_type", "actor_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AdminAuditLog {self.timestamp} {self.action} "
            f"actor={self.actor_type}:{self.actor_id} "
            f"resource={self.resource_type}:{self.resource_id} success={self.success}>"
        )
