"""add tool_permissions table

Revision ID: 7d41aa30c5f2
Revises: c999d3487714
Create Date: 2026-06-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7d41aa30c5f2'
down_revision: Union[str, Sequence[str], None] = 'c999d3487714'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tool_permissions",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tool_name", name="uq_tool_permissions_user_tool"),
    )
    op.create_index("ix_tool_permissions_user_id", "tool_permissions", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_tool_permissions_user_id", table_name="tool_permissions")
    op.drop_table("tool_permissions")
