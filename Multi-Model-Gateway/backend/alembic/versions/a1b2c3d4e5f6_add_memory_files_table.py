"""add memory_files table

Per-user memory file store (Claude-style) read/written by the agentic
memory_* tools. Deliberately NOT embeddings — this is a plain file store,
distinct from the pgvector RAG in services/memory.py. Versioned: every
mutating tool takes an if_version and gets a conflict back on a stale write.

Revision ID: a1b2c3d4e5f6
Revises: b83db11a1dc0
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'b83db11a1dc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    if_not_exists: the M1 tests create the table from the model on the dev DB
    (this migration is code-review-only and is not applied), so a later
    `alembic upgrade head` must be a no-op here rather than DuplicateTable.
    """
    op.create_table(
        "memory_files",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("'1'")),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default=sa.text("'0'")),
        sa.Column("sources", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "path", name="uq_memory_files_user_path"),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("memory_files")
