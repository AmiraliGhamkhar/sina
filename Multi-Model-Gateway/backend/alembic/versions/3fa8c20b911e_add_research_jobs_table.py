"""add research_jobs table

Revision ID: 3fa8c20b911e
Revises: 7d41aa30c5f2
Create Date: 2026-06-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3fa8c20b911e'
down_revision: Union[str, Sequence[str], None] = '7d41aa30c5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "research_jobs",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_jobs_user_id", "research_jobs", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_research_jobs_user_id", table_name="research_jobs")
    op.drop_table("research_jobs")
