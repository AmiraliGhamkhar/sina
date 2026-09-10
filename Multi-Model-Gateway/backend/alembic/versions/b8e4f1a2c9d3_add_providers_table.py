"""add providers table

Revision ID: b8e4f1a2c9d3
Revises: a1f4e7c8d92b
Create Date: 2026-08-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8e4f1a2c9d3'
down_revision: Union[str, Sequence[str], None] = 'a1f4e7c8d92b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(length=128), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_providers_user_name"),
    )
    op.create_index("ix_providers_user_id", "providers", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_providers_user_id", table_name="providers")
    op.drop_table("providers")
