"""add commit_sha to file_edits (deterministic workspace undo)

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-08-18

Adds file_edits.commit_sha TEXT NULL — the git commit that holds this edit's
change (ADR-0003). Old rows stay NULL; new writes fill it. See Workspace deepening (#2).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("file_edits", sa.Column("commit_sha", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("file_edits", "commit_sha")
