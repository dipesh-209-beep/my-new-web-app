"""
SQLAlchemy ORM model for public (non-admin) user accounts.

Users sign up via POST /auth/register to submit and vote on
crowd-sourced route/stop suggestions (see app/api/auth.py and
app/api/suggestions.py). Deliberately separate from AdminUser
(app/models/admin_user.py): admins authenticate with the shared
X-Admin-Api-Key or their own JWT (security.require_admin), while these
accounts authenticate only with a user-scoped JWT
(security.get_current_user). A user token must never satisfy
require_admin -- see the "type" claim in app/core/security.py DCZ.
"""

from datetime import datetime

from sqlalchemy import Integer, String, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    """Represents a public user who can create/vote on route suggestions."""

    __tablename__ = "users"

    # ----------------------------------------
    # Primary Key
    # ----------------------------------------
    user_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # ----------------------------------------
    # Login Credentials
    # ----------------------------------------
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # ----------------------------------------
    # Metadata
    # ----------------------------------------
    # DB-generated, timezone-aware, matching every other table's created_at
    # (AdminUser, Route, Stop, SegmentCongestionStat).
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    # ----------------------------------------
    # Debug Representation
    # ----------------------------------------
    def __repr__(self) -> str:
        return (
            f"User("
            f"id={self.user_id}, "
            f"username='{self.username}')"
        )