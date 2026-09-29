"""add service_credentials and admin_audit_log

Two tables that together replace the single shared `X-Admin-Api-Key`
secret with something that can be scoped, revoked, and attributed:

  service_credentials  -- scoped, revocable, hashed API keys for
    automated callers. Replaces a bearer token that meant "everything,
    forever, unrotatable, unrevocable, unattributable". See
    app/models/service_credential.py for the column-by-column rationale.

  admin_audit_log      -- append-only (application-level) record of every
    privileged action, including failures. See
    app/models/admin_audit_log.py.

Both are new tables with no backfill, so this revision is purely
additive: no existing column changes type or meaning, and the downgrade
is a plain drop of the two tables and their indexes. That is why the two
live in one revision rather than two -- there is no intermediate state
in which one exists without the other that anyone would want to be in,
and a half-applied security migration is worse than a slightly larger
one.

Notes carried from the models into the DDL:

  - key_hash is a 64-char hex column, so it is exactly SHA-256 output.
    CHECK-constrained to that length so a truncated or wrongly-algorithm
    value can't be stored by hand and silently fail every comparison.
  - scopes is text[] with a DEFAULT of an empty array, but a CHECK
    requires at least one element: a credential with no permissions is
    always a mistake.
  - No CHECK on the *values* inside scopes. The valid permission names
    live in app/core/security.py and adding a permission would otherwise
    require a schema migration; unknown scopes are rejected at creation
    time instead, and a credential carrying an unrecognised scope simply
    matches no endpoint.
  - client_ip is VARCHAR(64) and only ever holds one address (the
    leftmost X-Forwarded-For entry or the socket peer -- see
    app/core/request_context.py), never a header verbatim, so a
    multi-hop XFF chain can't overflow the column.
  - The audit indexes mirror the query shapes in
    app/api/service_credentials.py::list_audit_log: (timestamp DESC),
    (action, timestamp DESC), and (actor_type, actor_id).

This is the one place in the schema that is NOT append-only at the
database level: admin_audit_log has no rule or trigger preventing
UPDATE/DELETE. The API exposes no write path for it, but anyone with
direct table access can still rewrite it. Making it tamper-evident needs
storage or signing outside this schema and is tracked in
docs/security.md as remaining work.

Revision ID: b1c2d3e4f5a6
Revises: e5f6a7b8c9d0
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'service_credentials',
        sa.Column('credential_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column(
            'scopes',
            sa.ARRAY(sa.String(length=64)),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_by_admin_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('credential_id'),
        # ON DELETE SET NULL: removing the admin account must not delete
        # the record of a credential that may still authenticate.
        sa.ForeignKeyConstraint(
            ['created_by_admin_id'],
            ['admin_users.admin_id'],
            ondelete='SET NULL',
        ),
    )
    op.create_index('ix_service_credentials_key_id', 'service_credentials', ['key_id'], unique=True)
    op.create_index('ix_service_credentials_name', 'service_credentials', ['name'], unique=True)
    # Partial index over live credentials only. The list endpoint shows
    # revoked ones too, but every *authentication* lookup filters on
    # revoked_at IS NULL, so this is the index that matters on the hot
    # path -- and it stays small as credentials accumulate.
    op.create_index(
        'ix_service_credential_active',
        'service_credentials',
        ['key_id'],
        postgresql_where=sa.text('revoked_at IS NULL'),
    )
    op.create_check_constraint(
        'ck_service_credential_revoked_after_created',
        'service_credentials',
        'revoked_at IS NULL OR revoked_at >= created_at',
    )
    op.create_check_constraint(
        'ck_service_credential_scopes_non_empty',
        'service_credentials',
        'array_length(scopes, 1) > 0',
    )
    op.create_check_constraint(
        'ck_service_credential_key_hash_length',
        'service_credentials',
        # SHA-256 hex is exactly 64 characters. Catching a wrong-length
        # hash at write time beats debugging why every SvcKey comparison
        # silently failed.
        'char_length(key_hash) = 64',
    )

    op.create_table(
        'admin_audit_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('actor_type', sa.String(length=32), nullable=False),
        sa.Column('actor_id', sa.String(length=100), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.String(length=100), nullable=True),
        sa.Column('success', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column(
            'timestamp',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('client_ip', sa.String(length=64), nullable=True),
        sa.Column('detail', JSONB(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_admin_audit_timestamp', 'admin_audit_log', [sa.text('timestamp DESC')])
    op.create_index(
        'ix_admin_audit_action_timestamp',
        'admin_audit_log',
        ['action', sa.text('timestamp DESC')],
    )
    op.create_index('ix_admin_audit_actor', 'admin_audit_log', ['actor_type', 'actor_id'])
    op.create_check_constraint(
        'ck_admin_audit_actor_type',
        'admin_audit_log',
        "actor_type IN ('admin_user', 'service_credential', 'anonymous')",
    )
    op.create_check_constraint(
        'ck_admin_audit_actor_id_present',
        'admin_audit_log',
        # Only an anonymous row (no valid credential presented) may omit
        # the actor. Everything else must name someone.
        "actor_type = 'anonymous' OR actor_id IS NOT NULL",
    )


def downgrade() -> None:
    # Indexes first: dropping the table would take them with it, but being
    # explicit keeps this mirror-image readable and matches the ordering
    # used by d4e5f6a7b8c9.
    op.drop_index('ix_admin_audit_actor', table_name='admin_audit_log')
    op.drop_index('ix_admin_audit_action_timestamp', table_name='admin_audit_log')
    op.drop_index('ix_admin_audit_timestamp', table_name='admin_audit_log')
    op.drop_constraint('ck_admin_audit_actor_id_present', 'admin_audit_log', type_='check')
    op.drop_constraint('ck_admin_audit_actor_type', 'admin_audit_log', type_='check')
    op.drop_table('admin_audit_log')

    op.drop_constraint('ck_service_credential_key_hash_length', 'service_credentials', type_='check')
    op.drop_constraint('ck_service_credential_scopes_non_empty', 'service_credentials', type_='check')
    op.drop_constraint('ck_service_credential_revoked_after_created', 'service_credentials', type_='check')
    op.drop_index('ix_service_credential_active', table_name='service_credentials')
    op.drop_index('ix_service_credentials_name', table_name='service_credentials')
    op.drop_index('ix_service_credentials_key_id', table_name='service_credentials')
    op.drop_table('service_credentials')
