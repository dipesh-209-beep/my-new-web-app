"""add route_suggestions and suggestion_votes tables

Crowd-sourced edit proposals (rename stop, rename route, reorder route
stops) with user votes and admin review. See app/models/route_suggestion.py
and app/models/suggestion_vote.py for the column rationale -- the key
points carried into the migration:

  - payload is JSONB (exact client bytes) + payload_hash so identical
    submissions deduplicate into votes.
  - The dedup index is partial (WHERE status = 'pending') so a change
    that has already been handled can be suggested again.
  - status/target_type/suggestion_type are CHECK-constrained, mirroring
    the ck_admin_users_role pattern (9486a80c9dd1).
  - created_at/updated_at follow the TIMESTAMP(timezone=True) +
    server_default now() pattern (f56a50612cd8).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'route_suggestions',
        sa.Column('suggestion_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('target_type', sa.String(length=20), nullable=False),
        sa.Column('target_id', sa.String(length=50), nullable=False),
        sa.Column('suggestion_type', sa.String(length=30), nullable=False),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('vote_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('suggestion_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['admin_users.admin_id'], ondelete='SET NULL'),
    )
    op.create_index('ix_route_suggestions_user_id', 'route_suggestions', ['user_id'])
    op.create_index('ix_route_suggestions_status', 'route_suggestions', ['status'])
    op.create_index(
        'ix_route_suggestions_dedup',
        'route_suggestions',
        ['target_type', 'target_id', 'suggestion_type', 'payload_hash'],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_check_constraint(
        "ck_route_suggestions_target_type",
        "route_suggestions",
        "target_type IN ('stop', 'route')",
    )
    op.create_check_constraint(
        "ck_route_suggestions_suggestion_type",
        "route_suggestions",
        "suggestion_type IN ('stop_name_change', 'route_name_change', 'stop_sequence_change')",
    )
    op.create_check_constraint(
        "ck_route_suggestions_status",
        "route_suggestions",
        "status IN ('pending', 'approved', 'rejected', 'auto_applied')",
    )

    op.create_table(
        'suggestion_votes',
        sa.Column('vote_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('suggestion_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('vote_id'),
        sa.ForeignKeyConstraint(['suggestion_id'], ['route_suggestions.suggestion_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.UniqueConstraint('suggestion_id', 'user_id', name='uq_suggestion_votes_suggestion_user'),
    )
    op.create_index('ix_suggestion_votes_suggestion_id', 'suggestion_votes', ['suggestion_id'])
    op.create_index('ix_suggestion_votes_user_id', 'suggestion_votes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_suggestion_votes_user_id', table_name='suggestion_votes')
    op.drop_index('ix_suggestion_votes_suggestion_id', table_name='suggestion_votes')
    op.drop_table('suggestion_votes')

    op.drop_constraint('ck_route_suggestions_status', 'route_suggestions', type_='check')
    op.drop_constraint('ck_route_suggestions_suggestion_type', 'route_suggestions', type_='check')
    op.drop_constraint('ck_route_suggestions_target_type', 'route_suggestions', type_='check')
    op.drop_index('ix_route_suggestions_dedup', table_name='route_suggestions')
    op.drop_index('ix_route_suggestions_status', table_name='route_suggestions')
    op.drop_index('ix_route_suggestions_user_id', table_name='route_suggestions')
    op.drop_table('route_suggestions')