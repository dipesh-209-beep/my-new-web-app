"""
SQLAlchemy ORM model for one user's vote on a suggestion.

A vote IS a duplicate submission: when a logged-in user submits an
identical change to a still-pending suggestion (same target + type +
payload_hash -- see app/api/suggestions.py), that submission is recorded
here as a vote rather than minting a second pending row. One user can back
a given suggestion at most once (unique constraint), incl. the author.

CASCADE deletes: votes are meaningless without their suggestion, and
without the user who cast them -- authorization data, not content, so it
disappears with either side.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SuggestionVote(Base):
    __tablename__ = "suggestion_votes"

    vote_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    suggestion_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("route_suggestions.suggestion_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("suggestion_id", "user_id", name="uq_suggestion_votes_suggestion_user"),
        Index("ix_suggestion_votes_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"SuggestionVote(suggestion_id={self.suggestion_id}, user_id={self.user_id})"