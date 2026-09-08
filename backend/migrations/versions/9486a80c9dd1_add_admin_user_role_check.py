"""add_admin_user_role_check

Revision ID: 9486a80c9dd1
Revises: f56a50612cd8
Create Date: 2026-09-08 12:54:22.131218

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9486a80c9dd1'
down_revision: Union[str, None] = 'f56a50612cd8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_admin_users_role",
        "admin_users",
        "role IN ('editor', 'admin')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_admin_users_role", "admin_users", type_="check")
