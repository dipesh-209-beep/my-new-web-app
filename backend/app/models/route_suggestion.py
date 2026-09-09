"""
SQLAlchemy ORM model for a crowd-sourced route/stop change suggestion.

A person without admin credentials proposes an edit (rename a stop, rename
a route, reorder a route's stops). Suggestions start 'pending'; identical
pending suggestions deduplicate into votes (see suggestion_votes.py and
app/api/suggestions.py) rather than piling up; once vote_count reaches
AUTO_APPLY_VOTE_THRESHOLD (app/core/config.py) the change is applied to the
live dataset with status 'auto_applied', and an editor/admin can also
approve/reject it manually (app/api/suggestions.py::review_suggestion).

Column choices:

  - target_type/target_id: which entity the change touches ('stop' or
    'route'), so one table serves stop, route, and route-sequence changes.
  - suggestion_type: the kind of change payload carries
    ('stop_name_change' | 'route_name_change' | 'stop_sequence_change').
    Stored both here and inside payload (the discriminated-union tag);
    this column is what the dedup + status transitions key on, and the
    tag in payload is what Pydantic's union discrimination needs.
  - payload (JSONB): the RAW client payload dict, exactly as submitted
    (without suggestion_type), so hashing it reproduces the client's
    bytes. payload_hash is sha256(json.dumps(payload, sort_keys=True)).
  - payload_hash: lets an identical future submission be recognized as a
    vote instead of a duplicate row. The partial unique index on
    (target_type, target_id, suggestion_type, payload_hash) only applies
    to 'pending' suggestions -- once applied/rejected the same change
    becomes suggestable again (e.g. an applied name gets suggested back).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    TIMESTAMP,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RouteSuggestion(Base):
    __tablename__ = "route_suggestions"

    suggestion_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    # Author. SET NULL (not CASCADE) on account deletion so a suggestion
    # with real support isn't silently wiped when a throwaway account goes.
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- what's being changed ---
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(50), nullable=False)
    suggestion_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # --- the change itself ---
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- lifecycle ---
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    # Number of distinct users backing this (author's own submission counts
    # as one). Denormalized to avoid a COUNT() join on every listing/vote.
    vote_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Who made the final call (only set on manual approve/reject; auto-applied
    # suggestions record reviewed_at but no reviewer). SET NULL if the admin
    # account is later deleted.
    reviewed_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("admin_users.admin_id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("target_type IN ('stop', 'route')", name="ck_route_suggestions_target_type"),
        CheckConstraint(
            "suggestion_type IN ('stop_name_change', 'route_name_change', 'stop_sequence_change')",
            name="ck_route_suggestions_suggestion_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'auto_applied')",
            name="ck_route_suggestions_status",
        ),
        # Dedup key -- only while pending, so an already-handled change can be
        # suggested again without colliding.
        Index(
            "ix_route_suggestions_dedup",
            "target_type",
            "target_id",
            "suggestion_type",
            "payload_hash",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_route_suggestions_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"RouteSuggestion(id={self.suggestion_id}, "
            f"target={self.target_type}:{self.target_id}, "
            f"type={self.suggestion_type}, status={self.status}, votes={self.vote_count})"
        )